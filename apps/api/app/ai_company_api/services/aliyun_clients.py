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


class AliyunMnsClient(Protocol):
    def send_message(self, request: AliyunMnsSendMessageRequest) -> dict[str, Any]:
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
            container=[container],
        )
        result = client.create_container_group(create_request)
        body = getattr(result, "body", None)
        return {
            "container_group_id": getattr(body, "container_group_id", None),
            "request_id": getattr(body, "request_id", None),
            "cloud_run_id": request.cloud_run_id,
        }
