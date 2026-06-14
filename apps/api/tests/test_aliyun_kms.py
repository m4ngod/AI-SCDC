from base64 import b64decode, b64encode
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    AliyunSettings,
)
from ai_company_api.services.aliyun_kms import (
    SdkAliyunKmsClient,
    get_aliyun_kms_client,
)


def test_get_aliyun_kms_client_requires_region() -> None:
    settings = _aliyun_settings(region_id=None)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_REGION_ID",
    ):
        get_aliyun_kms_client(settings=settings)


def test_get_aliyun_kms_client_requires_access_key_id() -> None:
    settings = _aliyun_settings(access_key_id=None)

    with pytest.raises(
        AliyunConfigurationError,
        match="AI_SCDC_ALIYUN_ACCESS_KEY_ID",
    ):
        get_aliyun_kms_client(settings=settings)


def test_get_aliyun_kms_client_requires_access_key_secret_without_leaking_value() -> None:
    settings = _aliyun_settings(access_key_secret=None)

    with pytest.raises(AliyunConfigurationError) as exc_info:
        get_aliyun_kms_client(settings=settings)

    message = str(exc_info.value)
    assert "required secret environment variable" in message
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in message
    assert "ak-secret" not in message


def test_sdk_aliyun_kms_encrypt_encodes_plaintext_and_maps_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_kms_sdk(
        monkeypatch,
        encrypt_ciphertext_blob="kms-ciphertext-1",
    )

    ciphertext = SdkAliyunKmsClient(_aliyun_settings()).encrypt(
        "kms-key-1",
        "secret-value",
    )

    assert ciphertext == "kms-ciphertext-1"
    config = captured["client_config"]
    assert config.region_id == "cn-hangzhou"
    assert config.access_key_id == "ak-id"
    assert config.access_key_secret == "ak-secret"
    request = captured["encrypt_request"]
    assert request.key_id == "kms-key-1"
    decoded_plaintext = b64decode(
        request.plaintext.encode("ascii"),
        validate=True,
    ).decode("utf-8")
    assert decoded_plaintext == "secret-value"


def test_sdk_aliyun_kms_decrypt_decodes_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_kms_sdk(
        monkeypatch,
        decrypt_plaintext_blob=b64encode("opened-secret".encode("utf-8")).decode(
            "ascii"
        ),
    )

    plaintext = SdkAliyunKmsClient(_aliyun_settings()).decrypt(
        "kms-key-1",
        "kms-ciphertext-1",
    )

    assert plaintext == "opened-secret"
    request = captured["decrypt_request"]
    assert request.ciphertext_blob == "kms-ciphertext-1"


def test_sdk_aliyun_kms_encrypt_rejects_empty_ciphertext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kms_sdk(monkeypatch, encrypt_ciphertext_blob="")

    with pytest.raises(ValueError, match="Invalid KMS encrypt response"):
        SdkAliyunKmsClient(_aliyun_settings()).encrypt("kms-key-1", "secret-value")


def test_sdk_aliyun_kms_decrypt_rejects_empty_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kms_sdk(monkeypatch, decrypt_plaintext_blob="")

    with pytest.raises(ValueError, match="Invalid KMS decrypt response"):
        SdkAliyunKmsClient(_aliyun_settings()).decrypt(
            "kms-key-1",
            "kms-ciphertext-1",
        )


def test_sdk_aliyun_kms_decrypt_rejects_malformed_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_kms_sdk(monkeypatch, decrypt_plaintext_blob="not base64")

    with pytest.raises(ValueError, match="Invalid KMS decrypt response"):
        SdkAliyunKmsClient(_aliyun_settings()).decrypt(
            "kms-key-1",
            "kms-ciphertext-1",
        )


def test_sdk_aliyun_kms_delete_is_noop_without_sdk_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_kms_sdk(monkeypatch)

    SdkAliyunKmsClient(_aliyun_settings()).delete("kms-key-1", "kms-ciphertext-1")

    assert "client_config" not in captured
    assert "encrypt_request" not in captured
    assert "decrypt_request" not in captured


def _install_fake_kms_sdk(
    monkeypatch: pytest.MonkeyPatch,
    *,
    encrypt_ciphertext_blob: str = "kms-ciphertext-1",
    decrypt_plaintext_blob: str | None = None,
) -> dict[str, object]:
    if decrypt_plaintext_blob is None:
        decrypt_plaintext_blob = b64encode("secret-value".encode("utf-8")).decode(
            "ascii"
        )
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(
            self,
            *,
            access_key_id: str,
            access_key_secret: str,
            region_id: str,
        ) -> None:
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.region_id = region_id

    class FakeEncryptRequest:
        def __init__(self, *, key_id: str, plaintext: str) -> None:
            self.key_id = key_id
            self.plaintext = plaintext

    class FakeDecryptRequest:
        def __init__(self, *, ciphertext_blob: str) -> None:
            self.ciphertext_blob = ciphertext_blob

    class FakeClient:
        def __init__(self, config: FakeConfig) -> None:
            captured["client_config"] = config

        def encrypt(self, request: FakeEncryptRequest):
            captured["encrypt_request"] = request
            body = SimpleNamespace(ciphertext_blob=encrypt_ciphertext_blob)
            return SimpleNamespace(body=body)

        def decrypt(self, request: FakeDecryptRequest):
            captured["decrypt_request"] = request
            body = SimpleNamespace(plaintext=decrypt_plaintext_blob)
            return SimpleNamespace(body=body)

    kms_package = ModuleType("alibabacloud_kms20160120")
    kms_client_module = ModuleType("alibabacloud_kms20160120.client")
    kms_models_module = ModuleType("alibabacloud_kms20160120.models")
    kms_client_module.Client = FakeClient
    kms_models_module.EncryptRequest = FakeEncryptRequest
    kms_models_module.DecryptRequest = FakeDecryptRequest
    kms_package.models = kms_models_module

    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_models_module = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models_module.Config = FakeConfig
    openapi_package.models = openapi_models_module

    monkeypatch.setitem(sys.modules, "alibabacloud_kms20160120", kms_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_kms20160120.client",
        kms_client_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_kms20160120.models",
        kms_models_module,
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi.models",
        openapi_models_module,
    )
    return captured


def _aliyun_settings(
    *,
    region_id: str | None = "cn-hangzhou",
    access_key_id: str | None = "ak-id",
    access_key_secret: str | None = "ak-secret",
) -> AliyunSettings:
    return AliyunSettings(
        region_id=region_id,
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        mns_endpoint="https://123456.mns.cn-hangzhou.aliyuncs.com",
        mns_queue_name="ai-scdc-cloud-runs-dev",
        oss_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        oss_bucket="ai-scdc-dev-artifacts",
        eci_vswitch_id="vsw-demo",
        eci_security_group_id="sg-demo",
        eci_image="registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
        api_public_base_url="https://api.example.test",
    )
