import json

import pytest

from ai_company_api.services.kms_readiness import (
    KmsReadinessCheck,
    run_kms_live_smoke,
    run_kms_readiness,
    run_kms_preflight,
)
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    set_kms_client_for_tests,
    set_secret_vault_for_tests,
)


class FakeKmsClient:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        failure_message: str = "KMS encrypt failed for ak-secret-value",
        mismatch_open: bool = False,
    ) -> None:
        self.fail_on = fail_on
        self.failure_message = failure_message
        self.mismatch_open = mismatch_open
        self.encrypt_requests: list[tuple[str, str]] = []
        self.decrypt_requests: list[tuple[str, str]] = []
        self.delete_requests: list[tuple[str, str]] = []

    def encrypt(self, key_id: str, plaintext: str) -> str:
        if self.fail_on == "encrypt":
            raise RuntimeError(self.failure_message)
        self.encrypt_requests.append((key_id, plaintext))
        return f"fake-kms:{key_id}:{plaintext}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        if self.fail_on == "decrypt":
            raise RuntimeError("KMS decrypt failed for ak-secret-value")
        self.decrypt_requests.append((key_id, ciphertext))
        if self.mismatch_open:
            return "different-opened-secret"
        return ciphertext.removeprefix(f"fake-kms:{key_id}:")

    def delete(self, key_id: str, ciphertext: str) -> None:
        if self.fail_on == "delete":
            raise RuntimeError("KMS delete failed for ak-secret-value")
        self.delete_requests.append((key_id, ciphertext))


def set_complete_aliyun_kms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "aliyun_kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "kms-key-production")
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret-value")


def clear_kms_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        SECRET_VAULT_PROVIDER_ENV,
        SECRET_VAULT_KMS_KEY_ID_ENV,
        "AI_SCDC_ALIYUN_REGION_ID",
        "AI_SCDC_ALIYUN_ACCESS_KEY_ID",
        "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
        "AI_SCDC_ALIYUN_ECI_CPU",
    ):
        monkeypatch.delenv(name, raising=False)
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)


def result_json(result) -> str:
    return json.dumps(result.model_dump(), sort_keys=True)


def test_kms_readiness_preflight_cli_outputs_redacted_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_company_api.tools import kms_readiness as cli

    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)

    exit_code = cli.main([])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ready_for_live_smoke"
    assert payload["stage"] == "preflight"
    assert "kms-key-production" not in captured.out


def test_kms_readiness_live_cli_outputs_redacted_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_company_api.tools import kms_readiness as cli

    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        exit_code = cli.main(
            ["--live"],
            secret_factory=lambda: "temporary-smoke-secret",
        )
    finally:
        set_kms_client_for_tests(None)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "passed"
    assert payload["stage"] == "live_smoke"
    assert "temporary-smoke-secret" not in captured.out


def test_kms_readiness_cli_exits_one_for_failed_readiness(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_company_api.tools import kms_readiness as cli

    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "dev")

    exit_code = cli.main([])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "failed"
    assert payload["error_code"] == "configuration_error"


def test_kms_readiness_exposes_preflight_api_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)

    result = run_kms_readiness(
        live=False,
        vault_factory=lambda: pytest.fail("dev provider should fail first"),
    )

    check = KmsReadinessCheck(name="provider", status="skipped")

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.exit_code() == 1
    assert check.name == "provider"


def test_kms_preflight_succeeds_for_complete_aliyun_config_without_kms_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_preflight()
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)
    assert result.status == "ready_for_live_smoke"
    assert result.stage == "preflight"
    assert result.provider == "aliyun_kms"
    assert result.error_code is None
    assert result.key_id_hint.startswith("sha256:")
    assert "kms-key-production" not in serialized
    assert "ak-secret-value" not in serialized
    assert fake_kms.encrypt_requests == []
    assert fake_kms.decrypt_requests == []
    assert fake_kms.delete_requests == []


def test_kms_preflight_ignores_malformed_unrelated_aliyun_optional_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_CPU", "not-a-float")
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_preflight()
    finally:
        set_kms_client_for_tests(None)

    assert result.status == "ready_for_live_smoke"
    assert result.stage == "preflight"
    assert result.provider == "aliyun_kms"
    assert fake_kms.encrypt_requests == []
    assert fake_kms.decrypt_requests == []
    assert fake_kms.delete_requests == []


def test_kms_preflight_fails_when_key_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv(SECRET_VAULT_KMS_KEY_ID_ENV, raising=False)

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert SECRET_VAULT_KMS_KEY_ID_ENV in (result.message or "")


def test_kms_preflight_fails_when_region_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_REGION_ID", raising=False)

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert "AI_SCDC_ALIYUN_REGION_ID" in (result.message or "")


def test_kms_preflight_fails_when_access_key_id_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", raising=False)

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_ID" in (result.message or "")


def test_kms_preflight_fails_when_access_key_secret_is_missing_without_leaking_name_or_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    monkeypatch.delenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", raising=False)

    result = run_kms_preflight()
    serialized = result_json(result)

    assert result.status == "failed"
    assert result.error_code == "configuration_error"
    assert "required secret environment variable" in (result.message or "")
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in serialized
    assert "ak-secret-value" not in serialized


def test_kms_preflight_rejects_dev_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "dev")

    result = run_kms_preflight()

    assert result.status == "failed"
    assert result.error_code == "configuration_error"
    assert "aliyun_kms" in (result.message or "")


def test_kms_preflight_allows_generic_kms_only_with_configured_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "generic-key")
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_preflight()
    finally:
        set_kms_client_for_tests(None)

    assert result.status == "ready_for_live_smoke"
    assert result.provider == "kms"
    assert fake_kms.encrypt_requests == []
    assert fake_kms.decrypt_requests == []
    assert fake_kms.delete_requests == []


def test_kms_preflight_fails_closed_for_generic_kms_without_client_without_key_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    monkeypatch.setenv(SECRET_VAULT_PROVIDER_ENV, "kms")
    monkeypatch.setenv(SECRET_VAULT_KMS_KEY_ID_ENV, "generic-key-without-client")

    result = run_kms_preflight()
    serialized = result_json(result)

    assert result.status == "failed"
    assert result.stage == "preflight"
    assert result.error_code == "configuration_error"
    assert "generic-key-without-client" not in serialized


def test_kms_live_smoke_succeeds_through_secret_vault_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient()
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(
            secret_factory=lambda: "temporary-smoke-secret"
        )
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)
    ciphertext = "fake-kms:kms-key-production:temporary-smoke-secret"

    assert result.status == "passed"
    assert result.stage == "live_smoke"
    assert result.provider == "aliyun_kms"
    assert result.error_code is None
    assert result.key_id_hint.startswith("sha256:")
    assert result.fingerprint_hint.startswith("sha256:")
    assert [check.name for check in result.checks] == [
        "provider",
        "key_id",
        "configuration",
        "seal",
        "open",
        "fingerprint",
        "delete",
    ]
    assert {check.status for check in result.checks} == {"passed"}
    assert "temporary-smoke-secret" not in serialized
    assert ciphertext not in serialized
    assert "kms-key-production" not in serialized
    assert "ak-secret-value" not in serialized
    assert fake_kms.encrypt_requests == [
        ("kms-key-production", "temporary-smoke-secret")
    ]
    assert fake_kms.decrypt_requests == [
        ("kms-key-production", ciphertext),
        ("kms-key-production", ciphertext),
    ]
    assert fake_kms.delete_requests == [("kms-key-production", ciphertext)]


def test_kms_live_smoke_reports_kms_encrypt_failure_without_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient(fail_on="encrypt")
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(
            secret_factory=lambda: "temporary-smoke-secret"
        )
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)

    assert result.status == "failed"
    assert result.stage == "live_smoke"
    assert result.error_code == "kms_error"
    assert "ak-secret-value" not in serialized
    assert "temporary-smoke-secret" not in serialized
    assert fake_kms.encrypt_requests == []
    assert fake_kms.decrypt_requests == []
    assert fake_kms.delete_requests == []


def test_kms_live_smoke_redacts_access_key_id_from_kms_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient(
        fail_on="encrypt",
        failure_message="KMS encrypt failed for ak-id and ak-secret-value",
    )
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(
            secret_factory=lambda: "temporary-smoke-secret"
        )
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)

    assert result.status == "failed"
    assert result.stage == "live_smoke"
    assert result.error_code == "kms_error"
    assert "ak-id" not in serialized
    assert "ak-secret-value" not in serialized
    assert "temporary-smoke-secret" not in serialized


def test_kms_live_smoke_reports_roundtrip_mismatch_without_opened_secret_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_kms_env(monkeypatch)
    set_complete_aliyun_kms_env(monkeypatch)
    fake_kms = FakeKmsClient(mismatch_open=True)
    set_kms_client_for_tests(fake_kms)
    try:
        result = run_kms_live_smoke(
            secret_factory=lambda: "temporary-smoke-secret"
        )
    finally:
        set_kms_client_for_tests(None)

    serialized = result_json(result)

    assert result.status == "failed"
    assert result.stage == "live_smoke"
    assert result.error_code == "roundtrip_mismatch"
    assert "temporary-smoke-secret" not in serialized
    assert "different-opened-secret" not in serialized
