from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import os


class AliyunConfigurationError(Exception):
    pass


_ENV_BY_FIELD = {
    "region_id": "AI_SCDC_ALIYUN_REGION_ID",
    "access_key_id": "AI_SCDC_ALIYUN_ACCESS_KEY_ID",
    "access_key_secret": "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET",
    "mns_endpoint": "AI_SCDC_ALIYUN_MNS_ENDPOINT",
    "mns_queue_name": "AI_SCDC_ALIYUN_MNS_QUEUE_NAME",
    "oss_endpoint": "AI_SCDC_ALIYUN_OSS_ENDPOINT",
    "oss_bucket": "AI_SCDC_ALIYUN_OSS_BUCKET",
    "eci_vswitch_id": "AI_SCDC_ALIYUN_ECI_VSWITCH_ID",
    "eci_security_group_id": "AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID",
    "eci_image": "AI_SCDC_ALIYUN_ECI_IMAGE",
    "api_public_base_url": "AI_SCDC_API_PUBLIC_BASE_URL",
}

_SECRET_FIELDS = {"access_key_secret"}


@dataclass(frozen=True)
class AliyunSettings:
    region_id: str | None
    access_key_id: str | None
    access_key_secret: str | None = field(repr=False)
    mns_endpoint: str | None
    mns_queue_name: str | None
    oss_endpoint: str | None
    oss_bucket: str | None
    eci_vswitch_id: str | None
    eci_security_group_id: str | None
    eci_image: str | None
    api_public_base_url: str | None
    eci_cpu: float = 1.0
    eci_memory_gb: float = 2.0
    eci_auto_create_eip: bool = False
    eci_eip_bandwidth: int = 1
    eci_container_group_prefix: str = "ai-scdc-run"
    oss_prefix: str = "ai-scdc/dev"


def load_aliyun_settings() -> AliyunSettings:
    return AliyunSettings(
        region_id=_env("AI_SCDC_ALIYUN_REGION_ID"),
        access_key_id=_env("AI_SCDC_ALIYUN_ACCESS_KEY_ID"),
        access_key_secret=_env("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET"),
        mns_endpoint=_env("AI_SCDC_ALIYUN_MNS_ENDPOINT"),
        mns_queue_name=_env("AI_SCDC_ALIYUN_MNS_QUEUE_NAME"),
        oss_endpoint=_env("AI_SCDC_ALIYUN_OSS_ENDPOINT"),
        oss_bucket=_env("AI_SCDC_ALIYUN_OSS_BUCKET"),
        eci_vswitch_id=_env("AI_SCDC_ALIYUN_ECI_VSWITCH_ID"),
        eci_security_group_id=_env("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID"),
        eci_image=_env("AI_SCDC_ALIYUN_ECI_IMAGE"),
        api_public_base_url=_env("AI_SCDC_API_PUBLIC_BASE_URL"),
        eci_cpu=_float_env("AI_SCDC_ALIYUN_ECI_CPU", 1.0),
        eci_memory_gb=_float_env("AI_SCDC_ALIYUN_ECI_MEMORY_GB", 2.0),
        eci_auto_create_eip=_bool_env("AI_SCDC_ALIYUN_ECI_AUTO_CREATE_EIP", False),
        eci_eip_bandwidth=_int_env("AI_SCDC_ALIYUN_ECI_EIP_BANDWIDTH", 1),
        eci_container_group_prefix=_env(
            "AI_SCDC_ALIYUN_ECI_CONTAINER_GROUP_PREFIX"
        )
        or "ai-scdc-run",
        oss_prefix=_env("AI_SCDC_ALIYUN_OSS_PREFIX") or "ai-scdc/dev",
    )


def require_aliyun_settings(
    *,
    provider_name: str,
    required_names: Sequence[str],
    settings: AliyunSettings | None = None,
) -> AliyunSettings:
    resolved = settings or load_aliyun_settings()
    missing = [
        name
        for name in required_names
        if not getattr(resolved, name)
    ]
    if missing:
        safe_names = [
            _ENV_BY_FIELD[name]
            for name in missing
            if name not in _SECRET_FIELDS
        ]
        if any(name in _SECRET_FIELDS for name in missing):
            safe_names.append("required secret environment variable")
        joined = ", ".join(safe_names)
        raise AliyunConfigurationError(
            f"Aliyun provider {provider_name} is missing configuration: {joined}"
        )
    return resolved


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _float_env(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    return float(value)


def _int_env(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    return int(value)


def _bool_env(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}
