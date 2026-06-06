import copy
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest
from sqlalchemy import inspect, text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import CloudRunStoredObject
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


def test_local_inline_storage_persists_retention_metadata(tmp_path) -> None:
    with _build_storage_session(tmp_path) as session:
        provider = get_object_storage_provider("local_inline")
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)

        ref = provider.put_text(
            session,
            ObjectStorageWrite(
                workspace_id="dev_workspace",
                cloud_run_id="cloud_run_1",
                kind="log",
                content="retained log",
                content_type="text/plain",
                expires_at=expires_at,
                retention_policy="development_default",
            ),
        )
        session.commit()

        stored_object = session.get(
            CloudRunStoredObject,
            ref.uri.removeprefix("local-inline://cloud-run-objects/"),
        )

        assert stored_object is not None
        assert stored_object.expires_at is not None
        assert stored_object.expires_at.replace(tzinfo=timezone.utc) == expires_at
        assert stored_object.retention_policy == "development_default"


def test_init_db_upgrades_existing_stored_object_table_with_retention_columns(
    tmp_path,
) -> None:
    database_path = tmp_path / "stored-object-upgrade.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE cloud_run_stored_object (
                    id VARCHAR NOT NULL,
                    workspace_id VARCHAR NOT NULL,
                    cloud_run_id VARCHAR NOT NULL,
                    kind VARCHAR NOT NULL,
                    uri VARCHAR NOT NULL,
                    sha256 VARCHAR NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_type VARCHAR NOT NULL,
                    text_content VARCHAR NOT NULL,
                    created_at DATETIME NOT NULL,
                    PRIMARY KEY (id)
                )
                """
            )
        )

    init_db(engine)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("cloud_run_stored_object")
    }
    indexes = {
        index["name"]
        for index in inspect(engine).get_indexes("cloud_run_stored_object")
    }
    assert "expires_at" in columns
    assert "retention_policy" in columns
    assert "ix_cloud_run_stored_object_expires_at" in indexes
    assert "ix_cloud_run_stored_object_retention_policy" in indexes


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


def test_aliyun_oss_storage_rejects_bucket_prefix_size_and_kind_mismatch(
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

        wrong_bucket = copy.copy(ref)
        wrong_bucket.uri = wrong_bucket.uri.replace(
            "oss://ai-scdc-dev-artifacts/",
            "oss://other-bucket/",
            1,
        )
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_bucket)

        wrong_prefix = copy.copy(ref)
        wrong_prefix.uri = wrong_prefix.uri.replace(
            "/workspaces/dev_workspace/",
            "/other/dev_workspace/",
            1,
        )
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_prefix)

        wrong_size = copy.copy(ref)
        wrong_size.size_bytes += 1
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_size)

        wrong_kind = copy.copy(ref)
        wrong_kind.kind = "diff"
        with pytest.raises(ObjectStorageReadError):
            provider.read_text(session, wrong_kind)
