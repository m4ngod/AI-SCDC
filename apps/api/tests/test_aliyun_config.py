import pytest

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    load_aliyun_settings,
    require_aliyun_settings,
)


def test_load_aliyun_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_MNS_ENDPOINT",
        "https://123456.mns.cn-hangzhou.aliyuncs.com",
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_QUEUE_NAME", "ai-scdc-cloud-runs-dev")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_VSWITCH_ID", "vsw-demo")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID", "sg-demo")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ECI_IMAGE",
        "registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
    )
    monkeypatch.setenv("AI_SCDC_API_PUBLIC_BASE_URL", "https://api.example.test")

    settings = load_aliyun_settings()

    assert settings.region_id == "cn-hangzhou"
    assert settings.access_key_id == "ak-id"
    assert settings.access_key_secret == "ak-secret"
    assert settings.mns_queue_name == "ai-scdc-cloud-runs-dev"
    assert settings.oss_bucket == "ai-scdc-dev-artifacts"
    assert settings.eci_cpu == 1.0
    assert settings.eci_memory_gb == 2.0
    assert settings.eci_container_group_prefix == "ai-scdc-run"
    assert settings.oss_prefix == "ai-scdc/dev"


def test_aliyun_settings_repr_does_not_include_secret_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "super-secret-value")

    settings_repr = repr(load_aliyun_settings())

    assert "super-secret-value" not in settings_repr


def test_require_aliyun_settings_reports_missing_names_without_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "super-secret-value")

    with pytest.raises(AliyunConfigurationError) as exc_info:
        require_aliyun_settings(
            provider_name="aliyun_oss",
            required_names=("region_id", "access_key_id", "access_key_secret", "oss_bucket"),
        )

    message = str(exc_info.value)
    assert "Aliyun provider aliyun_oss is missing configuration" in message
    assert "AI_SCDC_ALIYUN_REGION_ID" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_ID" in message
    assert "AI_SCDC_ALIYUN_OSS_BUCKET" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in message
    assert "super-secret-value" not in message
