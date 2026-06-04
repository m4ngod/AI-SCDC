# Phase 12A Log Polling And Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded cloud-run log window API with cursor pagination and safe remote log-stream object reads.

**Architecture:** Keep the existing `/cloud-runs/{id}/logs` list endpoint unchanged. Add complete log-stream object metadata to `CloudRun`, persist it from runtime provider submissions, then expose a new focused `cloud_run_logs.py` service and `/logs/window` route that can page persisted log rows and optionally append redacted log-stream lines. Add desktop API-client support without changing the task board rendering in this phase.

**Tech Stack:** FastAPI, SQLModel, SQLite upgrade helpers, Pydantic schemas, pytest, React/Vite TypeScript API client, Vitest.

---

## File Structure

- Modify `apps/api/app/ai_company_api/models/entities.py`
  - Add nullable metadata columns to `CloudRun` for artifact manifest and log-stream object refs.
- Modify `apps/api/app/ai_company_api/db/session.py`
  - Add and call `_upgrade_sqlite_cloud_run_phase_12a_columns()`.
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`
  - Return full `ObjectStorageRef` metadata from `remote_stub` and `aliyun_eci` runtime submissions.
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Persist `RemoteRuntimeSubmissionResult` ref metadata onto `CloudRun`.
- Modify `apps/api/app/ai_company_api/schemas/api.py`
  - Add `CloudRunLogWindowEntryRead` and `CloudRunLogWindowRead`.
- Create `apps/api/app/ai_company_api/services/cloud_run_logs.py`
  - Own cursor parsing, bounded log-window queries, stream object reads, stream-line redaction, and response assembly.
- Modify `apps/api/app/ai_company_api/api/routes.py`
  - Add `GET /cloud-runs/{cloud_run_id}/logs/window`.
- Modify `apps/api/tests/test_cloud_run_api.py`
  - Add API and SQLite upgrade regression tests.
- Modify `apps/desktop/src/api/client.ts`
  - Add log-window types and HTTP/fake client method.
- Modify `apps/desktop/src/test/client.test.ts`
  - Add HTTP client mapping test.
- Modify `README.md`, `docs/architecture.md`, and `docs/superpowers/status.md`
  - Document Phase 12A behavior and verification.

---

### Task 1: Persist Runtime Log Stream Metadata

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Modify: `apps/api/app/ai_company_api/services/remote_runtime.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing SQLite upgrade test**

Add this test after `test_init_db_adds_phase_10d_callback_token_columns` in `apps/api/tests/test_cloud_run_api.py`:

```python
def test_init_db_adds_phase_12a_log_stream_metadata_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    SQLModel.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE cloud_run RENAME TO cloud_run_old")
        connection.exec_driver_sql(
            """
            CREATE TABLE cloud_run (
                id VARCHAR NOT NULL PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                project_id VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                repo_id VARCHAR NOT NULL,
                local_run_id VARCHAR,
                base_branch VARCHAR NOT NULL,
                head_branch VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                sandbox_kind VARCHAR NOT NULL,
                cancel_requested BOOLEAN NOT NULL DEFAULT 0,
                queue_provider VARCHAR NOT NULL DEFAULT 'local_db',
                runtime_provider VARCHAR,
                runtime_job_id VARCHAR,
                storage_provider VARCHAR,
                artifact_manifest_uri VARCHAR,
                log_stream_uri VARCHAR,
                external_status VARCHAR,
                external_error VARCHAR,
                callback_token_hash VARCHAR,
                callback_token_expires_at DATETIME,
                callback_token_used_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql("DROP TABLE cloud_run_old")

    init_db(engine)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("cloud_run")
    }

    assert "artifact_manifest_sha256" in columns
    assert "artifact_manifest_size_bytes" in columns
    assert "artifact_manifest_content_type" in columns
    assert "log_stream_sha256" in columns
    assert "log_stream_size_bytes" in columns
    assert "log_stream_content_type" in columns
```

- [ ] **Step 2: Run SQLite upgrade test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_init_db_adds_phase_12a_log_stream_metadata_columns -v
```

Expected: FAIL because the six Phase 12A columns do not exist on `CloudRun`.

- [ ] **Step 3: Add `CloudRun` metadata fields**

In `apps/api/app/ai_company_api/models/entities.py`, add these fields immediately after `log_stream_uri`:

```python
    artifact_manifest_sha256: str | None = None
    artifact_manifest_size_bytes: int | None = None
    artifact_manifest_content_type: str | None = None
    log_stream_sha256: str | None = None
    log_stream_size_bytes: int | None = None
    log_stream_content_type: str | None = None
```

- [ ] **Step 4: Add SQLite upgrade helper**

In `apps/api/app/ai_company_api/db/session.py`, update `init_db()`:

```python
def init_db(engine) -> None:
    _upgrade_sqlite_cloud_run_phase_9_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10a_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10b_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10d_columns(engine)
    _upgrade_sqlite_cloud_run_phase_12a_columns(engine)
    SQLModel.metadata.create_all(engine)
    _upgrade_sqlite_repository_phase_7_columns(engine)
    _upgrade_sqlite_cloud_run_phase_8_columns(engine)
    _upgrade_sqlite_local_test_run_nullable_patch_artifact(engine)
    _upgrade_sqlite_planner_run_metadata(engine)
    _upgrade_sqlite_task_execution_constraints(engine)
    _upgrade_sqlite_patch_review_uniqueness(engine)
```

Add this helper after `_upgrade_sqlite_cloud_run_phase_10d_columns()`:

```python
def _upgrade_sqlite_cloud_run_phase_12a_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "artifact_manifest_sha256": "VARCHAR",
        "artifact_manifest_size_bytes": "INTEGER",
        "artifact_manifest_content_type": "VARCHAR",
        "log_stream_sha256": "VARCHAR",
        "log_stream_size_bytes": "INTEGER",
        "log_stream_content_type": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )
```

- [ ] **Step 5: Run SQLite upgrade test to verify it passes**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_init_db_adds_phase_12a_log_stream_metadata_columns -v
```

Expected: PASS.

- [ ] **Step 6: Write failing runtime metadata persistence test**

Add this test near the existing remote runtime provider metadata tests in `apps/api/tests/test_cloud_run_api.py`:

```python
def test_remote_stub_runtime_submission_persists_log_stream_object_metadata(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "runtime_provider": "remote_stub",
            "storage_provider": "local_inline",
        },
    )

    assert response.status_code == 201
    cloud_run_id = response.json()["cloud_run"]["id"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, cloud_run_id)
        assert cloud_run is not None
        assert cloud_run.artifact_manifest_uri is not None
        assert cloud_run.artifact_manifest_sha256 is not None
        assert cloud_run.artifact_manifest_size_bytes is not None
        assert cloud_run.artifact_manifest_content_type == "application/json"
        assert cloud_run.log_stream_uri is not None
        assert cloud_run.log_stream_sha256 is not None
        assert cloud_run.log_stream_size_bytes is not None
        assert cloud_run.log_stream_content_type == "text/plain"
```

- [ ] **Step 7: Run runtime metadata test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_remote_stub_runtime_submission_persists_log_stream_object_metadata -v
```

Expected: FAIL because the runtime submission result does not return full object refs and `CloudRun` does not persist ref metadata.

- [ ] **Step 8: Return full object refs from runtime providers**

In `apps/api/app/ai_company_api/services/remote_runtime.py`, import `ObjectStorageRef`:

```python
from ai_company_api.services.object_storage import (
    ObjectStorageRef,
    ObjectStorageWrite,
    get_object_storage_provider,
)
```

Replace `RemoteRuntimeSubmissionResult` with:

```python
@dataclass(frozen=True)
class RemoteRuntimeSubmissionResult:
    runtime_job_id: str
    external_status: str
    artifact_manifest_ref: ObjectStorageRef | None = None
    log_stream_ref: ObjectStorageRef | None = None

    @property
    def artifact_manifest_uri(self) -> str | None:
        return self.artifact_manifest_ref.uri if self.artifact_manifest_ref else None

    @property
    def log_stream_uri(self) -> str | None:
        return self.log_stream_ref.uri if self.log_stream_ref else None
```

In `RemoteStubRuntimeProvider.submit()`, replace the local URI variables with ref variables:

```python
        artifact_manifest_ref: ObjectStorageRef | None = None
        log_stream_ref: ObjectStorageRef | None = None

        if submission.storage_provider == "local_inline":
            storage_provider = get_object_storage_provider("local_inline")
            artifact_manifest_ref = storage_provider.put_text(
                session,
                ObjectStorageWrite(
                    workspace_id=submission.workspace_id,
                    cloud_run_id=submission.cloud_run_id,
                    kind="manifest",
                    content=json.dumps(
                        {
                            "cloud_run_id": submission.cloud_run_id,
                            "queue_provider": submission.queue_provider,
                            "runtime_provider": submission.runtime_provider,
                            "storage_provider": submission.storage_provider,
                            "status": "submitted",
                        },
                        sort_keys=True,
                    ),
                    content_type="application/json",
                ),
            )
            log_stream_ref = storage_provider.put_text(
                session,
                ObjectStorageWrite(
                    workspace_id=submission.workspace_id,
                    cloud_run_id=submission.cloud_run_id,
                    kind="log",
                    content="Remote runtime submitted via remote_stub.\n",
                    content_type="text/plain",
                ),
            )

        return RemoteRuntimeSubmissionResult(
            runtime_job_id=f"remote-stub-job-{submission.cloud_run_id}",
            external_status="submitted",
            artifact_manifest_ref=artifact_manifest_ref,
            log_stream_ref=log_stream_ref,
        )
```

In `AliyunEciRuntimeProvider.submit()`, replace the local URI variables with ref variables:

```python
            artifact_manifest_ref: ObjectStorageRef | None = None
            log_stream_ref: ObjectStorageRef | None = None
            if submission.storage_provider == "aliyun_oss":
                storage_provider = get_object_storage_provider("aliyun_oss")
                artifact_manifest_ref = storage_provider.put_text(
                    session,
                    ObjectStorageWrite(
                        workspace_id=submission.workspace_id,
                        cloud_run_id=submission.cloud_run_id,
                        kind="manifest",
                        content=json.dumps(
                            {
                                "cloud_run_id": submission.cloud_run_id,
                                "queue_provider": submission.queue_provider,
                                "runtime_job_id": runtime_job_id,
                                "runtime_provider": self.name,
                                "status": "submitted",
                                "storage_provider": submission.storage_provider,
                            },
                            sort_keys=True,
                        ),
                        content_type="application/json",
                    ),
                )
                log_stream_ref = storage_provider.put_text(
                    session,
                    ObjectStorageWrite(
                        workspace_id=submission.workspace_id,
                        cloud_run_id=submission.cloud_run_id,
                        kind="log",
                        content="Remote runtime submitted via aliyun_eci.\n",
                        content_type="text/plain",
                    ),
                )
```

Update the return statement in `AliyunEciRuntimeProvider.submit()`:

```python
        return RemoteRuntimeSubmissionResult(
            runtime_job_id=runtime_job_id,
            external_status="submitted",
            artifact_manifest_ref=artifact_manifest_ref,
            log_stream_ref=log_stream_ref,
        )
```

- [ ] **Step 9: Persist runtime ref metadata in `cloud_runner.py`**

In `enqueue_cloud_run()`, after the `runtime_provider.submit` call that assigns `runtime_submission`, replace the URI assignments with:

```python
        cloud_run.runtime_job_id = runtime_submission.runtime_job_id
        cloud_run.external_status = runtime_submission.external_status
        cloud_run.artifact_manifest_uri = runtime_submission.artifact_manifest_uri
        cloud_run.log_stream_uri = runtime_submission.log_stream_uri
        if runtime_submission.artifact_manifest_ref is not None:
            cloud_run.artifact_manifest_sha256 = runtime_submission.artifact_manifest_ref.sha256
            cloud_run.artifact_manifest_size_bytes = (
                runtime_submission.artifact_manifest_ref.size_bytes
            )
            cloud_run.artifact_manifest_content_type = (
                runtime_submission.artifact_manifest_ref.content_type
            )
        if runtime_submission.log_stream_ref is not None:
            cloud_run.log_stream_sha256 = runtime_submission.log_stream_ref.sha256
            cloud_run.log_stream_size_bytes = runtime_submission.log_stream_ref.size_bytes
            cloud_run.log_stream_content_type = runtime_submission.log_stream_ref.content_type
```

- [ ] **Step 10: Run Task 1 focused tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "phase_12a or log_stream_object_metadata" -v
```

Expected: PASS for the two Task 1 tests.

- [ ] **Step 11: Commit Task 1**

Run:

```bash
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/services/remote_runtime.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: persist cloud run log stream metadata"
```

---

### Task 2: Add Control-Plane Log Window API

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/cloud_run_logs.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing bounded window endpoint test**

Add this test near `test_cloud_run_logs_are_ordered_and_redacted`:

```python
def test_cloud_run_log_window_returns_bounded_pages_without_duplicates(
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
        base_time = cloud_run.created_at
        for index in range(3):
            session.add(
                CloudRunLogEntry(
                    id=f"log_window_{index}",
                    cloud_run_id=cloud_run.id,
                    workspace_id=cloud_run.workspace_id,
                    event=f"window_{index}",
                    message=f"Window log {index}.",
                    created_at=base_time + timedelta(seconds=index + 1),
                )
            )
        session.commit()

    first_response = client.get(f"/cloud-runs/{queued['id']}/logs/window?limit=2")
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert [entry["event"] for entry in first_body["entries"]] == [
        "queued",
        "window_0",
    ]
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]

    second_response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"after": first_body["next_cursor"], "limit": 10},
    )
    assert second_response.status_code == 200
    second_body = second_response.json()
    assert [entry["event"] for entry in second_body["entries"]] == [
        "window_1",
        "window_2",
    ]
    assert second_body["has_more"] is False
    assert second_body["next_cursor"] is None
```

- [ ] **Step 2: Run bounded window test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_cloud_run_log_window_returns_bounded_pages_without_duplicates -v
```

Expected: FAIL with 404 because `/cloud-runs/{id}/logs/window` does not exist.

- [ ] **Step 3: Add log-window schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add after `CloudRunLogEntryRead`:

```python
class CloudRunLogWindowEntryRead(BaseModel):
    id: str
    cloud_run_id: str
    source: Literal["control_plane", "log_stream"]
    level: str
    event: str
    message: str
    payload: dict[str, Any] | None
    created_at: datetime
    sequence: int


class CloudRunLogWindowRead(BaseModel):
    entries: list[CloudRunLogWindowEntryRead]
    next_cursor: str | None
    has_more: bool
```

- [ ] **Step 4: Add `cloud_run_logs.py` control-plane pagination service**

Create `apps/api/app/ai_company_api/services/cloud_run_logs.py`:

```python
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Literal

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import CloudRun, CloudRunLogEntry
from ai_company_api.schemas.api import (
    CloudRunLogWindowEntryRead,
    CloudRunLogWindowRead,
)


CursorSource = Literal["control_plane", "log_stream"]


@dataclass(frozen=True)
class LogWindowCursor:
    source: CursorSource
    created_at: datetime | None = None
    id: str | None = None
    stream_line: int | None = None


def list_cloud_run_log_window(
    session: Session,
    *,
    cloud_run_id: str,
    after: str | None = None,
    limit: int = 100,
    include_stream: bool = True,
) -> CloudRunLogWindowRead:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")
    cursor = _decode_cursor(after)
    entries = _control_plane_entries(
        session,
        cloud_run=cloud_run,
        cursor=cursor,
        limit=limit + 1,
    )
    has_more = len(entries) > limit
    entries = entries[:limit]
    next_cursor = _entry_cursor(entries[-1]) if has_more and entries else None
    return CloudRunLogWindowRead(
        entries=entries,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def _control_plane_entries(
    session: Session,
    *,
    cloud_run: CloudRun,
    cursor: LogWindowCursor | None,
    limit: int,
) -> list[CloudRunLogWindowEntryRead]:
    if cursor is not None and cursor.source == "log_stream":
        return []
    statement = (
        select(CloudRunLogEntry)
        .where(CloudRunLogEntry.cloud_run_id == cloud_run.id)
        .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
        .limit(limit)
    )
    if cursor is not None and cursor.created_at is not None and cursor.id is not None:
        statement = (
            select(CloudRunLogEntry)
            .where(CloudRunLogEntry.cloud_run_id == cloud_run.id)
            .where(
                (CloudRunLogEntry.created_at > cursor.created_at)
                | (
                    (CloudRunLogEntry.created_at == cursor.created_at)
                    & (CloudRunLogEntry.id > cursor.id)
                )
            )
            .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            .limit(limit)
        )
    rows = session.exec(statement).all()
    return [
        CloudRunLogWindowEntryRead(
            id=row.id,
            cloud_run_id=row.cloud_run_id,
            source="control_plane",
            level=row.level,
            event=row.event,
            message=row.message,
            payload=row.payload,
            created_at=row.created_at,
            sequence=index,
        )
        for index, row in enumerate(rows)
    ]


def _decode_cursor(value: str | None) -> LogWindowCursor | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        source = payload["source"]
        if source not in {"control_plane", "log_stream"}:
            raise ValueError("invalid source")
        created_at = (
            datetime.fromisoformat(payload["created_at"])
            if payload.get("created_at")
            else None
        )
        return LogWindowCursor(
            source=source,
            created_at=created_at,
            id=payload.get("id"),
            stream_line=payload.get("stream_line"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid log cursor") from exc


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _entry_cursor(entry: CloudRunLogWindowEntryRead) -> str:
    return _encode_cursor(
        {
            "source": entry.source,
            "created_at": entry.created_at.isoformat(),
            "id": entry.id,
            "stream_line": None,
        }
    )
```

- [ ] **Step 5: Add route**

In `apps/api/app/ai_company_api/api/routes.py`, import `Query`:

```python
from fastapi import APIRouter, Depends, Query, Response, status
```

Import `CloudRunLogWindowRead` from schemas and `list_cloud_run_log_window` from the new service:

```python
    CloudRunLogWindowRead,
```

```python
from ai_company_api.services.cloud_run_logs import list_cloud_run_log_window
```

Add this route before the existing `/cloud-runs/{cloud_run_id}/logs` route:

```python
@router.get(
    "/cloud-runs/{cloud_run_id}/logs/window",
    response_model=CloudRunLogWindowRead,
)
def get_cloud_run_log_window(
    cloud_run_id: str,
    session: SessionDep,
    after: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    include_stream: bool = True,
) -> CloudRunLogWindowRead:
    return list_cloud_run_log_window(
        session,
        cloud_run_id=cloud_run_id,
        after=after,
        limit=limit,
        include_stream=include_stream,
    )
```

- [ ] **Step 6: Run bounded window test to verify it passes**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_cloud_run_log_window_returns_bounded_pages_without_duplicates -v
```

Expected: PASS.

- [ ] **Step 7: Write invalid cursor test**

Add:

```python
def test_cloud_run_log_window_rejects_invalid_cursor(tmp_path: Path) -> None:
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

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"after": "not-a-valid-cursor"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid log cursor"
```

- [ ] **Step 8: Run Task 2 tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window_returns_bounded or log_window_rejects_invalid_cursor" -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 2**

Run:

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_run_logs.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add cloud run log window endpoint"
```

---

### Task 3: Add Safe Remote Log Stream Reads

**Files:**
- Modify: `apps/api/app/ai_company_api/services/cloud_run_logs.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing stream-read and redaction test**

Add:

```python
def test_cloud_run_log_window_includes_redacted_stream_lines_when_metadata_exists(
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
        ref = get_object_storage_provider("local_inline").put_text(
            session,
            ObjectStorageWrite(
                workspace_id=cloud_run.workspace_id,
                cloud_run_id=cloud_run.id,
                kind="log",
                content=(
                    "worker started\n"
                    "provider token=secret-token Bearer abc.def\n"
                ),
                content_type="text/plain",
            ),
        )
        cloud_run.log_stream_uri = ref.uri
        cloud_run.log_stream_sha256 = ref.sha256
        cloud_run.log_stream_size_bytes = ref.size_bytes
        cloud_run.log_stream_content_type = ref.content_type
        session.add(cloud_run)
        session.commit()

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"limit": 20, "include_stream": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    stream_entries = [
        entry for entry in body["entries"] if entry["source"] == "log_stream"
    ]
    assert [entry["message"] for entry in stream_entries] == [
        "worker started",
        "provider token=[redacted] Bearer [redacted]",
    ]
    assert "secret-token" not in str(body)
    assert "abc.def" not in str(body)
    assert stream_entries[0]["payload"]["uri"] == ref.uri
    assert stream_entries[0]["payload"]["line"] == 1
```

- [ ] **Step 2: Run stream-read test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_cloud_run_log_window_includes_redacted_stream_lines_when_metadata_exists -v
```

Expected: FAIL because the log window service only returns control-plane entries.

- [ ] **Step 3: Extend `cloud_run_logs.py` with stream reads**

Add imports:

```python
import re
from urllib.parse import urlsplit, urlunsplit

from ai_company_api.services.object_storage import (
    ObjectStorageProviderNotFound,
    ObjectStorageReadError,
    ObjectStorageRef,
    get_object_storage_provider,
)
```

Add constants and helpers:

```python
STREAM_TOKEN_PATTERN = re.compile(
    r"\b(token|access_token|authorization|password|secret|sig)\s*[:=]\s*"
    r"(?:bearer\s+)?[^\s&]+",
    re.IGNORECASE,
)
STREAM_BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)


def _redact_stream_text(value: str) -> str:
    def replace_key_value(match: re.Match[str]) -> str:
        return f"{match.group(1)}=[redacted]"

    redacted = STREAM_TOKEN_PATTERN.sub(replace_key_value, value)
    return STREAM_BEARER_PATTERN.sub("Bearer [redacted]", redacted)


def _redact_uri(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _provider_name_from_uri(uri: str) -> str:
    if uri.startswith("local-inline://"):
        return "local_inline"
    if uri.startswith("oss://"):
        return "aliyun_oss"
    raise ObjectStorageReadError("Unsupported log stream URI scheme")


def _stream_ref(cloud_run: CloudRun) -> ObjectStorageRef | None:
    if (
        cloud_run.log_stream_uri is None
        or cloud_run.log_stream_sha256 is None
        or cloud_run.log_stream_size_bytes is None
        or cloud_run.log_stream_content_type is None
    ):
        return None
    return ObjectStorageRef(
        kind="log",
        uri=cloud_run.log_stream_uri,
        sha256=cloud_run.log_stream_sha256,
        size_bytes=cloud_run.log_stream_size_bytes,
        content_type=cloud_run.log_stream_content_type,
    )
```

Add stream entry assembly:

```python
def _stream_entries(
    session: Session,
    *,
    cloud_run: CloudRun,
    cursor: LogWindowCursor | None,
    limit: int,
) -> list[CloudRunLogWindowEntryRead]:
    ref = _stream_ref(cloud_run)
    if ref is None:
        return []
    start_line = cursor.stream_line if cursor and cursor.source == "log_stream" else 0
    try:
        provider = get_object_storage_provider(_provider_name_from_uri(ref.uri))
        content = provider.read_text(session, ref)
    except (ObjectStorageProviderNotFound, ObjectStorageReadError):
        return []
    lines = content.splitlines()
    selected = lines[start_line : start_line + limit]
    return [
        CloudRunLogWindowEntryRead(
            id=f"{cloud_run.id}:log-stream:{line_number}",
            cloud_run_id=cloud_run.id,
            source="log_stream",
            level="info",
            event="log_stream",
            message=_redact_stream_text(line),
            payload={
                "source": "log_stream",
                "line": line_number + 1,
                "uri": _redact_uri(ref.uri),
            },
            created_at=cloud_run.created_at,
            sequence=line_number,
        )
        for line_number, line in enumerate(selected, start=start_line)
    ]
```

Update `list_cloud_run_log_window()` so it fills the page with control-plane entries first, then stream entries:

```python
    entries = _control_plane_entries(
        session,
        cloud_run=cloud_run,
        cursor=cursor,
        limit=limit + 1,
    )
    if len(entries) > limit:
        return CloudRunLogWindowRead(
            entries=entries[:limit],
            next_cursor=_entry_cursor(entries[limit - 1]),
            has_more=True,
        )

    if include_stream:
        remaining = limit - len(entries)
        stream_entries = _stream_entries(
            session,
            cloud_run=cloud_run,
            cursor=cursor,
            limit=remaining + 1,
        )
        entries.extend(stream_entries[:remaining])
        if len(stream_entries) > remaining and entries:
            return CloudRunLogWindowRead(
                entries=entries,
                next_cursor=_entry_cursor(entries[-1]),
                has_more=True,
            )

    return CloudRunLogWindowRead(entries=entries, next_cursor=None, has_more=False)
```

Update `_entry_cursor()` for stream entries:

```python
def _entry_cursor(entry: CloudRunLogWindowEntryRead) -> str:
    if entry.source == "log_stream":
        return _encode_cursor(
            {
                "source": "log_stream",
                "created_at": None,
                "id": None,
                "stream_line": entry.sequence + 1,
            }
        )
    return _encode_cursor(
        {
            "source": entry.source,
            "created_at": entry.created_at.isoformat(),
            "id": entry.id,
            "stream_line": None,
        }
    )
```

- [ ] **Step 4: Run stream-read test to verify it passes**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_cloud_run_log_window_includes_redacted_stream_lines_when_metadata_exists -v
```

Expected: PASS.

- [ ] **Step 5: Write missing metadata test**

Add:

```python
def test_cloud_run_log_window_skips_stream_when_metadata_is_missing(
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
        cloud_run.log_stream_uri = "local-inline://cloud-run-objects/missing"
        cloud_run.log_stream_sha256 = None
        cloud_run.log_stream_size_bytes = None
        cloud_run.log_stream_content_type = None
        session.add(cloud_run)
        session.commit()

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true"},
    )

    assert response.status_code == 200
    body = response.json()
    assert all(entry["source"] == "control_plane" for entry in body["entries"])
    assert "local-inline://cloud-run-objects/missing" not in str(body)
```

- [ ] **Step 6: Run Task 3 focused tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window_includes_redacted_stream or log_window_skips_stream or log_window_returns_bounded" -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
git add apps/api/app/ai_company_api/services/cloud_run_logs.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: read cloud run log streams"
```

---

### Task 4: Add Desktop API Client Support

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Test: `apps/desktop/src/test/client.test.ts`

- [ ] **Step 1: Write failing HTTP client test**

Add this test after `"HTTP client processes, cancels, and lists cloud run logs"` in `apps/desktop/src/test/client.test.ts`:

```typescript
  it("HTTP client lists cloud run log windows", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        entries: [
          {
            id: "cloud_run_api:log-stream:0",
            cloud_run_id: "cloud_run_api",
            source: "log_stream",
            level: "info",
            event: "log_stream",
            message: "worker started",
            payload: { source: "log_stream", line: 1 },
            created_at: "2026-06-05T02:00:00Z",
            sequence: 0
          }
        ],
        next_cursor: "cursor_1",
        has_more: true
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });

    const window = await client.listCloudRunLogWindow("cloud_run_api", {
      after: "cursor_0",
      limit: 25,
      includeStream: true
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/cloud-runs/cloud_run_api/logs/window?after=cursor_0&limit=25&include_stream=true"
    );
    expect(window).toEqual({
      entries: [
        expect.objectContaining({
          id: "cloud_run_api:log-stream:0",
          source: "log_stream",
          sequence: 0
        })
      ],
      nextCursor: "cursor_1",
      hasMore: true
    });
  });
```

- [ ] **Step 2: Run desktop client test to verify it fails**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected: FAIL because `listCloudRunLogWindow` is not defined on the client type.

- [ ] **Step 3: Add desktop types and method signatures**

In `apps/desktop/src/api/client.ts`, add near `CloudRunLogEntryCard`:

```typescript
export type CloudRunLogWindowEntryCard = CloudRunLogEntryCard & {
  source: "control_plane" | "log_stream";
  sequence: number;
};

export type CloudRunLogWindowCard = {
  entries: CloudRunLogWindowEntryCard[];
  nextCursor: string | null;
  hasMore: boolean;
};

export type CloudRunLogWindowOptions = {
  after?: string | null;
  limit?: number;
  includeStream?: boolean;
};
```

Add to `ConsoleApiClient`:

```typescript
  listCloudRunLogWindow: (
    cloudRunId: string,
    options?: CloudRunLogWindowOptions
  ) => Promise<CloudRunLogWindowCard>;
```

Add API response types:

```typescript
type ApiCloudRunLogWindowEntry = ApiCloudRunLogEntry & {
  source: "control_plane" | "log_stream";
  sequence: number;
};

type ApiCloudRunLogWindow = {
  entries: ApiCloudRunLogWindowEntry[];
  next_cursor: string | null;
  has_more: boolean;
};
```

- [ ] **Step 4: Add mapping helpers**

In `apps/desktop/src/api/client.ts`, add after `mapCloudRunLogEntryCard()`:

```typescript
function mapCloudRunLogWindowEntryCard(
  logEntry: ApiCloudRunLogWindowEntry
): CloudRunLogWindowEntryCard {
  return {
    ...mapCloudRunLogEntryCard(logEntry),
    source: logEntry.source,
    sequence: logEntry.sequence
  };
}

function mapCloudRunLogWindowCard(window: ApiCloudRunLogWindow): CloudRunLogWindowCard {
  return {
    entries: window.entries.map(mapCloudRunLogWindowEntryCard),
    nextCursor: window.next_cursor,
    hasMore: window.has_more
  };
}
```

- [ ] **Step 5: Add fake client method**

In the fake client object in `apps/desktop/src/api/client.ts`, add:

```typescript
  async listCloudRunLogWindow(
    cloudRunId: string,
    windowOptions: CloudRunLogWindowOptions = {}
  ) {
    const logs = await this.listCloudRunLogs(cloudRunId);
    const limit = windowOptions.limit ?? logs.length;
    const entries = logs.slice(0, limit).map((entry, sequence) => ({
      ...entry,
      source: "control_plane" as const,
      sequence
    }));
    return {
      entries,
      nextCursor: logs.length > entries.length ? String(entries.length) : null,
      hasMore: logs.length > entries.length
    };
  },
```

- [ ] **Step 6: Add HTTP client method**

In `createHttpApiClient()`, add this method. The parameter is named
`windowOptions` so it does not shadow the outer `options` from
`createHttpApiClient(options)`:

```typescript
    async listCloudRunLogWindow(cloudRunId: string, windowOptions = {}) {
      const params = new URLSearchParams();
      if (windowOptions.after) {
        params.set("after", windowOptions.after);
      }
      if (windowOptions.limit !== undefined) {
        params.set("limit", String(windowOptions.limit));
      }
      if (windowOptions.includeStream !== undefined) {
        params.set("include_stream", String(windowOptions.includeStream));
      }
      const query = params.toString();
      const path = `/cloud-runs/${cloudRunId}/logs/window${query ? `?${query}` : ""}`;
      const response = await fetch(apiUrl(options.baseUrl, path));
      const window = await readJsonResponse<ApiCloudRunLogWindow>(
        response,
        `GET /cloud-runs/${cloudRunId}/logs/window`
      );
      return mapCloudRunLogWindowCard(window);
    },
```

- [ ] **Step 7: Run desktop client test to verify it passes**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected: PASS.

- [ ] **Step 8: Run desktop typecheck**

Run:

```bash
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit Task 4**

Run:

```bash
git add apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "feat: add desktop log window client"
```

---

### Task 5: Documentation, Status, And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Test: full API and desktop verification

- [ ] **Step 1: Update architecture**

In `docs/architecture.md`, add after the Phase 11 boundary:

```markdown
## Phase 12A Boundary

Phase 12A adds a bounded log polling surface for cloud runs. The API keeps the
legacy full log list endpoint and adds a cursor-based log window endpoint that
can return persisted control-plane log rows and redacted remote log-stream
lines when the run has complete object-storage ref metadata.

Phase 12A does not add WebSockets, provider-native live log streaming, direct
MNS receive/delete semantics, artifact browser UI, model-backed reviewer or
debugger agents, production KMS, or a broad `cloud_runner.py` split.
```

Move the roadmap completed list to include:

```markdown
16. Bounded cloud-run log polling with cursor windows and safe remote log-stream reads.
```

Keep the Future list focused on provider-native streaming and MNS semantics.

- [ ] **Step 2: Update status document**

In `docs/superpowers/status.md`, change the current phase summary:

```markdown
The project is through Phase 12A: bounded cloud-run log polling and safe remote
log-stream reads for provider-backed runs.
```

Add a completed item:

```markdown
16. Phase 12A cloud-run log polling: cursor-based log windows, persisted
    log-stream object metadata, redacted stream-line reads, and desktop API
    client support.
```

Add the new verification commands to the latest verification block:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or phase_12a" -v
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

- [ ] **Step 3: Update README**

In `README.md`, update the opening summary sentence to include:

```text
Phase 12A bounded cloud-run log polling and safe remote log-stream reads
```

Add a compact note after the Phase 11 paragraph:

```markdown
Phase 12A adds `GET /cloud-runs/{cloud_run_id}/logs/window` for bounded log
polling. The endpoint accepts an opaque `after` cursor, `limit`, and
`include_stream`; it returns persisted control-plane log entries and, when
complete object metadata is present, redacted remote log-stream lines. The
legacy `/logs` endpoint remains available for full-list compatibility.
```

- [ ] **Step 4: Run focused API tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or phase_12a" -v
```

Expected: PASS.

- [ ] **Step 5: Run focused desktop tests**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected: PASS.

- [ ] **Step 6: Run full API tests**

Run:

```bash
pytest apps/api/tests -v
```

Expected: PASS.

- [ ] **Step 7: Run full typecheck**

Run:

```bash
pnpm typecheck
```

Expected: PASS.

- [ ] **Step 8: Run formatting and secret scans**

Run:

```bash
git diff --check
rg -n "ghp_|callback-token|AI_SCDC_CALLBACK_TOKEN|clone_token|AccessKey|ACCESS_KEY_SECRET|secret-token|abc\\.def" apps docs README.md
```

Expected:

- `git diff --check` exits 0.
- `rg` hits only test fake values, env names, schema field names, README sample values, plan/spec examples, and explicit redaction regression strings.

- [ ] **Step 9: Commit Task 5**

Run:

```bash
git add README.md docs/architecture.md docs/superpowers/status.md
git commit -m "docs: document phase 12a log polling"
```

- [ ] **Step 10: Request final code review**

Use `superpowers:requesting-code-review` with:

```text
Description: Phase 12A bounded cloud-run log polling and safe remote log-stream reads.
Requirements: docs/superpowers/specs/2026-06-05-phase-12a-log-polling-stream-design.md and this implementation plan.
Base SHA: 15fcf8d
Head SHA: current HEAD after Task 5.
Review focus: cursor semantics, stream metadata integrity, redaction, API compatibility, desktop client compatibility, and test coverage.
```

- [ ] **Step 11: Fix review feedback with TDD**

If the review reports Critical or Important issues, write failing regression tests first, watch them fail, implement the smallest fix, rerun focused and full verification, then commit.

- [ ] **Step 12: Final branch verification**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or phase_12a" -v
pnpm --filter @ai-scdc/desktop test -- client.test.ts
pytest apps/api/tests -v
pnpm typecheck
git diff --check
git status --short --branch
```

Expected: all commands pass and `git status --short --branch` shows a clean `codex/phase-12-artifact-log-plane` branch.

---

## Plan Self-Review

Spec coverage:

- Cursor/limit endpoint: Task 2.
- Complete object-ref metadata: Task 1.
- Safe remote log-stream reads: Task 3.
- Redaction: Task 3.
- Desktop client support: Task 4.
- Compatibility with existing `/logs`: Tasks 2 and 4 leave existing methods in place.
- Documentation and verification: Task 5.

Type consistency:

- API uses snake_case response fields: `next_cursor`, `has_more`, `include_stream`.
- Desktop maps those fields to camelCase: `nextCursor`, `hasMore`, `includeStream`.
- Stream source values are exactly `"control_plane"` and `"log_stream"` in Python and TypeScript.

Scope check:

- This plan excludes WebSockets, SLS, direct MNS worker consumption, artifact browser UI, model reviewer/debugger behavior, KMS changes, and broad service refactors. Those remain outside Phase 12A.
