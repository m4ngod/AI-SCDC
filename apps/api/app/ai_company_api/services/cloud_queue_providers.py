from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_company_api.services.aliyun_config import require_aliyun_settings


class CloudQueueProviderNotFound(Exception):
    pass


@dataclass(frozen=True)
class CloudQueueEnqueueRequest:
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    queue_provider: str
    runtime_provider: str | None
    storage_provider: str | None


@dataclass(frozen=True)
class CloudQueueEnqueueResult:
    queue_message_id: str | None = None
    queue_receipt: str | None = None
    external_status: str | None = None


class CloudQueueProvider(Protocol):
    name: str

    def validate_configuration(self) -> None:
        ...

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        ...


@dataclass(frozen=True)
class RegisteredCloudQueueProvider:
    name: str

    def validate_configuration(self) -> None:
        return None

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        external_status = "queued" if self.name == "external_stub" else None
        return CloudQueueEnqueueResult(external_status=external_status)


class AliyunMnsQueueProvider:
    name = "aliyun_mns"

    def validate_configuration(self) -> None:
        require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
                "mns_queue_name",
            ),
        )

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        self.validate_configuration()
        return CloudQueueEnqueueResult(external_status="queued")


_KNOWN_QUEUE_PROVIDERS = {
    "local_db": RegisteredCloudQueueProvider(name="local_db"),
    "external_stub": RegisteredCloudQueueProvider(name="external_stub"),
    "aliyun_mns": AliyunMnsQueueProvider(),
}


def get_cloud_queue_provider(name: str) -> CloudQueueProvider:
    provider = _KNOWN_QUEUE_PROVIDERS.get(name)
    if provider is None:
        raise CloudQueueProviderNotFound(f"Unknown cloud queue provider: {name}")
    return provider
