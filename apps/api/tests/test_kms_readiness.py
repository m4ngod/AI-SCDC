import json

import pytest

from ai_company_api.services.kms_readiness import (
    KmsReadinessCheck,
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
    def __init__(self) -> None:
        self.encrypt_requests: list[tuple[str, str]] = []
        self.decrypt_requests: list[tuple[str, str]] = []
        self.delete_requests: list[tuple[str, str]] = []

    def encrypt(self, key_id: str, plaintext: str) -> str:
        self.encrypt_requests.append((key_id, plaintext))
        return f"ciphertext-for-{key_id}"

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        self.decrypt_requests.append((key_id, ciphertext))
        return "opened-secret"

    def delete(self, key_id: str, ciphertext: str) -> None:
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
    ):
        monkeypatch.delenv(name, raising=False)
    set_secret_vault_for_tests(None)
    set_kms_client_for_tests(None)


def result_json(result) -> str:
    return json.dumps(result.model_dump(), sort_keys=True)


def test_kms_readiness_exposes_preflight_api_surface() -> None:
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
