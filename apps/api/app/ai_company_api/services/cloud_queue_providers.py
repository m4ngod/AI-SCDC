from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol

from ai_company_api.services.aliyun_clients import (
    AliyunMnsDeleteMessageRequest,
    AliyunMnsReceiveMessageRequest,
    AliyunMnsSendMessageRequest,
    get_aliyun_client_bundle,
)
from ai_company_api.services.aliyun_config import require_aliyun_settings


class CloudQueueProviderNotFound(Exception):
    pass


class CloudQueueProviderError(Exception):
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
    worker_id: str | None = None
    callback_token: str | None = None
    callback_token_expires_at: str | None = None


@dataclass(frozen=True)
class CloudQueueEnqueueResult:
    queue_message_id: str | None = None
    queue_receipt: str | None = None
    external_status: str | None = None


@dataclass(frozen=True)
class CloudQueueReceivedMessage:
    queue_message_id: str
    queue_receipt: str
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    queue_provider: str
    runtime_provider: str | None
    storage_provider: str
    worker_id: str
    callback_token: str
    callback_token_expires_at: str


class CloudQueueProvider(Protocol):
    name: str

    def validate_configuration(self) -> None:
        ...

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        ...

    def receive(
        self, *, wait_seconds: int = 3
    ) -> CloudQueueReceivedMessage | None:
        ...

    def delete(self, *, queue_receipt: str) -> None:
        ...


@dataclass(frozen=True)
class RegisteredCloudQueueProvider:
    name: str

    def validate_configuration(self) -> None:
        return None

    def enqueue(self, request: CloudQueueEnqueueRequest) -> CloudQueueEnqueueResult:
        external_status = "queued" if self.name == "external_stub" else None
        return CloudQueueEnqueueResult(external_status=external_status)

    def receive(
        self, *, wait_seconds: int = 3
    ) -> CloudQueueReceivedMessage | None:
        raise CloudQueueProviderError(
            f"Cloud queue provider {self.name} does not support receive()"
        )

    def delete(self, *, queue_receipt: str) -> None:
        raise CloudQueueProviderError(
            f"Cloud queue provider {self.name} does not support delete()"
        )


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
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
                "mns_queue_name",
            ),
        )
        message_body = {
            "workspace_id": request.workspace_id,
            "project_id": request.project_id,
            "task_id": request.task_id,
            "cloud_run_id": request.cloud_run_id,
            "queue_provider": request.queue_provider,
            "runtime_provider": request.runtime_provider,
            "storage_provider": request.storage_provider,
        }
        if (
            request.worker_id
            and request.callback_token
            and request.callback_token_expires_at
        ):
            message_body.update(
                {
                    "worker_id": request.worker_id,
                    "callback_token": request.callback_token,
                    "callback_token_expires_at": request.callback_token_expires_at,
                }
            )
        body = json.dumps(message_body, sort_keys=True)
        try:
            result = get_aliyun_client_bundle(settings).mns.send_message(
                AliyunMnsSendMessageRequest(
                    queue_name=settings.mns_queue_name or "",
                    cloud_run_id=request.cloud_run_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                    task_id=request.task_id,
                    body=body,
                )
            )
        except Exception:
            raise CloudQueueProviderError(
                f"Cloud queue provider {self.name} failed to enqueue message"
            ) from None
        return CloudQueueEnqueueResult(
            queue_message_id=result.get("message_id"),
            external_status="queued",
        )

    def receive(
        self, *, wait_seconds: int = 3
    ) -> CloudQueueReceivedMessage | None:
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
                "mns_queue_name",
            ),
        )
        received = get_aliyun_client_bundle(settings).mns.receive_message(
            AliyunMnsReceiveMessageRequest(
                queue_name=settings.mns_queue_name or "",
                wait_seconds=wait_seconds,
            )
        )
        if received is None:
            return None
        try:
            payload = json.loads(received.body)
        except json.JSONDecodeError as exc:
            raise CloudQueueProviderError(
                "invalid MNS message: body is not JSON"
            ) from exc
        return _parse_mns_received_message(
            received.message_id,
            received.receipt_handle,
            payload,
        )

    def delete(self, *, queue_receipt: str) -> None:
        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
                "mns_queue_name",
            ),
        )
        get_aliyun_client_bundle(settings).mns.delete_message(
            AliyunMnsDeleteMessageRequest(
                queue_name=settings.mns_queue_name or "",
                receipt_handle=queue_receipt,
            )
        )


def _parse_mns_received_message(
    queue_message_id: str,
    queue_receipt: str,
    payload: object,
) -> CloudQueueReceivedMessage:
    if not isinstance(queue_message_id, str) or queue_message_id == "":
        raise CloudQueueProviderError(
            "invalid MNS message: field message_id must be a non-empty string"
        )
    if not isinstance(queue_receipt, str) or queue_receipt == "":
        raise CloudQueueProviderError(
            "invalid MNS message: field receipt_handle must be a non-empty string"
        )
    if not isinstance(payload, dict):
        raise CloudQueueProviderError("invalid MNS message: body is not an object")

    required_fields = (
        "workspace_id",
        "project_id",
        "task_id",
        "cloud_run_id",
        "queue_provider",
        "storage_provider",
        "worker_id",
        "callback_token",
        "callback_token_expires_at",
    )
    values: dict[str, str] = {}
    for field in required_fields:
        value = payload.get(field)
        if not isinstance(value, str) or value == "":
            raise CloudQueueProviderError(
                f"invalid MNS message: field {field} must be a non-empty string"
            )
        values[field] = value

    runtime_provider = payload.get("runtime_provider")
    if runtime_provider is not None and not isinstance(runtime_provider, str):
        raise CloudQueueProviderError(
            "invalid MNS message: field runtime_provider must be a string or null"
        )

    return CloudQueueReceivedMessage(
        queue_message_id=queue_message_id,
        queue_receipt=queue_receipt,
        workspace_id=values["workspace_id"],
        project_id=values["project_id"],
        task_id=values["task_id"],
        cloud_run_id=values["cloud_run_id"],
        queue_provider=values["queue_provider"],
        runtime_provider=runtime_provider,
        storage_provider=values["storage_provider"],
        worker_id=values["worker_id"],
        callback_token=values["callback_token"],
        callback_token_expires_at=values["callback_token_expires_at"],
    )


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
