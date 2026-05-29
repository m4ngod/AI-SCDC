from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, Field


class SealedSecret(BaseModel):
    encrypted_secret: str = Field(min_length=1)
    secret_last4: str


class SecretVault(Protocol):
    def seal(self, secret_value: str) -> SealedSecret:
        ...


class DevSecretVault:
    def seal(self, secret_value: str) -> SealedSecret:
        digest = sha256(secret_value.encode("utf-8")).hexdigest()
        return SealedSecret(
            encrypted_secret=f"dev-vault:v1:{digest}",
            secret_last4=secret_value[-4:] if len(secret_value) >= 4 else secret_value,
        )
