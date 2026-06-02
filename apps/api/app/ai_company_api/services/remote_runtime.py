from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RemoteRuntimeProviderNotFound(Exception):
    pass


class RemoteRuntimeProvider(Protocol):
    name: str


@dataclass(frozen=True)
class RegisteredRemoteRuntimeProvider:
    name: str


_KNOWN_RUNTIME_PROVIDERS = {
    "remote_stub": RegisteredRemoteRuntimeProvider(name="remote_stub"),
}


def get_remote_runtime_provider(name: str | None) -> RemoteRuntimeProvider | None:
    if name is None:
        return None
    provider = _KNOWN_RUNTIME_PROVIDERS.get(name)
    if provider is None:
        raise RemoteRuntimeProviderNotFound(
            f"Unknown remote runtime provider: {name}"
        )
    return provider
