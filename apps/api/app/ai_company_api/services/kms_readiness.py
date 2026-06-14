from __future__ import annotations

from hashlib import sha256
import os
from typing import Literal

from pydantic import BaseModel

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    require_aliyun_settings,
)
from ai_company_api.services.secret_vault import (
    SECRET_VAULT_KMS_KEY_ID_ENV,
    SECRET_VAULT_PROVIDER_ENV,
    SecretVaultConfigurationError,
    get_secret_vault,
)


_REQUIRED_ALIYUN_KMS_SETTING_NAMES = (
    "access_key_id",
    "access_key_secret",
    "region_id",
)


class KmsReadinessResult(BaseModel):
    status: Literal["ready_for_live_smoke", "failed"]
    stage: Literal["preflight", "live_smoke"]
    provider: str | None = None
    key_id_hint: str | None = None
    error_code: str | None = None
    message: str | None = None


def run_kms_preflight() -> KmsReadinessResult:
    provider = os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()
    key_id = os.getenv(SECRET_VAULT_KMS_KEY_ID_ENV, "").strip()

    try:
        if provider not in {"kms", "aliyun_kms"}:
            raise SecretVaultConfigurationError(
                f"{SECRET_VAULT_PROVIDER_ENV} must be configured as aliyun_kms "
                "before running KMS readiness checks"
            )
        if provider == "aliyun_kms":
            require_aliyun_settings(
                provider_name="kms",
                required_names=_REQUIRED_ALIYUN_KMS_SETTING_NAMES,
            )
        get_secret_vault()
    except (AliyunConfigurationError, SecretVaultConfigurationError) as exc:
        return KmsReadinessResult(
            status="failed",
            stage="preflight",
            provider=provider or None,
            key_id_hint=_key_id_hint(key_id),
            error_code="configuration_error",
            message=str(exc),
        )

    return KmsReadinessResult(
        status="ready_for_live_smoke",
        stage="preflight",
        provider=provider,
        key_id_hint=_key_id_hint(key_id),
    )


def run_kms_live_smoke() -> KmsReadinessResult:
    return KmsReadinessResult(
        status="failed",
        stage="live_smoke",
        error_code="not_implemented",
        message="KMS live smoke is not implemented in the preflight task.",
    )


def _key_id_hint(key_id: str) -> str | None:
    if key_id == "":
        return None
    return f"sha256:{sha256(key_id.encode('utf-8')).hexdigest()}"
