from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CloudQueueProviderNotFound(Exception):
    pass


class CloudQueueProvider(Protocol):
    name: str


@dataclass(frozen=True)
class RegisteredCloudQueueProvider:
    name: str


_KNOWN_QUEUE_PROVIDERS = {
    "local_db": RegisteredCloudQueueProvider(name="local_db"),
    "external_stub": RegisteredCloudQueueProvider(name="external_stub"),
}


def get_cloud_queue_provider(name: str) -> CloudQueueProvider:
    provider = _KNOWN_QUEUE_PROVIDERS.get(name)
    if provider is None:
        raise CloudQueueProviderNotFound(f"Unknown cloud queue provider: {name}")
    return provider
