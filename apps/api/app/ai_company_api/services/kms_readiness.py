from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import os
import secrets
from typing import Literal

from pydantic import BaseModel, Field

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    load_aliyun_settings,
    require_aliyun_settings,
)
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    SecretVault,
    SecretVaultConfigurationError,
    get_secret_vault,
)


KMS_READY_STATUS = "ready_for_live_smoke"
KMS_PASSED_STATUS = "passed"
KMS_FAILED_STATUS = "failed"
_KMS_PROVIDERS = {"kms", "aliyun_kms"}
_ALIYUN_KMS_REQUIRED_NAMES = (
    "access_key_id",
    "access_key_secret",
    "region_id",
)
_SECRET_ENV_NAMES = (
    "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
)


class KmsReadinessCheck(BaseModel):
    name: str
    status: Literal["passed", "failed", "skipped"]
    message: str | None = None


class KmsReadinessResult(BaseModel):
    status: Literal["ready_for_live_smoke", "passed", "failed"]
    stage: Literal["preflight", "live_smoke"]
    provider: str | None = None
    key_id_hint: str | None = None
    fingerprint_hint: str | None = None
    checks: list[KmsReadinessCheck] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None

    def exit_code(self) -> int:
        return 1 if self.status == KMS_FAILED_STATUS else 0


def run_kms_readiness(
    *,
    live: bool = False,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
    secret_factory: Callable[[], str] | None = None,
) -> KmsReadinessResult:
    if live:
        return run_kms_live_smoke(
            vault_factory=vault_factory,
            secret_factory=secret_factory,
        )
    return run_kms_preflight(vault_factory=vault_factory)


def run_kms_preflight(
    *,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
) -> KmsReadinessResult:
    provider = _configured_provider()
    key_id = _configured_key_id()
    checks: list[KmsReadinessCheck] = []

    if provider not in _KMS_PROVIDERS:
        checks.append(
            KmsReadinessCheck(
                name="provider",
                status="failed",
                message=(
                    "KMS readiness requires AI_SCDC_SECRET_VAULT_PROVIDER to be kms "
                    "or aliyun_kms."
                ),
            )
        )
        return _failure(
            stage="preflight",
            provider=provider,
            key_id=key_id,
            checks=checks,
            error_code="configuration_error",
            message=checks[-1].message,
        )
    checks.append(KmsReadinessCheck(name="provider", status="passed"))

    if key_id == "":
        message = (
            f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
        )
        checks.append(KmsReadinessCheck(name="key_id", status="failed", message=message))
        return _failure(
            stage="preflight",
            provider=provider,
            key_id=key_id,
            checks=checks,
            error_code="configuration_error",
            message=message,
        )
    checks.append(KmsReadinessCheck(name="key_id", status="passed"))

    try:
        if provider == "aliyun_kms":
            require_aliyun_settings(
                provider_name="kms",
                required_names=_ALIYUN_KMS_REQUIRED_NAMES,
                settings=load_aliyun_settings(),
            )
        else:
            vault_factory()
    except (AliyunConfigurationError, SecretVaultConfigurationError) as exc:
        checks.append(
            KmsReadinessCheck(
                name="configuration",
                status="failed",
                message=_redact_message(str(exc), key_id=key_id),
            )
        )
        return _failure(
            stage="preflight",
            provider=provider,
            key_id=key_id,
            checks=checks,
            error_code="configuration_error",
            message=checks[-1].message,
        )

    checks.append(KmsReadinessCheck(name="configuration", status="passed"))
    return KmsReadinessResult(
        status=KMS_READY_STATUS,
        stage="preflight",
        provider=provider,
        key_id_hint=_key_id_hint(key_id),
        checks=checks,
    )


def run_kms_live_smoke(
    *,
    vault_factory: Callable[[], SecretVault] = get_secret_vault,
    secret_factory: Callable[[], str] | None = None,
) -> KmsReadinessResult:
    _ = secret_factory
    preflight = run_kms_preflight(vault_factory=vault_factory)
    if preflight.status == KMS_FAILED_STATUS:
        return preflight
    return KmsReadinessResult(
        status=KMS_FAILED_STATUS,
        stage="live_smoke",
        provider=preflight.provider,
        key_id_hint=preflight.key_id_hint,
        checks=[
            *preflight.checks,
            KmsReadinessCheck(
                name="live_smoke",
                status="failed",
                message="Live KMS smoke is guarded until Task 2.",
            ),
        ],
        error_code="kms_error",
        message="Live KMS smoke is guarded until Task 2.",
    )


def _configured_provider() -> str:
    return os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()


def _configured_key_id() -> str:
    return os.getenv(SECRET_VAULT_KMS_KEY_ID_ENV, "").strip()


def _generated_secret() -> str:
    return f"ai-scdc-kms-smoke-{secrets.token_urlsafe(24)}"


def _key_id_hint(key_id: str) -> str | None:
    if key_id == "":
        return None
    return f"sha256:{sha256(key_id.encode('utf-8')).hexdigest()[:12]}"


def _fingerprint_hint(fingerprint: str | None) -> str | None:
    if not fingerprint:
        return None
    return fingerprint[:19]


def _failure(
    *,
    stage: Literal["preflight", "live_smoke"],
    provider: str | None,
    key_id: str,
    checks: list[KmsReadinessCheck],
    error_code: str,
    message: str | None,
) -> KmsReadinessResult:
    return KmsReadinessResult(
        status=KMS_FAILED_STATUS,
        stage=stage,
        provider=provider,
        key_id_hint=_key_id_hint(key_id),
        checks=checks,
        error_code=error_code,
        message=_redact_message(message or error_code, key_id=key_id),
    )


def _redact_message(
    message: str,
    *,
    key_id: str,
    extra_sensitive_values: list[str] | None = None,
) -> str:
    redacted = message
    for env_name in _SECRET_ENV_NAMES:
        value = os.getenv(env_name, "").strip()
        if value:
            redacted = redacted.replace(value, "[redacted]")
    if key_id:
        redacted = redacted.replace(key_id, _key_id_hint(key_id) or "[redacted-key]")
    for value in extra_sensitive_values or []:
        if value:
            redacted = redacted.replace(value, "[redacted]")
    return redacted
