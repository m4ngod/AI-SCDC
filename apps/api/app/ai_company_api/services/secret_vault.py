from base64 import b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from hashlib import sha256
import json
from json import JSONDecodeError
import os
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError


class SealedSecret(BaseModel):
    encrypted_secret: str = Field(min_length=1)
    secret_last4: str


class SecretVault(Protocol):
    def seal(self, secret_value: str) -> SealedSecret:
        ...

    def open(self, encrypted_secret: str) -> str:
        ...

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        ...

    def delete(self, encrypted_secret: str) -> None:
        ...

    def fingerprint(self, encrypted_secret: str) -> str:
        ...


class KmsClient(Protocol):
    def encrypt(self, key_id: str, plaintext: str) -> str:
        ...

    def decrypt(self, key_id: str, ciphertext: str) -> str:
        ...

    def delete(self, key_id: str, ciphertext: str) -> None:
        ...


class SecretVaultConfigurationError(RuntimeError):
    pass


class _KmsVaultEnvelope(BaseModel):
    provider: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    ciphertext: str = Field(min_length=1)


class DevSecretVault:
    _prefix = "dev-vault:v2:"

    def seal(self, secret_value: str) -> SealedSecret:
        encoded = urlsafe_b64encode(secret_value.encode("utf-8")).decode("ascii")
        return SealedSecret(
            encrypted_secret=f"{self._prefix}{encoded}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )

    def open(self, encrypted_secret: str) -> str:
        if not encrypted_secret.startswith(self._prefix):
            raise ValueError("Unsupported dev vault payload")
        encoded = encrypted_secret.removeprefix(self._prefix)
        if encoded == "":
            raise ValueError("Invalid dev vault payload")
        try:
            encoded_bytes = encoded.encode("ascii")
            return b64decode(encoded_bytes, altchars=b"-_", validate=True).decode("utf-8")
        except (BinasciiError, UnicodeEncodeError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid dev vault payload") from exc

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        self.open(encrypted_secret)
        return self.seal(new_secret_value)

    def delete(self, encrypted_secret: str) -> None:
        self.open(encrypted_secret)

    def fingerprint(self, encrypted_secret: str) -> str:
        secret = self.open(encrypted_secret)
        return f"sha256:{sha256(secret.encode('utf-8')).hexdigest()}"


class KmsSecretVault:
    _prefix = "kms-vault:v1:"

    def __init__(self, *, client: KmsClient | None, key_id: str, provider: str) -> None:
        if client is None:
            raise SecretVaultConfigurationError(
                "KMS SecretVault provider is not configured"
            )
        normalized_key_id = key_id.strip()
        if normalized_key_id == "":
            raise SecretVaultConfigurationError(
                f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
            )
        normalized_provider = provider.strip().lower()
        if normalized_provider == "":
            raise SecretVaultConfigurationError(
                "KMS SecretVault provider name is required"
            )
        self._client = client
        self.key_id = normalized_key_id
        self.provider = normalized_provider

    def seal(self, secret_value: str) -> SealedSecret:
        ciphertext = self._client.encrypt(self.key_id, secret_value)
        if ciphertext == "":
            raise ValueError("Invalid KMS vault payload")
        envelope = _KmsVaultEnvelope(
            provider=self.provider,
            key_id=self.key_id,
            ciphertext=ciphertext,
        )
        return SealedSecret(
            encrypted_secret=f"{self._prefix}{self._encode_envelope(envelope)}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )

    def open(self, encrypted_secret: str) -> str:
        envelope = self._decode_envelope(encrypted_secret)
        return self._client.decrypt(self.key_id, envelope.ciphertext)

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        self.open(encrypted_secret)
        return self.seal(new_secret_value)

    def delete(self, encrypted_secret: str) -> None:
        envelope = self._decode_envelope(encrypted_secret)
        self._client.delete(self.key_id, envelope.ciphertext)

    def fingerprint(self, encrypted_secret: str) -> str:
        secret = self.open(encrypted_secret)
        return f"sha256:{sha256(secret.encode('utf-8')).hexdigest()}"

    def _encode_envelope(self, envelope: _KmsVaultEnvelope) -> str:
        payload = envelope.model_dump()
        encoded = urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        return encoded

    def _decode_envelope(self, encrypted_secret: str) -> _KmsVaultEnvelope:
        if not encrypted_secret.startswith(self._prefix):
            raise ValueError("Unsupported KMS vault payload")
        encoded = encrypted_secret.removeprefix(self._prefix)
        if encoded == "":
            raise ValueError("Invalid KMS vault payload")
        try:
            decoded = b64decode(
                encoded.encode("ascii"),
                altchars=b"-_",
                validate=True,
            ).decode("utf-8")
            payload = json.loads(decoded)
            envelope = _KmsVaultEnvelope.model_validate(payload)
        except (
            BinasciiError,
            JSONDecodeError,
            TypeError,
            UnicodeEncodeError,
            UnicodeDecodeError,
            ValidationError,
        ) as exc:
            raise ValueError("Invalid KMS vault payload") from exc

        if envelope.provider != self.provider or envelope.key_id != self.key_id:
            raise ValueError("Invalid KMS vault payload")
        return envelope


SECRET_VAULT_PROVIDER_ENV = "AI_SCDC_SECRET_VAULT_PROVIDER"
SECRET_VAULT_KMS_KEY_ID_ENV = "AI_SCDC_KMS_KEY_ID"
_SECRET_VAULT_OVERRIDE: SecretVault | None = None
_KMS_CLIENT_OVERRIDE: KmsClient | None = None


def set_secret_vault_for_tests(vault: SecretVault | None) -> None:
    global _SECRET_VAULT_OVERRIDE
    _SECRET_VAULT_OVERRIDE = vault


def set_kms_client_for_tests(client: KmsClient | None) -> None:
    global _KMS_CLIENT_OVERRIDE
    _KMS_CLIENT_OVERRIDE = client


def get_secret_vault() -> SecretVault:
    if _SECRET_VAULT_OVERRIDE is not None:
        return _SECRET_VAULT_OVERRIDE

    provider = os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()
    if provider in {"", "dev", "development"}:
        return DevSecretVault()
    if provider in {"kms", "aliyun_kms"}:
        key_id = os.getenv(SECRET_VAULT_KMS_KEY_ID_ENV, "").strip()
        if key_id == "":
            raise SecretVaultConfigurationError(
                f"{SECRET_VAULT_KMS_KEY_ID_ENV} is required for KMS SecretVault provider"
            )
        return KmsSecretVault(
            client=_KMS_CLIENT_OVERRIDE,
            key_id=key_id,
            provider=provider,
        )
    raise SecretVaultConfigurationError(
        f"Secret vault provider {provider!r} is not configured"
    )
