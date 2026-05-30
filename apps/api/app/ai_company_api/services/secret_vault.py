from base64 import b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
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
