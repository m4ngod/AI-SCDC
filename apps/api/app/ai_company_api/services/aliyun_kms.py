from __future__ import annotations

from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from dataclasses import dataclass

from ai_company_api.services.aliyun_config import (
    AliyunSettings,
    load_aliyun_settings,
    require_aliyun_settings,
)


_REQUIRED_KMS_SETTING_NAMES = (
    "access_key_id",
    "access_key_secret",
    "region_id",
)


@dataclass(frozen=True)
class SdkAliyunKmsClient:
    settings: AliyunSettings

    def encrypt(self, key_id: str, plaintext: str) -> str:
        from alibabacloud_kms20160120 import models as kms_models

        plaintext_blob = b64encode(plaintext.encode("utf-8")).decode("ascii")
        result = self._client().encrypt(
            kms_models.EncryptRequest(
                key_id=key_id,
                plaintext=plaintext_blob,
            )
        )
        ciphertext = str(getattr(getattr(result, "body", None), "ciphertext_blob", "") or "")
        if ciphertext == "":
            raise ValueError("Invalid KMS encrypt response")
        return ciphertext

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        from alibabacloud_kms20160120 import models as kms_models

        result = self._client().decrypt(
            kms_models.DecryptRequest(ciphertext_blob=ciphertext)
        )
        plaintext_blob = str(getattr(getattr(result, "body", None), "plaintext", "") or "")
        if plaintext_blob == "":
            raise ValueError("Invalid KMS decrypt response")
        try:
            return b64decode(
                plaintext_blob.encode("ascii"),
                validate=True,
            ).decode("utf-8")
        except (BinasciiError, UnicodeEncodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid KMS decrypt response") from exc

    def delete(self, key_id: str, ciphertext: str) -> None:
        _ = (key_id, ciphertext)

    def _client(self):
        from alibabacloud_kms20160120.client import Client
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="kms",
            required_names=_REQUIRED_KMS_SETTING_NAMES,
            settings=self.settings,
        )
        return Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=settings.region_id,
            )
        )


def get_aliyun_kms_client(
    settings: AliyunSettings | None = None,
) -> SdkAliyunKmsClient:
    resolved = require_aliyun_settings(
        provider_name="kms",
        required_names=_REQUIRED_KMS_SETTING_NAMES,
        settings=settings or load_aliyun_settings(),
    )
    return SdkAliyunKmsClient(resolved)
