import sys
from types import ModuleType, SimpleNamespace
from typing import get_type_hints

import pytest

from ai_company_api.services.aliyun_config import AliyunSettings
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunEciCreateContainerGroupRequest,
    AliyunEciDescribeContainerLogRequest,
    AliyunMnsDeleteMessageRequest,
    AliyunMnsReceiveMessageRequest,
    AliyunMnsReceivedMessage,
    AliyunMnsSendMessageRequest,
    AliyunOssPutObjectRequest,
    SdkAliyunEciClient,
    SdkAliyunMnsClient,
    SdkAliyunOssClient,
    get_aliyun_client_bundle,
    set_aliyun_client_bundle_for_tests,
)


class FakeMnsClient:
    def __init__(self) -> None:
        self.receive_requests = []
        self.delete_requests = []
        self.next_received_message = None

    def send_message(self, request: AliyunMnsSendMessageRequest):
        return {"message_id": f"msg-{request.cloud_run_id}"}

    def receive_message(
        self, request: AliyunMnsReceiveMessageRequest
    ) -> AliyunMnsReceivedMessage | None:
        self.receive_requests.append(request)
        return self.next_received_message

    def delete_message(
        self, request: AliyunMnsDeleteMessageRequest
    ) -> dict[str, str]:
        self.delete_requests.append(request)
        return {"deleted": "true"}


class FakeOssClient:
    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        return None

    def get_object_text(self, bucket: str, object_key: str) -> str:
        return f"{bucket}/{object_key}"


class FakeEciClient:
    def create_container_group(self, request: AliyunEciCreateContainerGroupRequest):
        return {"container_group_id": f"eci-{request.cloud_run_id}"}


def test_client_bundle_override_is_returned_for_tests() -> None:
    bundle = AliyunClientBundle(
        mns=FakeMnsClient(),
        oss=FakeOssClient(),
        eci=FakeEciClient(),
    )
    set_aliyun_client_bundle_for_tests(bundle)
    try:
        assert get_aliyun_client_bundle() is bundle
    finally:
        set_aliyun_client_bundle_for_tests(None)


def test_oss_put_object_request_content_is_bytes() -> None:
    assert get_type_hints(AliyunOssPutObjectRequest)["content"] is bytes


def test_sdk_mns_receive_message_maps_sdk_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    captured: dict[str, object] = {}

    class FakeSdkMessage:
        message_id = "msg-1"
        receipt_handle = "receipt-1"
        message_body = '{"cloud_run_id":"cloud_run_1"}'

    class FakeQueue:
        def receive_message(self, wait_seconds: int | None = None) -> FakeSdkMessage:
            captured["wait_seconds"] = wait_seconds
            return FakeSdkMessage()

    class FakeAccount:
        def __init__(self, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
            captured["endpoint"] = endpoint
            captured["access_key_id"] = access_key_id
            captured["access_key_secret"] = access_key_secret

        def get_queue(self, queue_name: str) -> FakeQueue:
            captured["queue_name"] = queue_name
            return FakeQueue()

    account_module = ModuleType("mns.account")
    account_module.Account = FakeAccount
    monkeypatch.setitem(sys.modules, "mns.account", account_module)

    client = SdkAliyunMnsClient(_aliyun_settings())
    result = client.receive_message(
        AliyunMnsReceiveMessageRequest(queue_name="phase12c-queue", wait_seconds=7)
    )

    assert result == AliyunMnsReceivedMessage(
        message_id="msg-1",
        receipt_handle="receipt-1",
        body='{"cloud_run_id":"cloud_run_1"}',
    )
    assert captured["queue_name"] == "phase12c-queue"
    assert captured["wait_seconds"] == 7


def test_sdk_mns_delete_message_uses_receipt_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_complete_aliyun_env(monkeypatch)
    captured: dict[str, object] = {}

    class FakeQueue:
        def delete_message(self, receipt_handle: str) -> dict[str, str]:
            captured["receipt_handle"] = receipt_handle
            return {"ok": "true"}

    class FakeAccount:
        def __init__(self, endpoint: str, access_key_id: str, access_key_secret: str) -> None:
            captured["endpoint"] = endpoint

        def get_queue(self, queue_name: str) -> FakeQueue:
            captured["queue_name"] = queue_name
            return FakeQueue()

    account_module = ModuleType("mns.account")
    account_module.Account = FakeAccount
    monkeypatch.setitem(sys.modules, "mns.account", account_module)

    client = SdkAliyunMnsClient(_aliyun_settings())
    result = client.delete_message(
        AliyunMnsDeleteMessageRequest(queue_name="phase12c-queue", receipt_handle="receipt-1")
    )

    assert result == {"ok": "true"}
    assert captured["queue_name"] == "phase12c-queue"
    assert captured["receipt_handle"] == "receipt-1"


def test_sdk_oss_put_object_passes_bytes_body_to_sdk(monkeypatch) -> None:
    captured_requests = []

    class FakePutObjectRequest:
        def __init__(self, *, bucket, key, body, content_type):
            self.bucket = bucket
            self.key = key
            self.body = body
            self.content_type = content_type

    class FakeOssClient:
        def __init__(self, config):
            self.config = config

        def put_object(self, request):
            captured_requests.append(request)

    fake_oss = ModuleType("alibabacloud_oss_v2")
    fake_oss.PutObjectRequest = FakePutObjectRequest
    fake_oss.Client = FakeOssClient
    fake_oss.config = SimpleNamespace(load_default=lambda: SimpleNamespace())
    fake_oss.credentials = SimpleNamespace(
        StaticCredentialsProvider=lambda access_key_id, access_key_secret: (
            access_key_id,
            access_key_secret,
        )
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_oss_v2", fake_oss)

    client = SdkAliyunOssClient(_aliyun_settings())

    client.put_object(
        AliyunOssPutObjectRequest(
            bucket="bucket",
            object_key="runs/run-1/result.json",
            content=b"{\"ok\": true}",
            content_type="application/json",
        )
    )

    assert captured_requests[0].body == b"{\"ok\": true}"


def test_sdk_eci_create_container_group_uses_sdk_environment_var_field(
    monkeypatch,
) -> None:
    captured = {}

    class FakeEnvironmentVar:
        def __init__(self, *, key, value):
            self.key = key
            self.value = value

    class FakeContainer:
        def __init__(self, *, name, image, cpu, memory, environment_var):
            self.name = name
            self.image = image
            self.cpu = cpu
            self.memory = memory
            self.environment_var = environment_var
            captured["container"] = self

    class FakeCreateContainerGroupRequest:
        def __init__(
            self,
            *,
            region_id,
            container_group_name,
            security_group_id,
            v_switch_id,
            restart_policy,
            client_token,
            cpu,
            memory,
            auto_create_eip,
            eip_bandwidth,
            container,
        ):
            self.region_id = region_id
            self.container_group_name = container_group_name
            self.security_group_id = security_group_id
            self.v_switch_id = v_switch_id
            self.restart_policy = restart_policy
            self.client_token = client_token
            self.cpu = cpu
            self.memory = memory
            self.auto_create_eip = auto_create_eip
            self.eip_bandwidth = eip_bandwidth
            self.container = container

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def create_container_group(self, request):
            captured["request"] = request
            body = SimpleNamespace(container_group_id="eci-run-1", request_id="req-1")
            return SimpleNamespace(body=body)

    class FakeConfig:
        def __init__(self, *, access_key_id, access_key_secret, region_id):
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.region_id = region_id

    eci_package = ModuleType("alibabacloud_eci20180808")
    eci_client_module = ModuleType("alibabacloud_eci20180808.client")
    eci_models_module = ModuleType("alibabacloud_eci20180808.models")
    eci_client_module.Client = FakeClient
    eci_models_module.CreateContainerGroupRequestContainerEnvironmentVar = (
        FakeEnvironmentVar
    )
    eci_models_module.CreateContainerGroupRequestContainer = FakeContainer
    eci_models_module.CreateContainerGroupRequest = FakeCreateContainerGroupRequest
    eci_package.models = eci_models_module

    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_models_module = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models_module.Config = FakeConfig
    openapi_package.models = openapi_models_module

    monkeypatch.setitem(sys.modules, "alibabacloud_eci20180808", eci_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.client",
        eci_client_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.models",
        eci_models_module,
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi.models",
        openapi_models_module,
    )

    result = SdkAliyunEciClient(_aliyun_settings()).create_container_group(
        AliyunEciCreateContainerGroupRequest(
            region_id="cn-hangzhou",
            cloud_run_id="run-1",
            container_group_name="ai-scdc-run-1",
            image="registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
            vswitch_id="vsw-demo",
            security_group_id="sg-demo",
            cpu=1.0,
            memory_gb=2.0,
            restart_policy="Never",
            client_token="ai-scdc-run-1",
            auto_create_eip=True,
            eip_bandwidth=1,
            environment={"AI_SCDC_CLOUD_RUN_ID": "run-1"},
        )
    )

    assert result["container_group_id"] == "eci-run-1"
    assert captured["container"].environment_var[0].key == "AI_SCDC_CLOUD_RUN_ID"
    assert captured["request"].v_switch_id == "vsw-demo"
    assert captured["request"].restart_policy == "Never"
    assert captured["request"].client_token == "ai-scdc-run-1"
    assert captured["request"].auto_create_eip is True
    assert captured["request"].eip_bandwidth == 1
    assert captured["request"].container[0] is captured["container"]


def test_sdk_aliyun_eci_client_delete_container_group_builds_request(
    monkeypatch,
) -> None:
    captured = {}

    class FakeDeleteContainerGroupRequest:
        def __init__(self, *, region_id, container_group_id):
            self.region_id = region_id
            self.container_group_id = container_group_id

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def delete_container_group(self, request):
            captured["request"] = request

    class FakeConfig:
        def __init__(self, *, access_key_id, access_key_secret, region_id):
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.region_id = region_id

    eci_package = ModuleType("alibabacloud_eci20180808")
    eci_client_module = ModuleType("alibabacloud_eci20180808.client")
    eci_models_module = ModuleType("alibabacloud_eci20180808.models")
    eci_client_module.Client = FakeClient
    eci_models_module.DeleteContainerGroupRequest = FakeDeleteContainerGroupRequest
    eci_package.models = eci_models_module

    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_models_module = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models_module.Config = FakeConfig
    openapi_package.models = openapi_models_module

    monkeypatch.setitem(sys.modules, "alibabacloud_eci20180808", eci_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.client",
        eci_client_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.models",
        eci_models_module,
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi.models",
        openapi_models_module,
    )

    SdkAliyunEciClient(_aliyun_settings()).delete_container_group(
        region_id="cn-hangzhou",
        container_group_id="eci-cg-1",
    )

    assert captured["request"].region_id == "cn-hangzhou"
    assert captured["request"].container_group_id == "eci-cg-1"


def test_sdk_aliyun_eci_client_describe_container_log_builds_request(
    monkeypatch,
) -> None:
    captured = {}

    class FakeDescribeContainerLogRequest:
        def __init__(
            self,
            *,
            region_id,
            container_group_id,
            container_name,
            tail,
            limit_bytes,
            timestamps,
        ):
            self.region_id = region_id
            self.container_group_id = container_group_id
            self.container_name = container_name
            self.tail = tail
            self.limit_bytes = limit_bytes
            self.timestamps = timestamps

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def describe_container_log(self, request):
            captured["request"] = request
            body = SimpleNamespace(content="worker log line\n", request_id="req-log-1")
            return SimpleNamespace(body=body)

    class FakeConfig:
        def __init__(self, *, access_key_id, access_key_secret, region_id):
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.region_id = region_id

    eci_package = ModuleType("alibabacloud_eci20180808")
    eci_client_module = ModuleType("alibabacloud_eci20180808.client")
    eci_models_module = ModuleType("alibabacloud_eci20180808.models")
    eci_client_module.Client = FakeClient
    eci_models_module.DescribeContainerLogRequest = FakeDescribeContainerLogRequest
    eci_package.models = eci_models_module

    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_models_module = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models_module.Config = FakeConfig
    openapi_package.models = openapi_models_module

    monkeypatch.setitem(sys.modules, "alibabacloud_eci20180808", eci_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.client",
        eci_client_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.models",
        eci_models_module,
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi.models",
        openapi_models_module,
    )

    result = SdkAliyunEciClient(_aliyun_settings()).describe_container_log(
        AliyunEciDescribeContainerLogRequest(
            region_id="cn-hangzhou",
            container_group_id="eci-cg-run-1",
            container_name="ai-scdc-run-1",
            tail=500,
            limit_bytes=1024,
            timestamps=True,
        )
    )

    assert result == {"content": "worker log line\n", "request_id": "req-log-1"}
    assert captured["request"].region_id == "cn-hangzhou"
    assert captured["request"].container_group_id == "eci-cg-run-1"
    assert captured["request"].container_name == "ai-scdc-run-1"
    assert captured["request"].tail == 500
    assert captured["request"].limit_bytes == 1024
    assert captured["request"].timestamps is True


def _set_complete_aliyun_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak-id")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "ak-secret")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_MNS_ENDPOINT",
        "https://123456.mns.cn-hangzhou.aliyuncs.com",
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_MNS_QUEUE_NAME", "ai-scdc-cloud-runs-dev")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_OSS_ENDPOINT",
        "https://oss-cn-hangzhou.aliyuncs.com",
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_VSWITCH_ID", "vsw-demo")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ECI_SECURITY_GROUP_ID", "sg-demo")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_ECI_IMAGE",
        "registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
    )
    monkeypatch.setenv("AI_SCDC_API_PUBLIC_BASE_URL", "https://api.example.test")


def _aliyun_settings() -> AliyunSettings:
    return AliyunSettings(
        region_id="cn-hangzhou",
        access_key_id="ak-id",
        access_key_secret="ak-secret",
        mns_endpoint="https://123456.mns.cn-hangzhou.aliyuncs.com",
        mns_queue_name="ai-scdc-cloud-runs-dev",
        oss_endpoint="https://oss-cn-hangzhou.aliyuncs.com",
        oss_bucket="ai-scdc-dev-artifacts",
        eci_vswitch_id="vsw-demo",
        eci_security_group_id="sg-demo",
        eci_image="registry.cn-hangzhou.aliyuncs.com/ai-scdc/remote-worker:dev",
        api_public_base_url="https://api.example.test",
    )
