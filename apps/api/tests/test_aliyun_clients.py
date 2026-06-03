import sys
from types import ModuleType, SimpleNamespace
from typing import get_type_hints

from ai_company_api.services.aliyun_config import AliyunSettings
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunEciCreateContainerGroupRequest,
    AliyunMnsSendMessageRequest,
    AliyunOssPutObjectRequest,
    SdkAliyunEciClient,
    SdkAliyunOssClient,
    get_aliyun_client_bundle,
    set_aliyun_client_bundle_for_tests,
)


class FakeMnsClient:
    def send_message(self, request: AliyunMnsSendMessageRequest):
        return {"message_id": f"msg-{request.cloud_run_id}"}


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
            cpu,
            memory,
            container,
        ):
            self.region_id = region_id
            self.container_group_name = container_group_name
            self.security_group_id = security_group_id
            self.v_switch_id = v_switch_id
            self.cpu = cpu
            self.memory = memory
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
            environment={"AI_SCDC_CLOUD_RUN_ID": "run-1"},
        )
    )

    assert result["container_group_id"] == "eci-run-1"
    assert captured["container"].environment_var[0].key == "AI_SCDC_CLOUD_RUN_ID"
    assert captured["request"].container[0] is captured["container"]


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
