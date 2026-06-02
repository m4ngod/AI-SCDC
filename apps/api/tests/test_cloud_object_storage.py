from hashlib import sha256

import pytest
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.schemas.api import CloudRunArtifactRefCreate
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
