from base64 import b64decode, urlsafe_b64encode
import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    GitHubCredential,
    ModelCredential,
    SecretAccessAuditLog,
)
from ai_company_api.services.aliyun_config import AliyunConfigurationError
from ai_company_api.services.aliyun_kms import SdkAliyunKmsClient
from ai_company_api.services.secret_access_audit import open_secret
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    DevSecretVault,
    KmsSecretVault,
    SealedSecret,
    SecretVaultConfigurationError,
    get_secret_vault,
    set_kms_client_for_tests,
    set_secret_vault_for_tests,
)


class FakeSecretVault:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def seal(self, secret_value: str) -> SealedSecret:
        return SealedSecret(
            encrypted_secret=f"fake-vault:{secret_value}",
            secret_last4=secret_value[-4:],
        )

    def open(self, encrypted_secret: str) -> str:
        return encrypted_secret.removeprefix("fake-vault:")

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        self.open(encrypted_secret)
        return self.seal(new_secret_value)

    def delete(self, encrypted_secret: str) -> None:
        self.deleted.append(encrypted_secret)

    def fingerprint(self, encrypted_secret: str) -> str:
        return "fake:" + self.open(encrypted_secret)


class FakeKmsClient:
    def __init__(self) -> None:
        self.encrypt_requests: list[tuple[str, str]] = []
        self.decrypt_requests: list[tuple[str, str]] = []
        self.delete_requests: list[tuple[str, str]] = []

    def encrypt(self, key_id: str, plaintext: str) -> str:
        self.encrypt_requests.append((key_id, plaintext))
        encoded = urlsafe_b64encode(plaintext.encode("utf-8")).decode("ascii")
        return f"fake-kms:{key_id}:{encoded}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        self.decrypt_requests.append((key_id, ciphertext))
        prefix = f"fake-kms:{key_id}:"
        if not ciphertext.startswith(prefix):
            raise ValueError("Fake KMS ciphertext key mismatch")
        encoded = ciphertext.removeprefix(prefix)
        return b64decode(
            encoded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")

    def delete(self, key_id: str, ciphertext: str) -> None:
        prefix = f"fake-kms:{key_id}:"
        if not ciphertext.startswith(prefix):
            raise ValueError("Fake KMS ciphertext key mismatch")
        self.delete_requests.append((key_id, ciphertext))


def kms_test_payload(payload: dict[str, str]) -> str:
    encoded = urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    return f"kms-vault:v1:{encoded}"


def set_complete_aliyun_kms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret")


def build_client(database_path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def test_secret_vault_factory_defaults_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    set_secret_vault_for_tests(None)
    monkeypatch.delenv(SECRET_VAULT_PROVIDER_ENV, raising=False)

    sealed = get_secret_vault().seal("sk-example1234")

    assert sealed.encrypted_secret.startswith("dev-vault:v2:")
    assert sealed.secret_last4 == "1234"


def test_dev_secret_vault_rotates_deletes_and_fingerprints() -> None:
    vault = DevSecretVault()
    sealed = vault.seal("sk-example1234")

    rotated = vault.rotate(sealed.encrypted_secret, "sk-rotated5678")

    assert vault.open(rotated.encrypted_secret) == "sk-rotated5678"
    assert rotated.secret_last4 == "5678"
    assert vault.fingerprint(rotated.encrypted_secret) == (
        "sha256:" + sha256("sk-rotated5678".encode("utf-8")).hexdigest()
    )
    assert vault.delete(rotated.encrypted_secret) is None


def test_secret_vault_factory_fails_closed_for_unconfigured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "unknown")

    with pytest.raises(SecretVaultConfigurationError) as exc_info:
        get_secret_vault()

    assert "unknown" in str(exc_info.value)


def test_secret_vault_factory_fails_closed_for_kms_without_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(FakeKmsClient())
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.delenv(SECRET_VAULT_KMS_KEY_ID_ENV, raising=False)
    try:
        with pytest.raises(SecretVaultConfigurationError) as exc_info:
            get_secret_vault()
    finally:
        set_kms_client_for_tests(None)

    assert SECRET_VAULT_KMS_KEY_ID_ENV in str(exc_info.value)


def test_secret_vault_factory_fails_closed_for_kms_without_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")

    with pytest.raises(SecretVaultConfigurationError) as exc_info:
        get_secret_vault()

    assert "KMS SecretVault provider is not configured" in str(exc_info.value)


@pytest.mark.parametrize("provider", ["kms", "aliyun_kms"])
def test_secret_vault_factory_uses_configured_kms_client(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    fake_kms = FakeKmsClient()
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(fake_kms)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, provider)
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    try:
        vault = get_secret_vault()
        sealed = vault.seal("sk-production-secret")
        opened = vault.open(sealed.encrypted_secret)
    finally:
        set_kms_client_for_tests(None)

    assert isinstance(vault, KmsSecretVault)
    assert not isinstance(vault, DevSecretVault)
    assert sealed.encrypted_secret.startswith("kms-vault:v1:")
    assert sealed.secret_last4 == "cret"
    assert opened == "sk-production-secret"
    assert fake_kms.encrypt_requests == [("key-production", "sk-production-secret")]
    assert fake_kms.decrypt_requests[0][0] == "key-production"


def test_secret_vault_factory_uses_aliyun_kms_sdk_client_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)

    vault = get_secret_vault()

    assert isinstance(vault, KmsSecretVault)
    assert vault.provider == "aliyun_kms"
    assert vault.key_id == "key-production"
    assert isinstance(getattr(vault, "_client"), SdkAliyunKmsClient)


def test_secret_vault_factory_fails_closed_for_aliyun_kms_missing_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_REGION_ID", raising=False)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_REGION_ID",
    ):
        get_secret_vault()


def test_secret_vault_factory_fails_closed_for_aliyun_kms_missing_access_key_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", raising=False)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_ACCESS_KEY_ID",
    ):
        get_secret_vault()


def test_secret_vault_factory_fails_closed_for_aliyun_kms_missing_secret_without_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "key-production")
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", raising=False)

    with pytest.raises(AliyunConfigurationError) as exc_info:
        get_secret_vault()

    message = str(exc_info.value)
    assert "required secret environment variable" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in message
    assert "ak-secret" not in message


def test_kms_secret_vault_rotates_deletes_and_fingerprints() -> None:
    fake_kms = FakeKmsClient()
    vault = KmsSecretVault(
        client=fake_kms,
        key_id="key-a",
        provider="aliyun_kms",
    )
    sealed = vault.seal("sk-example1234")

    rotated = vault.rotate(sealed.encrypted_secret, "sk-rotated5678")

    assert sealed.encrypted_secret.startswith("kms-vault:v1:")
    assert "sk-example1234" not in sealed.encrypted_secret
    assert vault.open(rotated.encrypted_secret) == "sk-rotated5678"
    assert rotated.secret_last4 == "5678"
    assert vault.fingerprint(rotated.encrypted_secret) == (
        "sha256:" + sha256("sk-rotated5678".encode("utf-8")).hexdigest()
    )
    assert vault.delete(rotated.encrypted_secret) is None
    assert fake_kms.encrypt_requests == [
        ("key-a", "sk-example1234"),
        ("key-a", "sk-rotated5678"),
    ]
    assert fake_kms.delete_requests[-1][0] == "key-a"
    assert fake_kms.delete_requests[-1][1].startswith("fake-kms:key-a:")
    assert not fake_kms.delete_requests[-1][1].startswith("kms-vault:v1:")


def test_kms_secret_vault_rejects_extra_envelope_fields() -> None:
    vault = KmsSecretVault(
        client=FakeKmsClient(),
        key_id="key-a",
        provider="aliyun_kms",
    )
    payload = kms_test_payload(
        {
            "provider": "aliyun_kms",
            "key_id": "key-a",
            "ciphertext": "fake-kms:key-a:c2stbGVha2Vk",
            "plaintext": "sk-leaked",
        }
    )

    with pytest.raises(ValueError, match="Invalid KMS vault payload"):
        vault.open(payload)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "dev-vault:v2:c2stdGVzdA==",
        "kms-vault:v1:",
        "kms-vault:v1:not-base64",
        kms_test_payload(
            {
                "provider": "kms",
                "key_id": "key-a",
                "ciphertext": "fake-kms:key-a:c2stdGVzdA==",
            }
        ),
        kms_test_payload(
            {
                "provider": "aliyun_kms",
                "key_id": "key-b",
                "ciphertext": "fake-kms:key-a:c2stdGVzdA==",
            }
        ),
        kms_test_payload(
            {
                "provider": "aliyun_kms",
                "key_id": "key-a",
                "ciphertext": "",
            }
        ),
    ],
)
def test_kms_secret_vault_rejects_invalid_payloads(payload: str) -> None:
    vault = KmsSecretVault(
        client=FakeKmsClient(),
        key_id="key-a",
        provider="aliyun_kms",
    )

    with pytest.raises(ValueError):
        vault.open(payload)


def test_secret_vault_factory_uses_test_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_vault = FakeSecretVault()
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    set_secret_vault_for_tests(fake_vault)
    try:
        assert get_secret_vault() is fake_vault
    finally:
        set_secret_vault_for_tests(None)


def test_open_secret_records_audit_without_secret_payload() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    secret_value = "ghp_secret_value_1234"
    sealed = DevSecretVault().seal(secret_value)

    with Session(engine) as session:
        opened = open_secret(
            session,
            sealed.encrypted_secret,
            secret_kind="github_credential",
            secret_id="github_credential_1",
            access_reason="unit_test",
            workspace_id="workspace_a",
            organization_id="org_a",
            user_id="user_a",
            auth_mode="dev",
            commit_audit=True,
        )
        audit_log = session.exec(select(SecretAccessAuditLog)).one()

    serialized_log = " ".join(str(value) for value in audit_log.model_dump().values())
    assert opened == secret_value
    assert audit_log.workspace_id == "workspace_a"
    assert audit_log.organization_id == "org_a"
    assert audit_log.user_id == "user_a"
    assert audit_log.auth_mode == "dev"
    assert audit_log.secret_kind == "github_credential"
    assert audit_log.secret_id == "github_credential_1"
    assert audit_log.access_reason == "unit_test"
    assert audit_log.success is True
    assert secret_value not in serialized_log
    assert sealed.encrypted_secret not in serialized_log


def test_model_credential_create_and_delete_records_secret_audit(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    secret_value = "sk-secret-audit-1234"

    with build_client(database_path) as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-audit", "provider_type": "deepseek"},
        ).json()
        credential = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "DeepSeek audit key",
                "secret_value": secret_value,
            },
        ).json()
        response = client.delete(f"/model-credentials/{credential['id']}")

    assert response.status_code == 200
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        audit_logs = session.exec(
            select(SecretAccessAuditLog).order_by(SecretAccessAuditLog.created_at)
        ).all()

    assert [
        (log.secret_kind, log.secret_id, log.operation, log.access_reason, log.success)
        for log in audit_logs
    ] == [
        (
            "model_credential",
            credential["id"],
            "create",
            "model_credential_create",
            True,
        ),
        (
            "model_credential",
            credential["id"],
            "delete",
            "model_credential_delete",
            True,
        ),
    ]
    serialized_logs = " ".join(str(log.model_dump()) for log in audit_logs)
    assert secret_value not in serialized_logs


def test_model_credential_delete_uses_configured_secret_vault(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    fake_vault = FakeSecretVault()
    set_secret_vault_for_tests(fake_vault)
    try:
        with build_client(database_path) as client:
            provider = client.post(
                "/model-providers",
                json={"name": "deepseek-delete", "provider_type": "deepseek"},
            ).json()
            credential = client.post(
                "/model-credentials",
                json={
                    "provider_id": provider["id"],
                    "display_name": "DeepSeek delete key",
                    "secret_value": "sk-delete-1234",
                },
            ).json()
            response = client.delete(f"/model-credentials/{credential['id']}")
    finally:
        set_secret_vault_for_tests(None)

    assert response.status_code == 200
    assert fake_vault.deleted == ["fake-vault:sk-delete-1234"]


def test_model_credential_create_uses_configured_kms_secret_vault(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "app.db"
    secret_value = "sk-kms-model-1234"
    fake_kms = FakeKmsClient()
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(fake_kms)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "model-key")
    try:
        with build_client(database_path) as client:
            provider = client.post(
                "/model-providers",
                json={"name": "deepseek-kms", "provider_type": "deepseek"},
            ).json()
            credential = client.post(
                "/model-credentials",
                json={
                    "provider_id": provider["id"],
                    "display_name": "DeepSeek KMS key",
                    "secret_value": secret_value,
                },
            ).json()
    finally:
        set_kms_client_for_tests(None)

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        stored = session.exec(select(ModelCredential)).one()

    assert credential["secret_last4"] == "1234"
    assert stored.encrypted_secret.startswith("kms-vault:v1:")
    assert secret_value not in stored.encrypted_secret
    assert secret_value not in str(credential)
    assert stored.encrypted_secret not in str(credential)
    assert fake_kms.encrypt_requests == [("model-key", secret_value)]


def test_github_credential_create_and_delete_records_secret_audit(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    token = "ghp_secret_audit_1234"

    with build_client(database_path) as client:
        credential = client.post(
            "/github-credentials",
            json={"display_name": "GitHub audit token", "token": token},
        ).json()
        response = client.delete(f"/github-credentials/{credential['id']}")

    assert response.status_code == 200
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        audit_logs = session.exec(
            select(SecretAccessAuditLog).order_by(SecretAccessAuditLog.created_at)
        ).all()

    assert [
        (log.secret_kind, log.secret_id, log.operation, log.access_reason, log.success)
        for log in audit_logs
    ] == [
        (
            "github_credential",
            credential["id"],
            "create",
            "github_credential_create",
            True,
        ),
        (
            "github_credential",
            credential["id"],
            "delete",
            "github_credential_delete",
            True,
        ),
    ]
    serialized_logs = " ".join(str(log.model_dump()) for log in audit_logs)
    assert token not in serialized_logs


def test_github_credential_delete_uses_configured_kms_secret_vault(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "app.db"
    token = "ghp_kms_delete_1234"
    fake_kms = FakeKmsClient()
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(fake_kms)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "github-key")
    try:
        with build_client(database_path) as client:
            credential = client.post(
                "/github-credentials",
                json={"display_name": "GitHub KMS token", "token": token},
            ).json()
            response = client.delete(f"/github-credentials/{credential['id']}")
    finally:
        set_kms_client_for_tests(None)

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        stored = session.exec(select(GitHubCredential)).one()

    assert response.status_code == 200
    assert stored.encrypted_token.startswith("kms-vault:v1:")
    assert token not in stored.encrypted_token
    assert token not in str(response.json())
    assert stored.encrypted_token not in str(response.json())
    assert fake_kms.encrypt_requests == [("github-key", token)]
    assert len(fake_kms.delete_requests) == 1
    assert fake_kms.delete_requests[0][0] == "github-key"
    assert fake_kms.delete_requests[0][1].startswith("fake-kms:github-key:")
    assert not fake_kms.delete_requests[0][1].startswith("kms-vault:v1:")
