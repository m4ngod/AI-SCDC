from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunEciCreateContainerGroupRequest,
    AliyunMnsSendMessageRequest,
    AliyunOssPutObjectRequest,
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
