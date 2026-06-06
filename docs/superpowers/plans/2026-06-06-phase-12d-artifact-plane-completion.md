# Phase 12D Artifact Plane Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the remaining original Phase 12 artifact-plane goals by adding manifest, artifact read, download descriptor, retention cleanup, and desktop artifact browsing surfaces.

**Architecture:** Add a narrow `artifact_plane` service that builds read-only cloud-run artifact manifests from existing cloud-run metadata, local-inline stored-object rows, and patch-artifact fallback data. Keep provider-specific storage reads behind `object_storage`, expose only redacted provider refs and local API download URLs, and make cleanup delete only local-inline rows while reporting external lifecycle-only intent. The desktop consumes the new manifest endpoints through the existing console API client and renders artifacts inside the current cloud-run task detail area.

**Tech Stack:** FastAPI, SQLModel, SQLite upgrade helpers, pytest, React, TypeScript, Vitest, Testing Library.

---

## Execution Setup

Before implementation starts, create or switch into an isolated worktree with `superpowers:using-git-worktrees`. Do not implement this plan directly on `master` unless the user explicitly requests it.

Expected setup command:

```bash
git status -sb
```

Expected output shape:

```text
the first line shows the current branch and ahead count
```

After creating the worktree, each task below ends with a commit. Keep commits small so review can stop after any task.

## File Structure

- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Adds retention metadata columns to `CloudRunStoredObject`.
- Modify: `apps/api/app/ai_company_api/db/session.py`
  - Adds the SQLite upgrade helper for existing `cloud_run_stored_object` tables.
- Modify: `apps/api/app/ai_company_api/services/object_storage.py`
  - Carries retention metadata through local-inline object writes.
- Create: `apps/api/app/ai_company_api/services/artifact_plane.py`
  - Owns manifest construction, artifact id resolution, scoped content reads, provider-neutral download descriptors, redaction, and expired-artifact cleanup.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Adds artifact manifest, descriptor, content, download, and cleanup API models.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Adds Phase 12D artifact-plane endpoints.
- Modify: `apps/api/tests/test_cloud_object_storage.py`
  - Covers retention metadata persistence and SQLite upgrade behavior.
- Modify: `apps/api/tests/test_cloud_run_api.py`
  - Covers manifest/list/detail/content/download/cleanup API behavior.
- Modify: `apps/desktop/src/api/client.ts`
  - Adds artifact card types, fake client artifact data, and HTTP client artifact methods.
- Modify: `apps/desktop/src/App.tsx`
  - Fetches artifact manifests with cloud-run updates and opens artifact text previews.
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
  - Renders grouped artifact metadata and preview controls.
- Modify: `apps/desktop/src/styles/app.css`
  - Adds compact artifact list and preview styling.
- Modify: `apps/desktop/src/test/client.test.ts`
  - Covers fake and HTTP artifact client methods.
- Modify: `apps/desktop/src/test/App.test.tsx`
  - Covers artifact manifest rendering and preview opening.
- Modify: `README.md`
  - Adds Phase 12D smoke commands and endpoint summary.
- Modify: `docs/architecture.md`
  - Updates Phase 12 boundary now that artifact browser and manifest APIs exist.
- Modify: `docs/superpowers/status.md`
  - Marks Phase 12D as completed after verification.
- Modify: `STATUS.md`
  - Records final Phase 12D verification evidence.

---

### Task 1: Stored-Object Retention Metadata

**Files:**
- Modify: `apps/api/tests/test_cloud_object_storage.py`
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Modify: `apps/api/app/ai_company_api/services/object_storage.py`

- [ ] **Step 1: Write failing storage retention tests**

Modify the imports at the top of `apps/api/tests/test_cloud_object_storage.py` to include retention and migration helpers:

```python
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
```

Append these tests after `test_local_inline_storage_puts_and_reads_text_ref`:

```python
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
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest apps/api/tests/test_cloud_object_storage.py -q -k "retention_metadata or stored_object_table"
```

Expected output:

```text
FAILED apps/api/tests/test_cloud_object_storage.py::test_local_inline_storage_persists_retention_metadata
FAILED apps/api/tests/test_cloud_object_storage.py::test_init_db_upgrades_existing_stored_object_table_with_retention_columns
```

The first failure should mention `ObjectStorageWrite` receiving an unexpected keyword or `CloudRunStoredObject` lacking `expires_at`. The second failure should show missing retention columns or indexes.

- [ ] **Step 3: Add retention columns to the entity**

In `apps/api/app/ai_company_api/models/entities.py`, modify `CloudRunStoredObject`:

```python
class CloudRunStoredObject(SQLModel, table=True):
    __tablename__ = "cloud_run_stored_object"

    id: str = Field(default_factory=uuid_hex, primary_key=True)
    workspace_id: str = Field(index=True)
    cloud_run_id: str = Field(index=True)
    kind: str = Field(index=True)
    uri: str = Field(index=True)
    sha256: str
    size_bytes: int
    content_type: str = "text/plain"
    text_content: str
    expires_at: datetime | None = Field(default=None, index=True)
    retention_policy: str | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] **Step 4: Add the SQLite upgrade helper**

In `apps/api/app/ai_company_api/db/session.py`, call the new helper before `SQLModel.metadata.create_all(engine)`:

```python
def init_db(engine) -> None:
    _upgrade_sqlite_cloud_run_phase_9_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10a_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10b_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10d_columns(engine)
    _upgrade_sqlite_cloud_run_phase_12a_columns(engine)
    _upgrade_sqlite_cloud_run_stored_object_phase_12d_columns(engine)
    SQLModel.metadata.create_all(engine)
    _upgrade_sqlite_repository_phase_7_columns(engine)
    _upgrade_sqlite_cloud_run_phase_8_columns(engine)
    _upgrade_sqlite_local_test_run_nullable_patch_artifact(engine)
    _upgrade_sqlite_planner_run_metadata(engine)
    _upgrade_sqlite_task_execution_constraints(engine)
    _upgrade_sqlite_patch_review_uniqueness(engine)
```

Add this helper near the other cloud-run upgrade helpers:

```python
def _upgrade_sqlite_cloud_run_stored_object_phase_12d_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    stored_object_columns = {
        "expires_at": "DATETIME",
        "retention_policy": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run_stored_object" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(cloud_run_stored_object)")
            ).mappings()
        }
        for column_name, column_type in stored_object_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE cloud_run_stored_object "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        for column_name in ("expires_at", "retention_policy"):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_stored_object_{column_name} "
                    f"ON cloud_run_stored_object ({column_name})"
                )
            )
```

- [ ] **Step 5: Carry retention metadata through object storage writes**

In `apps/api/app/ai_company_api/services/object_storage.py`, add the import:

```python
from datetime import datetime
```

Modify `ObjectStorageWrite`:

```python
@dataclass
class ObjectStorageWrite:
    workspace_id: str
    cloud_run_id: str
    kind: str
    content: str
    content_type: str = "text/plain"
    expires_at: datetime | None = None
    retention_policy: str | None = None
```

Modify `LocalInlineObjectStorageProvider.put_text` so the stored row receives the metadata:

```python
stored_object = CloudRunStoredObject(
    workspace_id=write.workspace_id,
    cloud_run_id=write.cloud_run_id,
    kind=write.kind,
    uri="",
    sha256=sha256(content_bytes).hexdigest(),
    size_bytes=len(content_bytes),
    content_type=write.content_type,
    text_content=write.content,
    expires_at=write.expires_at,
    retention_policy=write.retention_policy,
)
```

Do not add retention metadata to `ObjectStorageRef`; refs remain provider-neutral object pointers.

- [ ] **Step 6: Run tests and confirm they pass**

Run:

```bash
pytest apps/api/tests/test_cloud_object_storage.py -q -k "retention_metadata or stored_object_table"
```

Expected output:

```text
2 passed
```

- [ ] **Step 7: Commit Task 1**

Run:

```bash
git add apps/api/tests/test_cloud_object_storage.py apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/services/object_storage.py
git commit -m "Add cloud run object retention metadata"
```

Expected output:

```text
commit summary includes "Add cloud run object retention metadata"
```

---

### Task 2: Artifact Manifest, List, Detail, And Content APIs

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/artifact_plane.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`

- [ ] **Step 1: Write failing API tests for manifest, list, detail, content, scope, and integrity**

In `apps/api/tests/test_cloud_run_api.py`, add `CloudRunStoredObject` to the existing `ai_company_api.models.entities` import list:

```python
from ai_company_api.models.entities import (
    CloudRun,
    CloudRunLogEntry,
    CloudRunStoredObject,
    TaskEvent,
    GitHubCredential,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    Project,
    Repository,
    SandboxProfile,
    Task,
    utc_now,
)
```

Add these helpers near the existing cloud-run test helpers:

```python
def store_cloud_run_artifact_ref(
    session: Session,
    cloud_run: CloudRun,
    *,
    kind: str,
    content: str,
    content_type: str = "text/plain",
    expires_at: datetime | None = None,
    retention_policy: str | None = "development_default",
):
    return get_object_storage_provider("local_inline").put_text(
        session,
        ObjectStorageWrite(
            workspace_id=cloud_run.workspace_id,
            cloud_run_id=cloud_run.id,
            kind=kind,
            content=content,
            content_type=content_type,
            expires_at=expires_at,
            retention_policy=retention_policy,
        ),
    )


def attach_phase_12d_artifacts(
    session: Session,
    cloud_run: CloudRun,
    *,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    refs = {
        "diff": store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="diff",
            content="diff --git a/AI_SCDC.md b/AI_SCDC.md\n+phase 12d\n",
            content_type="text/x-diff",
            expires_at=expires_at,
        ),
        "log": store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="log",
            content="worker started\nworker completed\n",
            content_type="text/plain",
            expires_at=expires_at,
        ),
        "command_result": store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="command_result",
            content='{"command":"pytest -q","exit_code":0}',
            content_type="application/json",
            expires_at=expires_at,
        ),
        "test_result": store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="test_result",
            content='{"status":"passed","total":1}',
            content_type="application/json",
            expires_at=expires_at,
        ),
        "manifest": store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="manifest",
            content='{"version":1,"source":"worker"}',
            content_type="application/json",
            expires_at=expires_at,
        ),
    }
    manifest_ref = refs["manifest"]
    log_ref = refs["log"]
    cloud_run.artifact_manifest_uri = manifest_ref.uri
    cloud_run.artifact_manifest_sha256 = manifest_ref.sha256
    cloud_run.artifact_manifest_size_bytes = manifest_ref.size_bytes
    cloud_run.artifact_manifest_content_type = manifest_ref.content_type
    cloud_run.log_stream_uri = log_ref.uri
    cloud_run.log_stream_sha256 = log_ref.sha256
    cloud_run.log_stream_size_bytes = log_ref.size_bytes
    cloud_run.log_stream_content_type = log_ref.content_type
    session.add(cloud_run)
    session.commit()
    return refs
```

Append these tests near the Phase 12 log-window tests:

```python
def test_cloud_run_artifact_manifest_lists_manifest_diff_log_command_and_test_refs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    expires_at = datetime(2026, 6, 13, tzinfo=timezone.utc)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        attach_phase_12d_artifacts(session, cloud_run, expires_at=expires_at)

    manifest_response = client.get(
        f"/cloud-runs/{queued['id']}/artifacts/manifest"
    )
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["version"] == 1
    assert manifest["cloud_run_id"] == queued["id"]
    assert manifest["workspace_id"] == "dev_workspace"
    assert manifest["retention"]["policy"] == "development_default"
    assert manifest["retention"]["cleanup_supported"] is True

    artifacts_by_kind = {
        artifact["kind"]: artifact
        for artifact in manifest["artifacts"]
    }
    assert set(artifacts_by_kind) == {
        "diff",
        "log",
        "command_result",
        "test_result",
        "manifest",
    }
    diff_artifact = artifacts_by_kind["diff"]
    assert diff_artifact["id"].startswith("diff_")
    assert diff_artifact["provider"] == "local_inline"
    assert diff_artifact["uri"].startswith("local-inline://cloud-run-objects/")
    assert diff_artifact["redacted_uri"] == diff_artifact["uri"]
    assert diff_artifact["content_type"] == "text/x-diff"
    assert diff_artifact["download_url"] == (
        f"/cloud-runs/{queued['id']}/artifacts/{diff_artifact['id']}/content"
    )
    assert "diff --git" not in str(manifest)

    list_response = client.get(f"/cloud-runs/{queued['id']}/artifacts")
    assert list_response.status_code == 200
    assert list_response.json() == manifest["artifacts"]

    detail_response = client.get(
        f"/cloud-runs/{queued['id']}/artifacts/{diff_artifact['id']}"
    )
    assert detail_response.status_code == 200
    assert detail_response.json() == diff_artifact


def test_cloud_run_artifact_content_reads_text_and_rejects_other_run_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, first_task = create_cloud_task(session)
        second_task = Task(
            project_id=first_task.project_id,
            title="Second cloud task",
            role_required="backend",
            status=TaskStatus.CREATED,
            allowed_paths=["AI_SCDC_SECOND.md"],
            required_tests=["python -V"],
        )
        session.add(second_task)
        session.commit()
        first_task_id = first_task.id
        second_task_id = second_task.id
        repo_id = repository.id

    first_run = client.post(
        f"/tasks/{first_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    second_run = client.post(
        f"/tasks/{second_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        first_cloud_run = session.get(CloudRun, first_run["id"])
        second_cloud_run = session.get(CloudRun, second_run["id"])
        assert first_cloud_run is not None
        assert second_cloud_run is not None
        attach_phase_12d_artifacts(session, first_cloud_run)
        attach_phase_12d_artifacts(session, second_cloud_run)

    first_manifest = client.get(
        f"/cloud-runs/{first_run['id']}/artifacts/manifest"
    ).json()
    second_manifest = client.get(
        f"/cloud-runs/{second_run['id']}/artifacts/manifest"
    ).json()
    first_diff = next(
        artifact
        for artifact in first_manifest["artifacts"]
        if artifact["kind"] == "diff"
    )
    second_diff = next(
        artifact
        for artifact in second_manifest["artifacts"]
        if artifact["kind"] == "diff"
    )

    content_response = client.get(
        f"/cloud-runs/{first_run['id']}/artifacts/{first_diff['id']}/content"
    )
    assert content_response.status_code == 200
    assert content_response.json()["content"].startswith("diff --git")
    assert content_response.json()["artifact"] == first_diff

    wrong_run_response = client.get(
        f"/cloud-runs/{first_run['id']}/artifacts/{second_diff['id']}/content"
    )
    assert wrong_run_response.status_code == 404
    assert wrong_run_response.json()["detail"] == "Cloud run artifact not found"


def test_cloud_run_artifact_content_rejects_integrity_mismatch(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        attach_phase_12d_artifacts(session, cloud_run)

    diff_artifact = next(
        artifact
        for artifact in client.get(
            f"/cloud-runs/{queued['id']}/artifacts/manifest"
        ).json()["artifacts"]
        if artifact["kind"] == "diff"
    )
    stored_object_id = diff_artifact["uri"].removeprefix(
        "local-inline://cloud-run-objects/"
    )
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        stored_object = session.get(CloudRunStoredObject, stored_object_id)
        assert stored_object is not None
        stored_object.text_content = "tampered"
        session.add(stored_object)
        session.commit()

    response = client.get(
        f"/cloud-runs/{queued['id']}/artifacts/{diff_artifact['id']}/content"
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Object storage content sha256 mismatch"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_content"
```

Expected output:

```text
FAILED apps/api/tests/test_cloud_run_api.py::test_cloud_run_artifact_manifest_lists_manifest_diff_log_command_and_test_refs
FAILED apps/api/tests/test_cloud_run_api.py::test_cloud_run_artifact_content_reads_text_and_rejects_other_run_artifact
FAILED apps/api/tests/test_cloud_run_api.py::test_cloud_run_artifact_content_rejects_integrity_mismatch
```

The failures should be `404 Not Found` for the new routes after Task 1 has passed.

- [ ] **Step 3: Add artifact read schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add these models after `CloudRunLogWindowRead` and before `CloudRunResultRead`:

```python
ArtifactKind = Literal["diff", "log", "command_result", "test_result", "manifest"]


class ArtifactRetentionRead(BaseModel):
    policy: str
    expires_at: datetime | None
    cleanup_supported: bool


class CloudRunArtifactDescriptorRead(BaseModel):
    id: str
    cloud_run_id: str
    kind: ArtifactKind
    label: str
    provider: str
    uri: str
    redacted_uri: str
    sha256: str
    size_bytes: int
    content_type: str
    created_at: datetime | None = None
    expires_at: datetime | None = None
    retention_policy: str | None = None
    download_url: str


class CloudRunArtifactManifestRead(BaseModel):
    version: int
    cloud_run_id: str
    workspace_id: str
    generated_at: datetime
    retention: ArtifactRetentionRead
    artifacts: list[CloudRunArtifactDescriptorRead]


class CloudRunArtifactContentRead(BaseModel):
    artifact: CloudRunArtifactDescriptorRead
    content: str
```

Modify `CloudRunArtifactRefCreate` and `CloudRunArtifactUploadCreate` to reuse `ArtifactKind`:

```python
class CloudRunArtifactRefCreate(BaseModel):
    kind: ArtifactKind
    uri: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    content_type: str = "text/plain"


class CloudRunArtifactUploadCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    callback_token: str | None = Field(default=None, min_length=1)
    kind: ArtifactKind
    content: str = Field(max_length=2 * 1024 * 1024)
    content_type: str = "text/plain"
```

- [ ] **Step 4: Create the artifact-plane service**

Create `apps/api/app/ai_company_api/services/artifact_plane.py` with this implementation:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import CloudRun, CloudRunStoredObject, PatchArtifact
from ai_company_api.schemas.api import (
    ArtifactRetentionRead,
    CloudRunArtifactContentRead,
    CloudRunArtifactDescriptorRead,
    CloudRunArtifactManifestRead,
)
from ai_company_api.services.aliyun_config import AliyunConfigurationError
from ai_company_api.services.object_storage import (
    ObjectStorageProviderNotFound,
    ObjectStorageReadError,
    ObjectStorageRef,
    get_object_storage_provider,
)


TEXT_CONTENT_TYPES = {
    "application/json",
    "application/x-ndjson",
    "text/plain",
    "text/x-diff",
}


@dataclass(frozen=True)
class ResolvedArtifact:
    descriptor: CloudRunArtifactDescriptorRead
    ref: ObjectStorageRef | None
    stored_object_id: str | None = None
    patch_artifact_id: str | None = None


def build_cloud_run_artifact_manifest(
    session: Session,
    *,
    cloud_run_id: str,
) -> CloudRunArtifactManifestRead:
    cloud_run = _get_cloud_run(session, cloud_run_id)
    artifacts = _resolved_artifacts(session, cloud_run)
    return CloudRunArtifactManifestRead(
        version=1,
        cloud_run_id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        generated_at=datetime.now(timezone.utc),
        retention=_manifest_retention(artifacts),
        artifacts=[artifact.descriptor for artifact in artifacts],
    )


def list_cloud_run_artifacts(
    session: Session,
    *,
    cloud_run_id: str,
) -> list[CloudRunArtifactDescriptorRead]:
    return build_cloud_run_artifact_manifest(
        session,
        cloud_run_id=cloud_run_id,
    ).artifacts


def get_cloud_run_artifact_descriptor(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> CloudRunArtifactDescriptorRead:
    return resolve_cloud_run_artifact(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    ).descriptor


def read_cloud_run_artifact_content(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> CloudRunArtifactContentRead:
    resolved = resolve_cloud_run_artifact(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    )
    if not _is_text_content_type(resolved.descriptor.content_type):
        raise HTTPException(
            status_code=400,
            detail="Cloud run artifact content type is not text readable",
        )
    _raise_if_expired(resolved.descriptor.expires_at)

    if resolved.patch_artifact_id is not None:
        content = _read_patch_artifact_content(
            session,
            cloud_run_id=cloud_run_id,
            patch_artifact_id=resolved.patch_artifact_id,
        )
        return CloudRunArtifactContentRead(
            artifact=resolved.descriptor,
            content=content,
        )

    if resolved.ref is None:
        raise HTTPException(status_code=400, detail="Cloud run artifact ref missing")

    if resolved.stored_object_id is not None:
        cloud_run = _get_cloud_run(session, cloud_run_id)
        _validate_stored_object_scope(
            session,
            stored_object_id=resolved.stored_object_id,
            cloud_run_id=cloud_run_id,
            workspace_id=cloud_run.workspace_id,
        )

    try:
        content = get_object_storage_provider(
            resolved.descriptor.provider
        ).read_text(session, resolved.ref)
    except (ObjectStorageProviderNotFound, ObjectStorageReadError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AliyunConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return CloudRunArtifactContentRead(
        artifact=resolved.descriptor,
        content=content,
    )


def resolve_cloud_run_artifact(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> ResolvedArtifact:
    cloud_run = _get_cloud_run(session, cloud_run_id)
    for artifact in _resolved_artifacts(session, cloud_run):
        if artifact.descriptor.id == artifact_id:
            return artifact
    raise HTTPException(status_code=404, detail="Cloud run artifact not found")


def _get_cloud_run(session: Session, cloud_run_id: str) -> CloudRun:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")
    return cloud_run


def _resolved_artifacts(
    session: Session,
    cloud_run: CloudRun,
) -> list[ResolvedArtifact]:
    resolved: list[ResolvedArtifact] = []

    stored_objects = session.exec(
        select(CloudRunStoredObject)
        .where(CloudRunStoredObject.cloud_run_id == cloud_run.id)
        .where(CloudRunStoredObject.workspace_id == cloud_run.workspace_id)
        .order_by(
            CloudRunStoredObject.created_at,
            CloudRunStoredObject.kind,
            CloudRunStoredObject.id,
        )
    ).all()
    for stored_object in stored_objects:
        resolved.append(_resolved_from_stored_object(cloud_run, stored_object))

    known_ref_keys = {
        (artifact.ref.kind, artifact.ref.uri, artifact.ref.sha256)
        for artifact in resolved
        if artifact.ref is not None
    }
    for ref in _cloud_run_metadata_refs(cloud_run):
        ref_key = (ref.kind, ref.uri, ref.sha256)
        if ref_key not in known_ref_keys:
            resolved.append(_resolved_from_ref(cloud_run, ref))
            known_ref_keys.add(ref_key)

    if not any(artifact.descriptor.kind == "diff" for artifact in resolved):
        patch_artifact = _patch_artifact_for_cloud_run(session, cloud_run)
        if patch_artifact is not None:
            resolved.append(_resolved_from_patch_artifact(cloud_run, patch_artifact))

    return resolved


def _resolved_from_stored_object(
    cloud_run: CloudRun,
    stored_object: CloudRunStoredObject,
) -> ResolvedArtifact:
    ref = ObjectStorageRef(
        kind=stored_object.kind,
        uri=stored_object.uri,
        sha256=stored_object.sha256,
        size_bytes=stored_object.size_bytes,
        content_type=stored_object.content_type,
    )
    return ResolvedArtifact(
        descriptor=_descriptor_from_ref(
            cloud_run,
            ref,
            created_at=stored_object.created_at,
            expires_at=stored_object.expires_at,
            retention_policy=stored_object.retention_policy,
        ),
        ref=ref,
        stored_object_id=stored_object.id,
    )


def _resolved_from_ref(
    cloud_run: CloudRun,
    ref: ObjectStorageRef,
) -> ResolvedArtifact:
    return ResolvedArtifact(
        descriptor=_descriptor_from_ref(
            cloud_run,
            ref,
            created_at=cloud_run.updated_at,
            expires_at=None,
            retention_policy=None,
        ),
        ref=ref,
    )


def _resolved_from_patch_artifact(
    cloud_run: CloudRun,
    patch_artifact: PatchArtifact,
) -> ResolvedArtifact:
    content_bytes = patch_artifact.diff_text.encode("utf-8")
    digest = sha256(content_bytes).hexdigest()
    uri = f"patch-artifact://{patch_artifact.id}/diff"
    descriptor = CloudRunArtifactDescriptorRead(
        id=_artifact_id(
            cloud_run_id=cloud_run.id,
            kind="diff",
            uri=uri,
            digest=digest,
        ),
        cloud_run_id=cloud_run.id,
        kind="diff",
        label=_artifact_label("diff"),
        provider="patch_artifact",
        uri=uri,
        redacted_uri=uri,
        sha256=digest,
        size_bytes=len(content_bytes),
        content_type="text/x-diff",
        created_at=patch_artifact.created_at,
        expires_at=None,
        retention_policy=None,
        download_url="",
    )
    descriptor.download_url = _download_url(cloud_run.id, descriptor.id)
    return ResolvedArtifact(
        descriptor=descriptor,
        ref=None,
        patch_artifact_id=patch_artifact.id,
    )


def _cloud_run_metadata_refs(cloud_run: CloudRun) -> list[ObjectStorageRef]:
    refs: list[ObjectStorageRef] = []
    if (
        cloud_run.artifact_manifest_uri is not None
        and cloud_run.artifact_manifest_sha256 is not None
        and cloud_run.artifact_manifest_size_bytes is not None
        and cloud_run.artifact_manifest_content_type is not None
    ):
        refs.append(
            ObjectStorageRef(
                kind="manifest",
                uri=cloud_run.artifact_manifest_uri,
                sha256=cloud_run.artifact_manifest_sha256,
                size_bytes=cloud_run.artifact_manifest_size_bytes,
                content_type=cloud_run.artifact_manifest_content_type,
            )
        )
    if (
        cloud_run.log_stream_uri is not None
        and cloud_run.log_stream_sha256 is not None
        and cloud_run.log_stream_size_bytes is not None
        and cloud_run.log_stream_content_type is not None
    ):
        refs.append(
            ObjectStorageRef(
                kind="log",
                uri=cloud_run.log_stream_uri,
                sha256=cloud_run.log_stream_sha256,
                size_bytes=cloud_run.log_stream_size_bytes,
                content_type=cloud_run.log_stream_content_type,
            )
        )
    return refs


def _descriptor_from_ref(
    cloud_run: CloudRun,
    ref: ObjectStorageRef,
    *,
    created_at: datetime | None,
    expires_at: datetime | None,
    retention_policy: str | None,
) -> CloudRunArtifactDescriptorRead:
    redacted_uri = redact_artifact_uri(ref.uri)
    artifact_id = _artifact_id(
        cloud_run_id=cloud_run.id,
        kind=ref.kind,
        uri=redacted_uri,
        digest=ref.sha256,
    )
    return CloudRunArtifactDescriptorRead(
        id=artifact_id,
        cloud_run_id=cloud_run.id,
        kind=ref.kind,
        label=_artifact_label(ref.kind),
        provider=_provider_from_uri(ref.uri),
        uri=redacted_uri,
        redacted_uri=redacted_uri,
        sha256=ref.sha256,
        size_bytes=ref.size_bytes,
        content_type=ref.content_type,
        created_at=created_at,
        expires_at=expires_at,
        retention_policy=retention_policy,
        download_url=_download_url(cloud_run.id, artifact_id),
    )


def _patch_artifact_for_cloud_run(
    session: Session,
    cloud_run: CloudRun,
) -> PatchArtifact | None:
    if cloud_run.patch_artifact_id is None:
        return None
    patch_artifact = session.get(PatchArtifact, cloud_run.patch_artifact_id)
    if patch_artifact is None:
        return None
    if patch_artifact.workspace_id != cloud_run.workspace_id:
        return None
    if patch_artifact.diff_text == "":
        return None
    return patch_artifact


def _read_patch_artifact_content(
    session: Session,
    *,
    cloud_run_id: str,
    patch_artifact_id: str,
) -> str:
    cloud_run = _get_cloud_run(session, cloud_run_id)
    patch_artifact = session.get(PatchArtifact, patch_artifact_id)
    if (
        patch_artifact is None
        or patch_artifact.workspace_id != cloud_run.workspace_id
        or cloud_run.patch_artifact_id != patch_artifact.id
    ):
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    return patch_artifact.diff_text


def _validate_stored_object_scope(
    session: Session,
    *,
    stored_object_id: str,
    cloud_run_id: str,
    workspace_id: str,
) -> None:
    stored_object = session.get(CloudRunStoredObject, stored_object_id)
    if stored_object is None:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    if stored_object.cloud_run_id != cloud_run_id:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    cloud_run = _get_cloud_run(session, cloud_run_id)
    if stored_object.workspace_id != cloud_run.workspace_id:
        raise HTTPException(status_code=404, detail="Cloud run artifact not found")
    _raise_if_expired(stored_object.expires_at)


def _manifest_retention(
    artifacts: list[ResolvedArtifact],
) -> ArtifactRetentionRead:
    expires_at_values = [
        artifact.descriptor.expires_at
        for artifact in artifacts
        if artifact.descriptor.expires_at is not None
    ]
    policies = [
        artifact.descriptor.retention_policy
        for artifact in artifacts
        if artifact.descriptor.retention_policy
    ]
    cleanup_supported = any(
        artifact.descriptor.provider == "local_inline"
        for artifact in artifacts
    )
    return ArtifactRetentionRead(
        policy=policies[0] if policies else "unspecified",
        expires_at=min(expires_at_values) if expires_at_values else None,
        cleanup_supported=cleanup_supported,
    )


def _artifact_id(
    *,
    cloud_run_id: str,
    kind: str,
    uri: str,
    digest: str,
) -> str:
    raw = f"{cloud_run_id}:{kind}:{uri}:{digest}".encode("utf-8")
    return f"{kind}_{sha256(raw).hexdigest()[:24]}"


def _download_url(cloud_run_id: str, artifact_id: str) -> str:
    encoded_artifact_id = quote(artifact_id, safe="")
    return f"/cloud-runs/{cloud_run_id}/artifacts/{encoded_artifact_id}/content"


def _artifact_label(kind: str) -> str:
    labels = {
        "diff": "Unified diff",
        "log": "Log stream",
        "command_result": "Command result",
        "test_result": "Test result",
        "manifest": "Artifact manifest",
    }
    return labels.get(kind, kind.replace("_", " ").title())


def _provider_from_uri(uri: str) -> str:
    scheme = urlsplit(uri).scheme
    if scheme == "local-inline":
        return "local_inline"
    if scheme == "oss":
        return "aliyun_oss"
    if scheme == "patch-artifact":
        return "patch_artifact"
    return "unknown"


def _is_text_content_type(content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type.startswith("text/") or media_type in TEXT_CONTENT_TYPES


def _raise_if_expired(expires_at: datetime | None) -> None:
    if expires_at is None:
        return
    normalized = expires_at
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    if normalized <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Cloud run artifact expired")


def redact_artifact_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
```

- [ ] **Step 5: Add manifest, list, detail, and content routes**

In `apps/api/app/ai_company_api/api/routes.py`, add these schema imports to the existing `from ai_company_api.schemas.api import` block:

```python
    CloudRunArtifactContentRead,
    CloudRunArtifactDescriptorRead,
    CloudRunArtifactManifestRead,
```

Add these service imports:

```python
from ai_company_api.services.artifact_plane import (
    build_cloud_run_artifact_manifest,
    get_cloud_run_artifact_descriptor,
    list_cloud_run_artifacts,
    read_cloud_run_artifact_content,
)
```

Add these routes after `post_cloud_run_cancel` and before `get_cloud_run_log_window`:

```python
@router.get(
    "/cloud-runs/{cloud_run_id}/artifacts/manifest",
    response_model=CloudRunArtifactManifestRead,
)
def get_cloud_run_artifact_manifest(
    cloud_run_id: str,
    session: SessionDep,
) -> CloudRunArtifactManifestRead:
    return build_cloud_run_artifact_manifest(session, cloud_run_id=cloud_run_id)


@router.get(
    "/cloud-runs/{cloud_run_id}/artifacts",
    response_model=list[CloudRunArtifactDescriptorRead],
)
def get_cloud_run_artifacts(
    cloud_run_id: str,
    session: SessionDep,
) -> list[CloudRunArtifactDescriptorRead]:
    return list_cloud_run_artifacts(session, cloud_run_id=cloud_run_id)


@router.get(
    "/cloud-runs/{cloud_run_id}/artifacts/{artifact_id}",
    response_model=CloudRunArtifactDescriptorRead,
)
def get_cloud_run_artifact(
    cloud_run_id: str,
    artifact_id: str,
    session: SessionDep,
) -> CloudRunArtifactDescriptorRead:
    return get_cloud_run_artifact_descriptor(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    )


@router.get(
    "/cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/content",
    response_model=CloudRunArtifactContentRead,
)
def get_cloud_run_artifact_content(
    cloud_run_id: str,
    artifact_id: str,
    session: SessionDep,
) -> CloudRunArtifactContentRead:
    return read_cloud_run_artifact_content(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    )
```

- [ ] **Step 6: Run manifest and content tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_content"
```

Expected output:

```text
3 passed
```

- [ ] **Step 7: Run focused log-window regression tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "log_window"
```

Expected output:

```text
passed
```

The exact count can change as the file grows; the command must not fail.

- [ ] **Step 8: Commit Task 2**

Run:

```bash
git add apps/api/tests/test_cloud_run_api.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/artifact_plane.py apps/api/app/ai_company_api/api/routes.py
git commit -m "Add cloud run artifact manifest APIs"
```

Expected output:

```text
commit summary includes "Add cloud run artifact manifest APIs"
```

---

### Task 3: Provider-Neutral Download Descriptors And Cleanup

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/artifact_plane.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`

- [ ] **Step 1: Write failing API tests for external redaction, download descriptors, and cleanup**

Append these tests near the Task 2 artifact tests in `apps/api/tests/test_cloud_run_api.py`:

```python
def test_cloud_run_artifact_external_refs_are_redacted_and_download_is_local(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        cloud_run.artifact_manifest_uri = (
            "oss://ai-scdc-dev-artifacts/workspaces/dev_workspace/"
            "cloud-runs/cloud_run/artifacts/manifest.json?signature=secret#frag"
        )
        cloud_run.artifact_manifest_sha256 = "a" * 64
        cloud_run.artifact_manifest_size_bytes = 41
        cloud_run.artifact_manifest_content_type = "application/json"
        session.add(cloud_run)
        session.commit()

    manifest_response = client.get(
        f"/cloud-runs/{queued['id']}/artifacts/manifest"
    )
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    manifest_artifact = manifest["artifacts"][0]
    assert manifest_artifact["kind"] == "manifest"
    assert manifest_artifact["provider"] == "aliyun_oss"
    assert "signature=secret" not in str(manifest)
    assert "#frag" not in str(manifest)
    assert manifest_artifact["uri"].endswith("/manifest.json")
    assert manifest_artifact["redacted_uri"] == manifest_artifact["uri"]

    download_response = client.post(
        f"/cloud-runs/{queued['id']}/artifacts/{manifest_artifact['id']}/download"
    )
    assert download_response.status_code == 200
    download = download_response.json()
    assert download["artifact"] == manifest_artifact
    assert download["download_url"] == (
        f"/cloud-runs/{queued['id']}/artifacts/"
        f"{manifest_artifact['id']}/content"
    )
    assert download["sha256"] == "a" * 64
    assert download["size_bytes"] == 41
    assert "signature=secret" not in str(download)
    assert "https://" not in str(download)


def test_cloud_run_artifact_cleanup_expired_deletes_local_and_reports_external(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    expired_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        local_ref = store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="log",
            content="expired local log",
            expires_at=expired_at,
            retention_policy="development_default",
        )
        external_object = CloudRunStoredObject(
            workspace_id=cloud_run.workspace_id,
            cloud_run_id=cloud_run.id,
            kind="manifest",
            uri=(
                "oss://ai-scdc-dev-artifacts/workspaces/dev_workspace/"
                "cloud-runs/cloud_run/manifest.json?signature=secret#frag"
            ),
            sha256="b" * 64,
            size_bytes=17,
            content_type="application/json",
            text_content="",
            expires_at=expired_at,
            retention_policy="oss_lifecycle",
        )
        session.add(external_object)
        session.commit()
        local_object_id = local_ref.uri.removeprefix(
            "local-inline://cloud-run-objects/"
        )
        external_object_id = external_object.id

    response = client.post(
        "/cloud-runs/artifacts/cleanup-expired",
        json={
            "before": "2026-06-06T00:00:00+00:00",
            "limit": 10,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["deleted_count"] == 1
    assert body["lifecycle_only_count"] == 1
    actions = {item["action"] for item in body["items"]}
    assert actions == {"deleted", "lifecycle_only"}
    assert "signature=secret" not in str(body)
    lifecycle_item = next(
        item
        for item in body["items"]
        if item["action"] == "lifecycle_only"
    )
    assert lifecycle_item["provider"] == "aliyun_oss"
    assert lifecycle_item["reason"] == "external_provider_cleanup_not_supported_by_api"

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        assert session.get(CloudRunStoredObject, local_object_id) is None
        assert session.get(CloudRunStoredObject, external_object_id) is not None


def test_cloud_run_artifact_content_returns_gone_for_expired_local_object(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        store_cloud_run_artifact_ref(
            session,
            cloud_run,
            kind="diff",
            content="diff --git a/expired b/expired\n+expired\n",
            content_type="text/x-diff",
            expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            retention_policy="development_default",
        )
        session.commit()

    manifest = client.get(
        f"/cloud-runs/{queued['id']}/artifacts/manifest"
    ).json()
    diff_artifact = next(
        artifact
        for artifact in manifest["artifacts"]
        if artifact["kind"] == "diff"
    )

    response = client.get(
        f"/cloud-runs/{queued['id']}/artifacts/{diff_artifact['id']}/content"
    )
    assert response.status_code == 410
    assert response.json()["detail"] == "Cloud run artifact expired"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "download_is_local or cleanup_expired or returns_gone"
```

Expected output:

```text
FAILED apps/api/tests/test_cloud_run_api.py::test_cloud_run_artifact_external_refs_are_redacted_and_download_is_local
FAILED apps/api/tests/test_cloud_run_api.py::test_cloud_run_artifact_cleanup_expired_deletes_local_and_reports_external
```

The expired-content test can pass after Task 2 if expiration was already implemented; the download and cleanup routes should fail until this task is implemented.

- [ ] **Step 3: Add download and cleanup schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add these models after `CloudRunArtifactContentRead`:

```python
class CloudRunArtifactDownloadRead(BaseModel):
    artifact: CloudRunArtifactDescriptorRead
    download_url: str
    expires_at: datetime | None
    content_type: str
    size_bytes: int
    sha256: str


class CloudRunArtifactCleanupRequest(BaseModel):
    before: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1000)


class CloudRunArtifactCleanupItemRead(BaseModel):
    artifact_id: str
    cloud_run_id: str
    provider: str
    action: Literal["deleted", "lifecycle_only"]
    redacted_uri: str
    reason: str


class CloudRunArtifactCleanupResultRead(BaseModel):
    before: datetime
    deleted_count: int
    lifecycle_only_count: int
    items: list[CloudRunArtifactCleanupItemRead]
```

- [ ] **Step 4: Add download and cleanup service functions**

In `apps/api/app/ai_company_api/services/artifact_plane.py`, add these schema imports:

```python
    CloudRunArtifactCleanupItemRead,
    CloudRunArtifactCleanupRequest,
    CloudRunArtifactCleanupResultRead,
    CloudRunArtifactDownloadRead,
```

Add these service functions after `read_cloud_run_artifact_content`:

```python
def build_cloud_run_artifact_download(
    session: Session,
    *,
    cloud_run_id: str,
    artifact_id: str,
) -> CloudRunArtifactDownloadRead:
    resolved = resolve_cloud_run_artifact(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    )
    return CloudRunArtifactDownloadRead(
        artifact=resolved.descriptor,
        download_url=resolved.descriptor.download_url,
        expires_at=resolved.descriptor.expires_at,
        content_type=resolved.descriptor.content_type,
        size_bytes=resolved.descriptor.size_bytes,
        sha256=resolved.descriptor.sha256,
    )


def cleanup_expired_cloud_run_artifacts(
    session: Session,
    *,
    request: CloudRunArtifactCleanupRequest,
) -> CloudRunArtifactCleanupResultRead:
    before = request.before or datetime.now(timezone.utc)
    normalized_before = before
    if normalized_before.tzinfo is None:
        normalized_before = normalized_before.replace(tzinfo=timezone.utc)

    stored_objects = session.exec(
        select(CloudRunStoredObject)
        .where(CloudRunStoredObject.expires_at.is_not(None))
        .where(CloudRunStoredObject.expires_at <= normalized_before)
        .order_by(CloudRunStoredObject.expires_at, CloudRunStoredObject.id)
        .limit(request.limit)
    ).all()

    items: list[CloudRunArtifactCleanupItemRead] = []
    deleted_count = 0
    lifecycle_only_count = 0
    for stored_object in stored_objects:
        provider = _provider_from_uri(stored_object.uri)
        artifact_id = _artifact_id(
            cloud_run_id=stored_object.cloud_run_id,
            kind=stored_object.kind,
            uri=redact_artifact_uri(stored_object.uri),
            digest=stored_object.sha256,
        )
        if provider == "local_inline":
            items.append(
                CloudRunArtifactCleanupItemRead(
                    artifact_id=artifact_id,
                    cloud_run_id=stored_object.cloud_run_id,
                    provider=provider,
                    action="deleted",
                    redacted_uri=redact_artifact_uri(stored_object.uri),
                    reason="local_inline_expired",
                )
            )
            session.delete(stored_object)
            deleted_count += 1
            continue

        items.append(
            CloudRunArtifactCleanupItemRead(
                artifact_id=artifact_id,
                cloud_run_id=stored_object.cloud_run_id,
                provider=provider,
                action="lifecycle_only",
                redacted_uri=redact_artifact_uri(stored_object.uri),
                reason="external_provider_cleanup_not_supported_by_api",
            )
        )
        lifecycle_only_count += 1

    session.commit()
    return CloudRunArtifactCleanupResultRead(
        before=normalized_before,
        deleted_count=deleted_count,
        lifecycle_only_count=lifecycle_only_count,
        items=items,
    )
```

- [ ] **Step 5: Add download and cleanup routes**

In `apps/api/app/ai_company_api/api/routes.py`, add these schema imports:

```python
    CloudRunArtifactCleanupRequest,
    CloudRunArtifactCleanupResultRead,
    CloudRunArtifactDownloadRead,
```

Add these service imports:

```python
    build_cloud_run_artifact_download,
    cleanup_expired_cloud_run_artifacts,
```

Add these routes after `get_cloud_run_artifact_content`:

```python
@router.post(
    "/cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/download",
    response_model=CloudRunArtifactDownloadRead,
)
def post_cloud_run_artifact_download(
    cloud_run_id: str,
    artifact_id: str,
    session: SessionDep,
) -> CloudRunArtifactDownloadRead:
    return build_cloud_run_artifact_download(
        session,
        cloud_run_id=cloud_run_id,
        artifact_id=artifact_id,
    )


@router.post(
    "/cloud-runs/artifacts/cleanup-expired",
    response_model=CloudRunArtifactCleanupResultRead,
)
def post_cloud_run_artifact_cleanup_expired(
    data: CloudRunArtifactCleanupRequest,
    session: SessionDep,
) -> CloudRunArtifactCleanupResultRead:
    return cleanup_expired_cloud_run_artifacts(session, request=data)
```

- [ ] **Step 6: Run download and cleanup tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "download_is_local or cleanup_expired or returns_gone"
```

Expected output:

```text
3 passed
```

- [ ] **Step 7: Run artifact API test set**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_content or download_is_local or cleanup_expired or returns_gone"
```

Expected output:

```text
6 passed
```

- [ ] **Step 8: Commit Task 3**

Run:

```bash
git add apps/api/tests/test_cloud_run_api.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/artifact_plane.py apps/api/app/ai_company_api/api/routes.py
git commit -m "Add artifact download and cleanup APIs"
```

Expected output:

```text
commit summary includes "Add artifact download and cleanup APIs"
```

---

### Task 4: Desktop Artifact Client And Browser

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
- Modify: `apps/desktop/src/styles/app.css`
- Modify: `apps/desktop/src/test/client.test.ts`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Write failing desktop API client tests**

In `apps/desktop/src/test/client.test.ts`, append these tests near the cloud-run client tests:

```typescript
  it("fake client returns deterministic cloud run artifacts and content", async () => {
    const queued = await fakeApiClient.startCloudRun("task_artifact_demo");
    await fakeApiClient.processCloudRun(queued.cloud_run.id);

    const manifest = await fakeApiClient.getCloudRunArtifactManifest(queued.cloud_run.id);
    expect(manifest).toMatchObject({
      version: 1,
      cloud_run_id: queued.cloud_run.id,
      retention: {
        policy: "development_default",
        cleanup_supported: true
      }
    });
    expect(manifest.artifacts.map((artifact) => artifact.kind)).toEqual([
      "diff",
      "log",
      "command_result",
      "test_result",
      "manifest"
    ]);

    const diff = manifest.artifacts.find((artifact) => artifact.kind === "diff")!;
    await expect(
      fakeApiClient.getCloudRunArtifactContent(queued.cloud_run.id, diff.id)
    ).resolves.toMatchObject({
      artifact: diff,
      content: expect.stringContaining("diff --git")
    });
  });

  it("HTTP client maps cloud run artifact manifest and content endpoints", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          version: 1,
          cloud_run_id: "cloud_run_api",
          workspace_id: "workspace_api",
          generated_at: "2026-06-06T00:00:00Z",
          retention: {
            policy: "development_default",
            expires_at: "2026-06-13T00:00:00Z",
            cleanup_supported: true
          },
          artifacts: [
            {
              id: "diff_abc",
              cloud_run_id: "cloud_run_api",
              kind: "diff",
              label: "Unified diff",
              provider: "local_inline",
              uri: "local-inline://cloud-run-objects/object",
              redacted_uri: "local-inline://cloud-run-objects/object",
              sha256: "a".repeat(64),
              size_bytes: 12,
              content_type: "text/x-diff",
              created_at: "2026-06-06T00:00:00Z",
              expires_at: "2026-06-13T00:00:00Z",
              retention_policy: "development_default",
              download_url: "/cloud-runs/cloud_run_api/artifacts/diff_abc/content"
            }
          ]
        })
      )
      .mockResolvedValueOnce(
        jsonResponse({
          artifact: {
            id: "diff_abc",
            cloud_run_id: "cloud_run_api",
            kind: "diff",
            label: "Unified diff",
            provider: "local_inline",
            uri: "local-inline://cloud-run-objects/object",
            redacted_uri: "local-inline://cloud-run-objects/object",
            sha256: "a".repeat(64),
            size_bytes: 12,
            content_type: "text/x-diff",
            created_at: "2026-06-06T00:00:00Z",
            expires_at: "2026-06-13T00:00:00Z",
            retention_policy: "development_default",
            download_url: "/cloud-runs/cloud_run_api/artifacts/diff_abc/content"
          },
          content: "diff --git a/file b/file\n+api"
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const manifest = await client.getCloudRunArtifactManifest("cloud_run_api");
    const content = await client.getCloudRunArtifactContent("cloud_run_api", "diff_abc");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://127.0.0.1:8000/cloud-runs/cloud_run_api/artifacts/manifest"
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://127.0.0.1:8000/cloud-runs/cloud_run_api/artifacts/diff_abc/content"
    );
    expect(manifest.artifacts[0].label).toBe("Unified diff");
    expect(content.content).toContain("+api");
  });
```

- [ ] **Step 2: Run client tests and confirm they fail**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected output:

```text
FAIL  src/test/client.test.ts
```

The failure should mention `getCloudRunArtifactManifest` or `getCloudRunArtifactContent` missing from the client type or implementation.

- [ ] **Step 3: Add desktop artifact types and API methods**

In `apps/desktop/src/api/client.ts`, add artifact card types after `CloudRunLogWindowCard`:

```typescript
export type CloudRunArtifactRetentionCard = {
  policy: string;
  expires_at?: string | null;
  cleanup_supported: boolean;
};

export type CloudRunArtifactCard = {
  id: string;
  cloud_run_id: string;
  kind: "diff" | "log" | "command_result" | "test_result" | "manifest";
  label: string;
  provider: string;
  uri: string;
  redacted_uri: string;
  sha256: string;
  size_bytes: number;
  content_type: string;
  created_at?: string | null;
  expires_at?: string | null;
  retention_policy?: string | null;
  download_url: string;
};

export type CloudRunArtifactManifestCard = {
  version: number;
  cloud_run_id: string;
  workspace_id: string;
  generated_at: string;
  retention: CloudRunArtifactRetentionCard;
  artifacts: CloudRunArtifactCard[];
};

export type CloudRunArtifactContentCard = {
  artifact: CloudRunArtifactCard;
  content: string;
};
```

Extend `TaskCard`:

```typescript
  cloud_run_artifact_manifest?: CloudRunArtifactManifestCard;
  cloud_run_artifact_preview?: CloudRunArtifactContentCard;
```

Extend `ConsoleApiClient`:

```typescript
  getCloudRunArtifactManifest: (
    cloudRunId: string
  ) => Promise<CloudRunArtifactManifestCard>;
  getCloudRunArtifactContent: (
    cloudRunId: string,
    artifactId: string
  ) => Promise<CloudRunArtifactContentCard>;
```

Add API aliases near `ApiCloudRunLogWindow`:

```typescript
type ApiCloudRunArtifactManifest = CloudRunArtifactManifestCard;

type ApiCloudRunArtifactContent = CloudRunArtifactContentCard;
```

Add these helpers near the fake cloud-run helpers:

```typescript
function fakeCloudRunArtifactManifest(cloudRunId: string): CloudRunArtifactManifestCard {
  const baseArtifacts: Array<Pick<CloudRunArtifactCard, "kind" | "label" | "content_type">> = [
    { kind: "diff", label: "Unified diff", content_type: "text/x-diff" },
    { kind: "log", label: "Log stream", content_type: "text/plain" },
    { kind: "command_result", label: "Command result", content_type: "application/json" },
    { kind: "test_result", label: "Test result", content_type: "application/json" },
    { kind: "manifest", label: "Artifact manifest", content_type: "application/json" }
  ];
  return {
    version: 1,
    cloud_run_id: cloudRunId,
    workspace_id: "workspace_demo",
    generated_at: "2026-06-06T00:00:00Z",
    retention: {
      policy: "development_default",
      expires_at: "2026-06-13T00:00:00Z",
      cleanup_supported: true
    },
    artifacts: baseArtifacts.map((artifact, index) => {
      const id = `${artifact.kind}_fake_${index}`;
      return {
        id,
        cloud_run_id: cloudRunId,
        kind: artifact.kind,
        label: artifact.label,
        provider: "local_inline",
        uri: `local-inline://cloud-run-objects/${cloudRunId}_${artifact.kind}`,
        redacted_uri: `local-inline://cloud-run-objects/${cloudRunId}_${artifact.kind}`,
        sha256: String(index + 1).repeat(64).slice(0, 64),
        size_bytes: 128 + index,
        content_type: artifact.content_type,
        created_at: "2026-06-06T00:00:00Z",
        expires_at: "2026-06-13T00:00:00Z",
        retention_policy: "development_default",
        download_url: `/cloud-runs/${cloudRunId}/artifacts/${id}/content`
      };
    })
  };
}

function fakeCloudRunArtifactContent(
  cloudRunId: string,
  artifactId: string
): CloudRunArtifactContentCard {
  const manifest = fakeCloudRunArtifactManifest(cloudRunId);
  const artifact =
    manifest.artifacts.find((item) => item.id === artifactId) ?? manifest.artifacts[0];
  const contentByKind: Record<CloudRunArtifactCard["kind"], string> = {
    diff: "diff --git a/README.md b/README.md\n+fake artifact\n",
    log: "Cloud run queued.\nCloud run completed.\n",
    command_result: "{\"command\":\"python -V\",\"exit_code\":0}",
    test_result: "{\"status\":\"passed\",\"total\":1}",
    manifest: "{\"version\":1,\"source\":\"fake\"}"
  };
  return {
    artifact,
    content: contentByKind[artifact.kind]
  };
}
```

Add fake client methods:

```typescript
  async getCloudRunArtifactManifest(cloudRunId: string) {
    return fakeCloudRunArtifactManifest(cloudRunId);
  },
  async getCloudRunArtifactContent(cloudRunId: string, artifactId: string) {
    return fakeCloudRunArtifactContent(cloudRunId, artifactId);
  },
```

Add HTTP client methods near `listCloudRunLogWindow`:

```typescript
    async getCloudRunArtifactManifest(cloudRunId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/cloud-runs/${cloudRunId}/artifacts/manifest`)
      );
      return readJsonResponse<ApiCloudRunArtifactManifest>(
        response,
        `GET /cloud-runs/${cloudRunId}/artifacts/manifest`
      );
    },
    async getCloudRunArtifactContent(cloudRunId: string, artifactId: string) {
      const encodedArtifactId = encodeURIComponent(artifactId);
      const response = await fetch(
        apiUrl(
          options.baseUrl,
          `/cloud-runs/${cloudRunId}/artifacts/${encodedArtifactId}/content`
        )
      );
      return readJsonResponse<ApiCloudRunArtifactContent>(
        response,
        `GET /cloud-runs/${cloudRunId}/artifacts/${artifactId}/content`
      );
    },
```

- [ ] **Step 4: Run client tests**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected output:

```text
PASS  src/test/client.test.ts
```

- [ ] **Step 5: Write failing App artifact browser test**

In `apps/desktop/src/test/App.test.tsx`, extend the import:

```typescript
import type {
  CloudRunArtifactContentCard,
  CloudRunArtifactManifestCard,
  CloudRunCard,
  ConsoleApiClient,
  PlannerRunDraft,
  TaskCard
} from "../api/client";
```

Add these fixtures after `queuedCloudRunFixture`:

```typescript
function cloudRunArtifactManifestFixture(): CloudRunArtifactManifestCard {
  return {
    version: 1,
    cloud_run_id: "cloud_run_test",
    workspace_id: "workspace_test",
    generated_at: "2026-06-06T00:00:00Z",
    retention: {
      policy: "development_default",
      expires_at: "2026-06-13T00:00:00Z",
      cleanup_supported: true
    },
    artifacts: [
      {
        id: "diff_artifact_test",
        cloud_run_id: "cloud_run_test",
        kind: "diff",
        label: "Unified diff",
        provider: "local_inline",
        uri: "local-inline://cloud-run-objects/diff_artifact_test",
        redacted_uri: "local-inline://cloud-run-objects/diff_artifact_test",
        sha256: "a".repeat(64),
        size_bytes: 44,
        content_type: "text/x-diff",
        created_at: "2026-06-06T00:00:00Z",
        expires_at: "2026-06-13T00:00:00Z",
        retention_policy: "development_default",
        download_url: "/cloud-runs/cloud_run_test/artifacts/diff_artifact_test/content"
      },
      {
        id: "log_artifact_test",
        cloud_run_id: "cloud_run_test",
        kind: "log",
        label: "Log stream",
        provider: "local_inline",
        uri: "local-inline://cloud-run-objects/log_artifact_test",
        redacted_uri: "local-inline://cloud-run-objects/log_artifact_test",
        sha256: "b".repeat(64),
        size_bytes: 20,
        content_type: "text/plain",
        created_at: "2026-06-06T00:00:00Z",
        expires_at: "2026-06-13T00:00:00Z",
        retention_policy: "development_default",
        download_url: "/cloud-runs/cloud_run_test/artifacts/log_artifact_test/content"
      }
    ]
  };
}

function cloudRunArtifactContentFixture(): CloudRunArtifactContentCard {
  const artifact = cloudRunArtifactManifestFixture().artifacts[0];
  return {
    artifact,
    content: "diff --git a/README.md b/README.md\n+artifact preview"
  };
}
```

Extend `createMockApiClient` defaults:

```typescript
    getCloudRunArtifactManifest: vi
      .fn()
      .mockResolvedValue(cloudRunArtifactManifestFixture()),
    getCloudRunArtifactContent: vi
      .fn()
      .mockResolvedValue(cloudRunArtifactContentFixture()),
```

Append this test near the cloud-run UI tests:

```typescript
  it("renders cloud run artifacts from manifest and opens text previews", async () => {
    const user = userEvent.setup();
    const task: TaskCard = {
      ...taskCardFixture("Run artifact task"),
      id: "task_cloud"
    };
    const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
      cloud_run: queuedCloudRunFixture(),
      patch_artifact: undefined
    });
    const processCloudRun = vi.fn<ConsoleApiClient["processCloudRun"]>().mockResolvedValue({
      cloud_run: cloudRunFixture(),
      patch_artifact: cloudPatchArtifactFixture()
    });
    const listCloudRunLogs = vi.fn<ConsoleApiClient["listCloudRunLogs"]>().mockResolvedValue([]);
    const getCloudRunArtifactManifest = vi
      .fn<ConsoleApiClient["getCloudRunArtifactManifest"]>()
      .mockResolvedValue(cloudRunArtifactManifestFixture());
    const getCloudRunArtifactContent = vi
      .fn<ConsoleApiClient["getCloudRunArtifactContent"]>()
      .mockResolvedValue(cloudRunArtifactContentFixture());
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([task]),
      startCloudRun,
      processCloudRun,
      listCloudRunLogs,
      getCloudRunArtifactManifest,
      getCloudRunArtifactContent
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Run cloud" }));
    await user.click(await within(board).findByRole("button", { name: "Process" }));

    expect(getCloudRunArtifactManifest).toHaveBeenCalledWith("cloud_run_test");
    expect(await within(board).findByText("Artifacts")).toBeInTheDocument();
    expect(within(board).getByText("development_default")).toBeInTheDocument();
    expect(within(board).getByRole("button", { name: "Unified diff" })).toBeInTheDocument();
    expect(within(board).getByText("Log stream")).toBeInTheDocument();

    await user.click(within(board).getByRole("button", { name: "Unified diff" }));
    expect(getCloudRunArtifactContent).toHaveBeenCalledWith(
      "cloud_run_test",
      "diff_artifact_test"
    );
    expect(await within(board).findByText(/artifact preview/)).toBeInTheDocument();
  });
```

- [ ] **Step 6: Run App test and confirm it fails**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- App.test.tsx
```

Expected output:

```text
FAIL  src/test/App.test.tsx
```

The failure should show missing artifact rendering or missing artifact methods in the mock client.

- [ ] **Step 7: Fetch artifact manifests in App cloud-run flows**

In `apps/desktop/src/App.tsx`, add the artifact types import to the existing client import:

```typescript
import type {
  CloudRunArtifactCard,
  CloudRunArtifactManifestCard,
  TaskCard
} from "./api/client";
```

Add a helper after `refreshCloudRunLogs`:

```typescript
  async function refreshCloudRunArtifacts(
    cloudRunId: string,
    fallbackManifest?: CloudRunArtifactManifestCard
  ) {
    try {
      return await apiClient.getCloudRunArtifactManifest(cloudRunId);
    } catch {
      return fallbackManifest;
    }
  }
```

In `handleStartCloudRun`, fetch artifacts together with logs:

```typescript
      const [cloudRunLogs, cloudRunArtifactManifest] = await Promise.all([
        refreshCloudRunLogs(result.cloud_run.id),
        refreshCloudRunArtifacts(result.cloud_run.id)
      ]);
```

Add the manifest to the task update:

```typescript
                cloud_run: result.cloud_run,
                cloud_run_logs: cloudRunLogs,
                cloud_run_artifact_manifest: cloudRunArtifactManifest
```

In `handleProcessCloudRun`, fetch artifacts together with logs:

```typescript
      const [cloudRunLogs, cloudRunArtifactManifest] = await Promise.all([
        refreshCloudRunLogs(result.cloud_run.id, task.cloud_run_logs),
        refreshCloudRunArtifacts(
          result.cloud_run.id,
          task.cloud_run_artifact_manifest
        )
      ]);
```

Add the manifest to the task update:

```typescript
                cloud_run: result.cloud_run,
                cloud_run_logs: cloudRunLogs,
                cloud_run_artifact_manifest: cloudRunArtifactManifest
```

In `handleCancelCloudRun`, fetch artifacts together with logs:

```typescript
      const [cloudRunLogs, cloudRunArtifactManifest] = await Promise.all([
        refreshCloudRunLogs(cloudRun.id, task.cloud_run_logs),
        refreshCloudRunArtifacts(cloudRun.id, task.cloud_run_artifact_manifest)
      ]);
```

Add the manifest to the task update:

```typescript
                cloud_run: cloudRun,
                cloud_run_logs: cloudRunLogs,
                cloud_run_artifact_manifest: cloudRunArtifactManifest
```

Add this preview handler before `const contextPanel`:

```typescript
  async function handleOpenCloudRunArtifact(
    task: TaskCard,
    artifact: CloudRunArtifactCard
  ) {
    if (!task.cloud_run) {
      return;
    }
    try {
      const content = await apiClient.getCloudRunArtifactContent(
        task.cloud_run.id,
        artifact.id
      );
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...currentTask,
                cloud_run_artifact_preview: content
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to load cloud artifact")
      }));
    }
  }
```

Pass the handler to `TaskBoard`:

```tsx
        onOpenCloudRunArtifact={handleOpenCloudRunArtifact}
```

- [ ] **Step 8: Render artifacts and previews in TaskBoard**

In `apps/desktop/src/components/TaskBoard.tsx`, update the import:

```typescript
import type { CloudRunArtifactCard, TaskCard } from "../api/client";
```

Extend `TaskBoardProps`:

```typescript
  onOpenCloudRunArtifact?: (task: TaskCard, artifact: CloudRunArtifactCard) => void;
```

Add the prop to the function parameters:

```typescript
  onOpenCloudRunArtifact
```

Add this helper inside the component before the `return`:

```typescript
  function artifactMeta(artifact: CloudRunArtifactCard) {
    return `${artifact.kind} | ${artifact.content_type} | ${artifact.size_bytes} bytes`;
  }

  function artifactGroups(artifacts: CloudRunArtifactCard[]) {
    return Object.entries(
      artifacts.reduce<Record<string, CloudRunArtifactCard[]>>((groups, artifact) => {
        return {
          ...groups,
          [artifact.kind]: [...(groups[artifact.kind] ?? []), artifact]
        };
      }, {})
    );
  }
```

Inside the cloud-run metadata `<dl className="task-patch-meta">`, after the cloud logs block, add:

```tsx
                {task.cloud_run_artifact_manifest ? (
                  <div>
                    <dt>Artifacts</dt>
                    <dd>
                      <div className="task-cloud-artifacts">
                        <div className="task-artifact-summary">
                          <span>{task.cloud_run_artifact_manifest.artifacts.length} objects</span>
                          <span>{task.cloud_run_artifact_manifest.retention.policy}</span>
                        </div>
                        <ul className="task-cloud-artifact-groups">
                          {artifactGroups(task.cloud_run_artifact_manifest.artifacts).map(
                            ([kind, artifacts]) => (
                              <li key={kind}>
                                <span className="task-artifact-kind">{kind}</span>
                                <ul className="task-cloud-artifact-list">
                                  {artifacts.map((artifact) => (
                                    <li key={artifact.id}>
                                      {onOpenCloudRunArtifact ? (
                                        <button
                                          type="button"
                                          className="task-artifact-link"
                                          onClick={() => onOpenCloudRunArtifact(task, artifact)}
                                        >
                                          {artifact.label}
                                        </button>
                                      ) : (
                                        <span>{artifact.label}</span>
                                      )}
                                      <span>{artifactMeta(artifact)}</span>
                                      <span>{artifact.redacted_uri}</span>
                                    </li>
                                  ))}
                                </ul>
                              </li>
                            )
                          )}
                        </ul>
                      </div>
                    </dd>
                  </div>
                ) : null}
```

After the existing diff preview block, add:

```tsx
            {task.cloud_run_artifact_preview ? (
              <div className="task-artifact-preview">
                <h4 id={`task-${task.id}-artifact-preview-title`}>
                  {task.cloud_run_artifact_preview.artifact.label}
                </h4>
                <pre
                  aria-labelledby={`task-${task.id}-artifact-preview-title`}
                  role="region"
                  tabIndex={0}
                >
                  {task.cloud_run_artifact_preview.content}
                </pre>
              </div>
            ) : null}
```

- [ ] **Step 9: Add artifact styles**

In `apps/desktop/src/styles/app.css`, add these styles near the task cloud log styles:

```css
.task-cloud-artifacts {
  display: grid;
  gap: 6px;
}

.task-artifact-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: #344047;
  font-size: 12px;
}

.task-cloud-artifact-groups {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.task-artifact-kind {
  display: block;
  color: #344047;
  font-size: 11px;
  font-weight: 700;
}

.task-cloud-artifact-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.task-cloud-artifact-list li {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.task-artifact-link {
  justify-self: start;
  border: 0;
  padding: 0;
  color: #24586a;
  background: transparent;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

.task-artifact-link:focus-visible {
  outline: 2px solid #24586a;
  outline-offset: 2px;
}

.task-cloud-artifact-list span {
  overflow-wrap: anywhere;
  color: #344047;
  font-size: 12px;
}

.task-artifact-preview {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.task-artifact-preview h4 {
  margin: 0;
  color: #344047;
  font-size: 11px;
  line-height: 1.3;
}

.task-artifact-preview pre {
  max-height: 180px;
  margin: 0;
  overflow: auto;
  border: 1px solid #d7dee2;
  border-radius: 6px;
  padding: 8px;
  background: #f8f9fa;
  color: #172026;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre;
}
```

- [ ] **Step 10: Run desktop tests**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- App.test.tsx client.test.ts
```

Expected output:

```text
PASS  src/test/App.test.tsx
PASS  src/test/client.test.ts
```

- [ ] **Step 11: Commit Task 4**

Run:

```bash
git add apps/desktop/src/api/client.ts apps/desktop/src/App.tsx apps/desktop/src/components/TaskBoard.tsx apps/desktop/src/styles/app.css apps/desktop/src/test/client.test.ts apps/desktop/src/test/App.test.tsx
git commit -m "Add desktop artifact browser"
```

Expected output:

```text
commit summary includes "Add desktop artifact browser"
```

---

### Task 5: Documentation, Status, And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Modify: `STATUS.md`

- [ ] **Step 1: Update README Phase list and add smoke commands**

In `README.md`, update the first paragraph so it ends with Phase 12D before Phase 13A:

```markdown
This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, Phase 2 backend-first model routing and BYOK foundation, Phase 3 real planner vertical slice, Phase 4 local runner vertical slice, Phase 5 deterministic test/review/debug workflow, Phase 6 human patch approval and diff viewer workflow, Phase 7 GitHub-only cloud-run and pull-request boundary, Phase 8 Docker local sandbox executor, Phase 9 local cloud-run queue worker boundary, Phase 10A remote worker control-plane contract, Phase 10B provider-neutral remote execution-plane contract, Phase 10C Aliyun provider MVP, Phase 10D run-scoped remote worker callback token hardening, Phase 11 real remote worker execution skeleton, Phase 12A bounded cloud-run log polling and safe remote log-stream reads, Phase 12B optional provider-native log sync, Phase 12C Aliyun MNS pull-worker receipt handling, Phase 12D cloud-run artifact manifest and retention plane, and Phase 13A Aliyun operational hardening for a desktop multi-agent software engineering console.
```

Add this section after the Phase 12A/12B/12C documentation block:

```markdown
### Phase 12D artifact plane smoke

Phase 12D adds a provider-neutral artifact manifest and cleanup seam for cloud runs. Clients can locate diff, log, command-result, test-result, and manifest artifacts without scraping individual cloud-run fields.

```powershell
$base = "http://127.0.0.1:8000"
$cloudRunId = $processedCloudRun.cloud_run.id

$manifest = Invoke-RestMethod -Uri "$base/cloud-runs/$cloudRunId/artifacts/manifest"
$manifest.artifacts | Select-Object kind,label,provider,size_bytes,content_type,redacted_uri

$diff = $manifest.artifacts | Where-Object { $_.kind -eq "diff" } | Select-Object -First 1
if ($diff) {
  Invoke-RestMethod -Uri "$base/cloud-runs/$cloudRunId/artifacts/$($diff.id)/content"
  Invoke-RestMethod -Method Post -Uri "$base/cloud-runs/$cloudRunId/artifacts/$($diff.id)/download"
}

Invoke-RestMethod -Method Post `
  -Uri "$base/cloud-runs/artifacts/cleanup-expired" `
  -ContentType "application/json" `
  -Body (@{ before = (Get-Date).ToUniversalTime().ToString("o"); limit = 100 } | ConvertTo-Json)
```

The manifest response redacts provider URI query strings and fragments. The download response returns a local API URL, not a signed OSS URL. Cleanup deletes expired `local_inline` rows and reports `aliyun_oss` rows as lifecycle-only.
```

- [ ] **Step 2: Update architecture Phase 12 boundary**

In `docs/architecture.md`, update the Phase 12 boundary section to include Phase 12D:

```markdown
Phase 12D completes the artifact/log plane goal from the original Phase 12 roadmap. The API now exposes `GET /cloud-runs/{cloud_run_id}/artifacts/manifest`, artifact list/detail/content endpoints, provider-neutral download descriptors, retention metadata, and `POST /cloud-runs/artifacts/cleanup-expired`. The artifact plane builds descriptors from cloud-run manifest/log metadata, local-inline stored objects, and patch-artifact diff fallback while preserving workspace and run-scope checks.

The artifact plane does not return signed provider URLs or delete Aliyun OSS objects. It redacts provider refs for display by removing query strings and fragments, validates content reads through existing object-storage integrity checks, deletes only expired `local_inline` rows, and reports external-provider cleanup as lifecycle-only operator intent.

The desktop now renders a compact artifact browser inside the cloud-run task detail area. It shows retention policy, artifact count, grouped artifact metadata, redacted refs, and inline text previews for readable artifacts.
```

Also update the previous non-goal sentence that says Phase 12 does not add artifact browser UI. Replace it with:

```markdown
Phase 12 still does not add WebSockets, Server-Sent Events, SLS-managed log stores, model-backed reviewer or debugger agents, production KMS, or billing. Phase 12D intentionally keeps the artifact browser minimal and scoped to the existing task detail area.
```

- [ ] **Step 3: Update status docs after verification**

Before final verification, update the top of `docs/superpowers/status.md` so it states the project is through Phase 12D and Phase 13A:

```markdown
The project is through Phase 13A, with Phase 12D now completing the original Phase 12 artifact plane: cloud-run artifact manifests, safe artifact listing/detail/content APIs, provider-neutral download descriptors, retention metadata, local-inline cleanup, external lifecycle-only cleanup intent, and a minimal desktop artifact browser.
```

Add Phase 12D to the completed milestone list:

```markdown
18. Phase 12D artifact plane completion: manifest/list/detail/content APIs,
    provider-neutral download descriptors, retention metadata, local-inline
    cleanup, external lifecycle-only cleanup intent, and desktop artifact
    browsing.
19. Phase 13A Aliyun operational hardening: service-level MNS receipt recovery,
    ECI terminal cleanup, bounded MNS receive, lifecycle documentation, and
    protected callback endpoint boundaries.
```

In `STATUS.md`, replace the title with:

```markdown
# Phase 12D Artifact Plane Status
```

Add a short status summary:

```markdown
Phase 12D completes the remaining original Phase 12 artifact plane targets after Phase 12A, 12B, 12C, and 13A. The API exposes cloud-run artifact manifests, artifact list/detail/content endpoints, provider-neutral download descriptors, retention metadata, and expired-artifact cleanup. The desktop task board can display manifest artifacts and open text previews.
```

Leave a `Verification` section ready for real command output. Use exact command names and final pass counts from Step 4.

- [ ] **Step 4: Run full verification**

Run these commands in order:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_content or download_is_local or cleanup_expired or returns_gone"
pytest apps/api/tests/test_cloud_object_storage.py -q
pytest apps/api/tests -q
pnpm --filter @ai-scdc/desktop test -- App.test.tsx client.test.ts
pnpm typecheck
git diff --check
```

Expected output shape:

```text
6 passed
cloud object storage command exits 0 and the pytest summary ends with "passed"
all API test command exits 0 and the pytest summary ends with "passed"
PASS  src/test/App.test.tsx
PASS  src/test/client.test.ts
typecheck completed without errors
git diff --check produced no output
```

If a command fails, fix the failing code or test in the task that introduced the failure, then rerun the failing command and every later command in this list.

- [ ] **Step 5: Record verification results**

Update `STATUS.md` with the real outputs from Step 4. Use this structure:

```markdown
## Verification

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "artifact_manifest or artifact_content or download_is_local or cleanup_expired or returns_gone"`: record the pass count printed by pytest.
- `pytest apps/api/tests/test_cloud_object_storage.py -q`: record the pass count printed by pytest.
- `pytest apps/api/tests -q`: record the pass count printed by pytest.
- `pnpm --filter @ai-scdc/desktop test -- App.test.tsx client.test.ts`: record the Vitest pass count.
- `pnpm typecheck`: passed
- `git diff --check`: passed
```

Do not commit this section until every line above contains the real result from the current worktree.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add README.md docs/architecture.md docs/superpowers/status.md STATUS.md
git commit -m "Document Phase 12D artifact plane completion"
```

Expected output:

```text
commit summary includes "Document Phase 12D artifact plane completion"
```

---

## Final Review Checklist

- [ ] `GET /cloud-runs/{cloud_run_id}/artifacts/manifest` returns a versioned manifest with retention metadata and redacted artifact refs.
- [ ] `GET /cloud-runs/{cloud_run_id}/artifacts` and `GET /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}` return the same descriptor data as the manifest.
- [ ] `GET /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/content` validates cloud-run scope, workspace scope, kind, sha256, size, content type, and expiration.
- [ ] Expired local-inline content returns HTTP 410 before cleanup and HTTP 404 after cleanup removes the row.
- [ ] `POST /cloud-runs/{cloud_run_id}/artifacts/{artifact_id}/download` returns only a local API URL and safe metadata.
- [ ] `POST /cloud-runs/artifacts/cleanup-expired` deletes only local-inline rows and reports Aliyun OSS rows as lifecycle-only.
- [ ] Desktop fake client and HTTP client both expose deterministic artifact manifest and content methods.
- [ ] Desktop task board displays artifact count, retention policy, labels, redacted refs, and text previews.
- [ ] Existing log-window tests still pass.
- [ ] Final docs contain real verification output from the current worktree.
