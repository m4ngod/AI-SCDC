from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ai_company_api.services.aliyun_config import (
    AliyunSettings,
    load_aliyun_settings,
    require_aliyun_settings,
)


@dataclass(frozen=True)
class AliyunMnsSendMessageRequest:
    queue_name: str
    cloud_run_id: str
    workspace_id: str
    project_id: str
    task_id: str
    body: str


@dataclass(frozen=True)
class AliyunMnsReceiveMessageRequest:
    queue_name: str
    wait_seconds: int = 3


@dataclass(frozen=True)
class AliyunMnsDeleteMessageRequest:
    queue_name: str
    receipt_handle: str


@dataclass(frozen=True)
class AliyunMnsReceivedMessage:
    message_id: str
    receipt_handle: str
    body: str


@dataclass(frozen=True)
class AliyunOssPutObjectRequest:
    bucket: str
    object_key: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class AliyunEciCreateContainerGroupRequest:
    region_id: str
    cloud_run_id: str
    container_group_name: str
    image: str
    vswitch_id: str
    security_group_id: str
    cpu: float
    memory_gb: float
    restart_policy: str
    client_token: str
    environment: dict[str, str]
    auto_create_eip: bool = False
    eip_bandwidth: int = 1


@dataclass(frozen=True)
class AliyunEciDescribeContainerLogRequest:
    region_id: str
    container_group_id: str
    container_name: str
    tail: int = 2000
    limit_bytes: int = 1024 * 1024
    timestamps: bool = False


class AliyunMnsClient(Protocol):
    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict[str, Any]:
        ...

    def receive_message(
        self, request: AliyunMnsReceiveMessageRequest
    ) -> AliyunMnsReceivedMessage | None:
        ...

    def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> dict[str, str]:
        ...


class AliyunOssClient(Protocol):
    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        ...

    def get_object_text(self, bucket: str, object_key: str) -> str:
        ...


class AliyunEciClient(Protocol):
    def create_container_group(
        self,
        request: AliyunEciCreateContainerGroupRequest,
    ) -> dict[str, Any]:
        ...

    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict[str, Any]:
        ...

    def delete_container_group(
        self,
        *,
        region_id: str,
        container_group_id: str,
    ) -> None:
        ...


@dataclass(frozen=True)
class AliyunClientBundle:
    mns: AliyunMnsClient
    oss: AliyunOssClient
    eci: AliyunEciClient


_CLIENT_BUNDLE_OVERRIDE: AliyunClientBundle | None = None


def set_aliyun_client_bundle_for_tests(bundle: AliyunClientBundle | None) -> None:
    global _CLIENT_BUNDLE_OVERRIDE
    _CLIENT_BUNDLE_OVERRIDE = bundle


def get_aliyun_client_bundle(
    settings: AliyunSettings | None = None,
) -> AliyunClientBundle:
    if _CLIENT_BUNDLE_OVERRIDE is not None:
        return _CLIENT_BUNDLE_OVERRIDE

    resolved = settings or load_aliyun_settings()
    return AliyunClientBundle(
        mns=SdkAliyunMnsClient(resolved),
        oss=SdkAliyunOssClient(resolved),
        eci=SdkAliyunEciClient(resolved),
    )


@dataclass(frozen=True)
class SdkAliyunMnsClient:
    settings: AliyunSettings

    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict[str, Any]:
        from mns.account import Account
        from mns.queue import Message

        settings = require_aliyun_settings(
            provider_name="mns",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
            ),
            settings=self.settings,
        )
        queue = Account(
            settings.mns_endpoint,
            settings.access_key_id,
            settings.access_key_secret,
        ).get_queue(request.queue_name)
        result = queue.send_message(Message(request.body))
        return {
            "message_id": getattr(result, "message_id", None),
            "cloud_run_id": request.cloud_run_id,
        }

    def receive_message(
        self, request: AliyunMnsReceiveMessageRequest
    ) -> AliyunMnsReceivedMessage | None:
        from mns.account import Account

        settings = require_aliyun_settings(
            provider_name="mns",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
            ),
            settings=self.settings,
        )
        queue = Account(
            settings.mns_endpoint,
            settings.access_key_id,
            settings.access_key_secret,
        ).get_queue(request.queue_name)
        result = queue.receive_message(wait_seconds=request.wait_seconds)
        if result is None:
            return None
        body = getattr(result, "message_body", None) or getattr(result, "body", "")
        return AliyunMnsReceivedMessage(
            message_id=str(getattr(result, "message_id", "")),
            receipt_handle=str(getattr(result, "receipt_handle", "")),
            body=str(body),
        )

    def delete_message(self, request: AliyunMnsDeleteMessageRequest) -> dict[str, str]:
        from mns.account import Account

        settings = require_aliyun_settings(
            provider_name="mns",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "mns_endpoint",
            ),
            settings=self.settings,
        )
        queue = Account(
            settings.mns_endpoint,
            settings.access_key_id,
            settings.access_key_secret,
        ).get_queue(request.queue_name)
        result = queue.delete_message(request.receipt_handle)
        return result if isinstance(result, dict) else {"deleted": "true"}


@dataclass(frozen=True)
class SdkAliyunOssClient:
    settings: AliyunSettings

    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        import alibabacloud_oss_v2 as oss

        client = self._client()
        client.put_object(
            oss.PutObjectRequest(
                bucket=request.bucket,
                key=request.object_key,
                body=request.content,
                content_type=request.content_type,
            )
        )

    def get_object_text(self, bucket: str, object_key: str) -> str:
        import alibabacloud_oss_v2 as oss

        client = self._client()
        result = client.get_object(
            oss.GetObjectRequest(
                bucket=bucket,
                key=object_key,
            )
        )
        body = result.body.read()
        return body.decode("utf-8")

    def _client(self) -> Any:
        import alibabacloud_oss_v2 as oss

        settings = require_aliyun_settings(
            provider_name="oss",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "region_id",
                "oss_endpoint",
            ),
            settings=self.settings,
        )
        config = oss.config.load_default()
        config.region = settings.region_id
        config.endpoint = settings.oss_endpoint
        config.credentials_provider = oss.credentials.StaticCredentialsProvider(
            settings.access_key_id,
            settings.access_key_secret,
        )
        return oss.Client(config)


@dataclass(frozen=True)
class SdkAliyunEciClient:
    settings: AliyunSettings

    def create_container_group(
        self,
        request: AliyunEciCreateContainerGroupRequest,
    ) -> dict[str, Any]:
        from alibabacloud_eci20180808.client import Client
        from alibabacloud_eci20180808 import models as eci_models
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="eci",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "region_id",
            ),
            settings=self.settings,
        )
        client = Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=request.region_id,
            )
        )
        env_vars = [
            eci_models.CreateContainerGroupRequestContainerEnvironmentVar(
                key=key,
                value=value,
            )
            for key, value in request.environment.items()
        ]
        container = eci_models.CreateContainerGroupRequestContainer(
            name=request.container_group_name,
            image=request.image,
            cpu=request.cpu,
            memory=request.memory_gb,
            environment_var=env_vars,
        )
        create_request = eci_models.CreateContainerGroupRequest(
            region_id=request.region_id,
            container_group_name=request.container_group_name,
            security_group_id=request.security_group_id,
            v_switch_id=request.vswitch_id,
            restart_policy=request.restart_policy,
            client_token=request.client_token,
            cpu=request.cpu,
            memory=request.memory_gb,
            auto_create_eip=request.auto_create_eip,
            eip_bandwidth=request.eip_bandwidth,
            container=[container],
        )
        result = client.create_container_group(create_request)
        body = getattr(result, "body", None)
        return {
            "container_group_id": getattr(body, "container_group_id", None),
            "request_id": getattr(body, "request_id", None),
            "cloud_run_id": request.cloud_run_id,
        }

    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict[str, Any]:
        from alibabacloud_eci20180808.client import Client
        from alibabacloud_eci20180808 import models as eci_models
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="eci",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "region_id",
            ),
            settings=self.settings,
        )
        client = Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=request.region_id,
            )
        )
        result = client.describe_container_log(
            eci_models.DescribeContainerLogRequest(
                region_id=request.region_id,
                container_group_id=request.container_group_id,
                container_name=request.container_name,
                tail=request.tail,
                limit_bytes=request.limit_bytes,
                timestamps=request.timestamps,
            )
        )
        body = getattr(result, "body", None)
        return {
            "content": getattr(body, "content", "") or "",
            "request_id": getattr(body, "request_id", None),
        }

    def delete_container_group(
        self,
        *,
        region_id: str,
        container_group_id: str,
    ) -> None:
        from alibabacloud_eci20180808.client import Client
        from alibabacloud_eci20180808 import models as eci_models
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="eci",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "region_id",
            ),
            settings=self.settings,
        )
        client = Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=region_id,
            )
        )
        client.delete_container_group(
            eci_models.DeleteContainerGroupRequest(
                region_id=region_id,
                container_group_id=container_group_id,
            )
        )
