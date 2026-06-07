from base64 import b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
from hashlib import sha256
import os
from typing import Protocol

from pydantic import BaseModel, Field


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


class SecretVaultConfigurationError(RuntimeError):
    pass


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
    def seal(self, secret_value: str) -> SealedSecret:
        raise self._not_configured()

    def open(self, encrypted_secret: str) -> str:
        raise self._not_configured()

    def rotate(self, encrypted_secret: str, new_secret_value: str) -> SealedSecret:
        raise self._not_configured()

    def delete(self, encrypted_secret: str) -> None:
        raise self._not_configured()

    def fingerprint(self, encrypted_secret: str) -> str:
        raise self._not_configured()

    def _not_configured(self) -> SecretVaultConfigurationError:
        return SecretVaultConfigurationError("KMS SecretVault provider is not configured")


SECRET_VAULT_PROVIDER_ENV = "AI_SCDC_SECRET_VAULT_PROVIDER"
_SECRET_VAULT_OVERRIDE: SecretVault | None = None


def set_secret_vault_for_tests(vault: SecretVault | None) -> None:
    global _SECRET_VAULT_OVERRIDE
    _SECRET_VAULT_OVERRIDE = vault


def get_secret_vault() -> SecretVault:
    if _SECRET_VAULT_OVERRIDE is not None:
        return _SECRET_VAULT_OVERRIDE

    provider = os.getenv(SECRET_VAULT_PROVIDER_ENV, "dev").strip().lower()
    if provider in {"", "dev", "development"}:
        return DevSecretVault()
    if provider in {"kms", "aliyun_kms"}:
        return KmsSecretVault()
    raise SecretVaultConfigurationError(
        f"Secret vault provider {provider!r} is not configured"
    )
