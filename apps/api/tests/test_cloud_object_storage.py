from hashlib import sha256

import pytest
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.schemas.api import CloudRunArtifactRefCreate
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunOssPutObjectRequest,
)
from ai_company_api.services.object_storage import (
    ObjectStorageReadError,
    ObjectStorageWrite,
    get_object_storage_provider,
)


def _build_storage_session(tmp_path):
    database_path = tmp_path / "object-storage.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    init_db(engine)
    return Session(engine)


def test_local_inline_storage_puts_and_reads_text_ref(tmp_path) -> None:
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("local_inline")
        text = "diff --git a/file.txt b/file.txt\n+hello\n"

        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="diff",
                content=text,
                content_type="text/x-diff",
            ),
        )

        assert ref.kind == "diff"
        assert ref.uri.startswith("local-inline://cloud-run-objects/")
        assert ref.sha256 == sha256(text.encode("utf-8")).hexdigest()
        assert ref.size_bytes == len(text.encode("utf-8"))
        assert ref.content_type == "text/x-diff"
        assert provider.read_text(session, ref) == text


def test_local_inline_storage_rejects_hash_mismatch(tmp_path) -> None:
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("local_inline")
        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="log",
                content="safe log",
                content_type="text/plain",
            ),
        )
        ref.sha256 = "0" * 64

        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, ref)


def test_cloud_run_artifact_ref_schema_accepts_storage_ref(tmp_path) -> None:
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("local_inline")
        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="manifest",
                content='{"status":"stored"}',
                content_type="application/json",
            ),
        )

        artifact_ref = CloudRunArtifactRefCreate(
            kind=ref.kind,
            uri=ref.uri,
            sha256=ref.sha256,
            size_bytes=ref.size_bytes,
            content_type=ref.content_type,
        )

        assert artifact_ref.kind == "manifest"
        assert artifact_ref.uri == ref.uri
        assert artifact_ref.sha256 == ref.sha256
        assert artifact_ref.size_bytes == ref.size_bytes
        assert artifact_ref.content_type == "application/json"


class FakeAliyunOssClient:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.put_requests: list[AliyunOssPutObjectRequest] = []

    def put_object(self, request: AliyunOssPutObjectRequest) -> None:
        self.put_requests.append(request)
        self.objects[(request.bucket, request.object_key)] = request.content

    def get_object_text(self, bucket: str, object_key: str) -> str:
        return self.objects[(bucket, object_key)].decode("utf-8")


class UnusedClient:
    pass


def _install_fake_oss(
    monkeypatch: pytest.MonkeyPatch,
    *,
    oss_prefix: str | None = None,
) -> FakeAliyunOssClient:
    monkeypatch.setenv("AI_SCDC_ALIYUN_REGION_ID", "cn-hangzhou")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AI_SCDC_ALIYUN_ACCESS_KEY_SECRET", "secret")
    monkeypatch.setenv(
        "AI_SCDC_ALIYUN_OSS_ENDPOINT",
        "https://oss-cn-hangzhou.aliyuncs.com",
    )
    monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_BUCKET", "ai-scdc-dev-artifacts")
    if oss_prefix is not None:
        monkeypatch.setenv("AI_SCDC_ALIYUN_OSS_PREFIX", oss_prefix)
    fake_oss = FakeAliyunOssClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(mns=UnusedClient(), oss=fake_oss, eci=UnusedClient()),
    )
    return fake_oss


def test_aliyun_oss_storage_puts_and_reads_text_ref(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_oss = _install_fake_oss(monkeypatch)
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("aliyun_oss")
        text = "diff --git a/app.py b/app.py\n+print('oss')\n"

        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="diff",
                content=text,
                content_type="text/x-diff",
            ),
        )

        assert ref.kind == "diff"
        assert ref.uri.startswith("oss://ai-scdc-dev-artifacts/ai-scdc/dev/")
        assert ref.sha256 == sha256(text.encode("utf-8")).hexdigest()
        assert ref.size_bytes == len(text.encode("utf-8"))
        assert ref.content_type == "text/x-diff"
        assert fake_oss.put_requests[0].content_type == "text/x-diff"
        assert provider.read_text(session, ref) == text


def test_aliyun_oss_storage_rejects_hash_mismatch(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_oss(monkeypatch)
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("aliyun_oss")
        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="log",
                content="safe log",
            ),
        )
        ref.sha256 = "0" * 64

        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, ref)


def test_aliyun_oss_storage_rejects_query_or_fragment_in_ref(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_oss(monkeypatch)
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("aliyun_oss")
        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="log",
                content="safe log",
            ),
        )
        ref.uri = f"{ref.uri}?token=secret#frag"

        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, ref)


def test_aliyun_oss_storage_reads_ref_with_root_prefix(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_oss(monkeypatch, oss_prefix="/")
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("aliyun_oss")
        text = "root prefix content"

        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="log",
                content=text,
            ),
        )

        assert ref.uri.startswith("oss://ai-scdc-dev-artifacts/workspaces/")
        assert provider.read_text(session, ref) == text
