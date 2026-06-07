from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import SecretAccessAuditLog
from ai_company_api.services.secret_access_audit import open_secret
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_PROVIDER_ENV,
    DevSecretVault,
    KmsSecretVault,
    SealedSecret,
    SecretVaultConfigurationError,
    get_secret_vault,
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


def test_secret_vault_factory_uses_kms_placeholder_without_dev_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_secret_vault_for_tests(None)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")

    vault = get_secret_vault()

    assert isinstance(vault, KmsSecretVault)
    assert not isinstance(vault, DevSecretVault)
    with pytest.raises(SecretVaultConfigurationError):
        vault.seal("sk-production-secret")


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
