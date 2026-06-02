# Phase 10A Remote Worker Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a remote-worker lease, heartbeat, completion, and expired-lease requeue contract for cloud runs while keeping Phase 9 fake and `docker_local` processing working.

**Architecture:** Extend the existing `CloudRun` record with queue and lease metadata, add explicit worker lease API endpoints, and implement the default queue adapter as local SQLite compare-and-set updates. Remote completion reuses the existing cloud-run finalization path so patch artifacts, cancellation, task transitions, and logs remain consistent with Phase 9.

**Tech Stack:** FastAPI, SQLModel, SQLite upgrade helpers, Pydantic schemas, pytest `TestClient`, Vitest/TypeScript desktop type tests.

---

## File Structure

- Modify `apps/api/app/ai_company_api/models/entities.py`
  - Add Phase 10A lease, heartbeat, attempt, and queue provider fields to `CloudRun`.
- Modify `apps/api/app/ai_company_api/db/session.py`
  - Add `_upgrade_sqlite_cloud_run_phase_10a_columns(engine)` and call it from `init_db()`.
- Modify `apps/api/app/ai_company_api/schemas/api.py`
  - Extend `CloudRunRead`.
  - Add request/response schemas for lease claim, heartbeat, completion, and expired-lease requeue.
- Modify `apps/api/app/ai_company_api/api/routes.py`
  - Add lease endpoints under `/cloud-run-worker/leases`.
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Add local queue adapter functions for claim, heartbeat, completion, and expired-lease requeue.
  - Extract executor finalization so local worker processing and remote lease completion share the same logic.
- Modify `apps/api/tests/test_cloud_run_api.py`
  - Add TDD coverage for Phase 10A data fields, lease lifecycle, stale leases, expired leases, cancellation, and legacy Phase 9 endpoint compatibility.
- Modify `apps/desktop/src/api/client.ts`
  - Add new optional/read fields to the `CloudRun` TypeScript type.
- Modify `apps/desktop/src/test/client.test.ts`
  - Add one client normalization test for new `CloudRun` fields.
- Modify `docs/architecture.md`, `docs/superpowers/status.md`, and `README.md`
  - Document the Phase 10A boundary and verification commands.

---

## Task 1: Phase 10A CloudRun Fields and SQLite Upgrade

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write the failing model/schema migration test**

Add this test near the existing init-db migration tests in `apps/api/tests/test_cloud_run_api.py`:

```python
def test_init_db_adds_phase_10a_cloud_run_lease_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase10a.db"
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
                worker_id VARCHAR,
                claimed_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO cloud_run (
                id, workspace_id, project_id, task_id, repo_id, local_run_id,
                base_branch, head_branch, status, sandbox_kind, cancel_requested,
                worker_id, claimed_at, completed_at, created_at, updated_at
            )
            SELECT id, workspace_id, project_id, task_id, repo_id, local_run_id,
                base_branch, head_branch, status, sandbox_kind, cancel_requested,
                worker_id, claimed_at, completed_at, created_at, updated_at
            FROM cloud_run_old
            """
        )
        connection.exec_driver_sql("DROP TABLE cloud_run_old")

    init_db(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cloud_run")}
    assert {
        "queue_provider",
        "remote_worker_kind",
        "lease_id",
        "lease_expires_at",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "last_queue_error",
    }.issubset(columns)
    indexes = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("cloud_run")
    }
    assert ("queue_provider",) in indexes
    assert ("remote_worker_kind",) in indexes
    assert ("lease_id",) in indexes
    assert ("lease_expires_at",) in indexes
```

- [ ] **Step 2: Run the failing migration test**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_init_db_adds_phase_10a_cloud_run_lease_columns -v
```

Expected: FAIL because the Phase 10A columns and indexes do not exist.

- [ ] **Step 3: Add fields to `CloudRun`**

In `apps/api/app/ai_company_api/models/entities.py`, extend `CloudRun` after `completed_at`:

```python
    queue_provider: str = Field(default="local_db", index=True)
    remote_worker_kind: str | None = Field(default=None, index=True)
    lease_id: str | None = Field(default=None, index=True)
    lease_expires_at: datetime | None = Field(default=None, index=True)
    heartbeat_at: datetime | None = None
    attempt_count: int = Field(default=0)
    max_attempts: int = Field(default=3)
    last_queue_error: str | None = None
```

- [ ] **Step 4: Add SQLite upgrade helper**

In `apps/api/app/ai_company_api/db/session.py`, call the new helper immediately after `_upgrade_sqlite_cloud_run_phase_9_columns(engine)`:

```python
def init_db(engine) -> None:
    _upgrade_sqlite_cloud_run_phase_9_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10a_columns(engine)
    SQLModel.metadata.create_all(engine)
    ...
```

Add this helper after `_upgrade_sqlite_cloud_run_phase_9_columns`:

```python
def _upgrade_sqlite_cloud_run_phase_10a_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "queue_provider": "VARCHAR NOT NULL DEFAULT 'local_db'",
        "remote_worker_kind": "VARCHAR",
        "lease_id": "VARCHAR",
        "lease_expires_at": "DATETIME",
        "heartbeat_at": "DATETIME",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "max_attempts": "INTEGER NOT NULL DEFAULT 3",
        "last_queue_error": "VARCHAR",
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

        for column_name in (
            "queue_provider",
            "remote_worker_kind",
            "lease_id",
            "lease_expires_at",
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_{column_name} "
                    f"ON cloud_run ({column_name})"
                )
            )
```

- [ ] **Step 5: Extend `CloudRunRead`**

In `apps/api/app/ai_company_api/schemas/api.py`, add fields after `completed_at`:

```python
    queue_provider: str
    remote_worker_kind: str | None
    lease_id: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    attempt_count: int
    max_attempts: int
    last_queue_error: str | None
```

In `apps/api/app/ai_company_api/services/cloud_runner.py`, extend `_cloud_run_read()` with the matching values:

```python
        queue_provider=cloud_run.queue_provider,
        remote_worker_kind=cloud_run.remote_worker_kind,
        lease_id=cloud_run.lease_id,
        lease_expires_at=cloud_run.lease_expires_at,
        heartbeat_at=cloud_run.heartbeat_at,
        attempt_count=cloud_run.attempt_count,
        max_attempts=cloud_run.max_attempts,
        last_queue_error=cloud_run.last_queue_error,
```

- [ ] **Step 6: Verify the migration test passes**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_init_db_adds_phase_10a_cloud_run_lease_columns -v
```

Expected: PASS.

- [ ] **Step 7: Run all cloud-run tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "api: add phase 10a cloud run lease fields"
```

---

## Task 2: Lease Schemas and Claim Endpoint

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing claim tests**

Add these tests after `test_process_next_returns_no_content_when_queue_is_empty`:

```python
def test_claim_next_cloud_run_lease_marks_run_running(tmp_path: Path) -> None:
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

    response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["cloud_run"]["id"] == queued["id"]
    assert body["cloud_run"]["status"] == "running"
    assert body["cloud_run"]["worker_id"] == "remote-worker-1"
    assert body["cloud_run"]["remote_worker_kind"] == "remote_stub"
    assert body["cloud_run"]["queue_provider"] == "local_db"
    assert body["cloud_run"]["attempt_count"] == 1
    assert body["lease_id"] == body["cloud_run"]["lease_id"]
    assert body["lease_expires_at"] == body["cloud_run"]["lease_expires_at"]
    assert body["heartbeat_at"] == body["cloud_run"]["heartbeat_at"]
    assert body["cancel_requested"] is False

    second = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-2",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )
    assert second.status_code == 204

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert local_run is not None
    assert local_run.status == "running"
    assert "lease_claimed" in log_events


def test_claim_next_cloud_run_lease_skips_cancelled_and_exhausted_runs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, first_task = create_cloud_task(session)
        second_task = Task(
            project_id=first_task.project_id,
            title="Exhausted queued run",
            role_required="backend",
            status=TaskStatus.CREATED,
            allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
            required_tests=[],
        )
        session.add(second_task)
        session.commit()
        first_task_id = first_task.id
        second_task_id = second_task.id
        repo_id = repository.id

    cancelled = client.post(
        f"/tasks/{first_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    exhausted = client.post(
        f"/tasks/{second_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    client.post(f"/cloud-runs/{cancelled['id']}/cancel")

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        exhausted_run = session.get(CloudRun, exhausted["id"])
        assert exhausted_run is not None
        exhausted_run.attempt_count = exhausted_run.max_attempts
        session.add(exhausted_run)
        session.commit()

    response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 204
```

- [ ] **Step 2: Run the failing claim tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "claim_next_cloud_run_lease" -v
```

Expected: FAIL because `/cloud-run-worker/leases` and lease schemas do not exist.

- [ ] **Step 3: Add lease schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add after `CloudRunResultRead`:

```python
class CloudRunLeaseCreate(BaseModel):
    worker_id: str = Field(min_length=1)
    worker_kind: str = Field(default="remote_stub", min_length=1)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class CloudRunLeaseHeartbeat(BaseModel):
    worker_id: str = Field(min_length=1)
    lease_seconds: int = Field(default=60, ge=1, le=3600)


class CloudRunLeaseRead(BaseModel):
    cloud_run: CloudRunRead
    lease_id: str
    lease_expires_at: datetime
    heartbeat_at: datetime
    attempt_count: int
    cancel_requested: bool
```

`Field` is already imported from `pydantic` in this file; no import change is needed for these schemas.

- [ ] **Step 4: Add service claim functions**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, import `prefixed_id` from entities and `CloudRunLeaseRead` from schemas:

```python
from ai_company_api.models.entities import (
    CloudRun,
    CloudRunLogEntry,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    Repository,
    Task,
    prefixed_id,
    utc_now,
)
```

```python
from ai_company_api.schemas.api import (
    CloudRunCreate,
    CloudRunLeaseRead,
    CloudRunLogEntryRead,
    CloudRunRead,
    CloudRunResultRead,
    PatchArtifactRead,
)
```

Add constants near `CLOUD_RUN_TERMINAL_STATUSES`:

```python
DEFAULT_QUEUE_PROVIDER = "local_db"
DEFAULT_LEASE_SECONDS = 60
```

Add these functions after `process_next_cloud_run`:

```python
def claim_next_cloud_run_lease(
    session: Session,
    *,
    worker_id: str,
    worker_kind: str = "remote_stub",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> CloudRunLeaseRead | None:
    cloud_run = session.exec(
        select(CloudRun)
        .where(
            CloudRun.status == "queued",
            CloudRun.cancel_requested.is_(False),
            CloudRun.attempt_count < CloudRun.max_attempts,
        )
        .order_by(CloudRun.created_at, CloudRun.id)
    ).first()
    if cloud_run is None:
        return None

    now = utc_now()
    lease_id = prefixed_id("lease")
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    if not _claim_cloud_run_lease(
        session,
        cloud_run_id=cloud_run.id,
        worker_id=worker_id,
        worker_kind=worker_kind,
        lease_id=lease_id,
        lease_expires_at=lease_expires_at,
        now=now,
    ):
        session.rollback()
        return None

    session.refresh(cloud_run)
    local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
    local_run.status = "running"
    local_run.updated_at = now
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="lease_claimed",
        message="Cloud run lease claimed.",
        payload={
            "worker_id": worker_id,
            "worker_kind": worker_kind,
            "lease_id_suffix": lease_id[-6:],
            "lease_seconds": lease_seconds,
            "attempt_count": cloud_run.attempt_count,
        },
    )
    session.add(local_run)
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return _cloud_run_lease_read(cloud_run)
```

Add the compare-and-set helper:

```python
def _claim_cloud_run_lease(
    session: Session,
    *,
    cloud_run_id: str,
    worker_id: str,
    worker_kind: str,
    lease_id: str,
    lease_expires_at: datetime,
    now: datetime,
) -> bool:
    result = session.exec(
        update(CloudRun)
        .where(
            CloudRun.id == cloud_run_id,
            CloudRun.status == "queued",
            CloudRun.cancel_requested.is_(False),
            CloudRun.attempt_count < CloudRun.max_attempts,
        )
        .values(
            status="running",
            queue_provider=DEFAULT_QUEUE_PROVIDER,
            remote_worker_kind=worker_kind,
            worker_id=worker_id,
            lease_id=lease_id,
            lease_expires_at=lease_expires_at,
            heartbeat_at=now,
            attempt_count=CloudRun.attempt_count + 1,
            claimed_at=now,
            last_queue_error=None,
            updated_at=now,
        )
    )
    return result.rowcount == 1
```

Add the read mapper near `_cloud_run_read`:

```python
def _cloud_run_lease_read(cloud_run: CloudRun) -> CloudRunLeaseRead:
    if cloud_run.lease_id is None:
        raise HTTPException(status_code=409, detail="Cloud run has no active lease")
    if cloud_run.lease_expires_at is None:
        raise HTTPException(status_code=409, detail="Cloud run has no lease expiry")
    if cloud_run.heartbeat_at is None:
        raise HTTPException(status_code=409, detail="Cloud run has no heartbeat")
    return CloudRunLeaseRead(
        cloud_run=_cloud_run_read(cloud_run),
        lease_id=cloud_run.lease_id,
        lease_expires_at=cloud_run.lease_expires_at,
        heartbeat_at=cloud_run.heartbeat_at,
        attempt_count=cloud_run.attempt_count,
        cancel_requested=cloud_run.cancel_requested,
    )
```

- [ ] **Step 5: Add claim route**

In `apps/api/app/ai_company_api/api/routes.py`, import the new schema and service:

```python
from ai_company_api.schemas.api import (
    CloudRunCreate,
    CloudRunLeaseCreate,
    CloudRunLeaseRead,
    CloudRunLogEntryRead,
    CloudRunRead,
    CloudRunResultRead,
    ...
)
```

```python
from ai_company_api.services.cloud_runner import (
    cancel_cloud_run,
    claim_next_cloud_run_lease,
    get_cloud_run_read,
    list_cloud_run_logs,
    list_cloud_runs,
    process_cloud_run,
    process_next_cloud_run,
    start_cloud_run,
)
```

Add the route before `/cloud-run-worker/process-next`:

```python
@router.post(
    "/cloud-run-worker/leases",
    status_code=status.HTTP_201_CREATED,
    response_model=CloudRunLeaseRead,
)
def post_cloud_run_worker_lease(
    data: CloudRunLeaseCreate,
    session: SessionDep,
) -> CloudRunLeaseRead | Response:
    lease = claim_next_cloud_run_lease(
        session,
        worker_id=data.worker_id,
        worker_kind=data.worker_kind,
        lease_seconds=data.lease_seconds,
    )
    if lease is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return lease
```

- [ ] **Step 6: Verify claim tests pass**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "claim_next_cloud_run_lease" -v
```

Expected: PASS.

- [ ] **Step 7: Run cloud-run tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/api/routes.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "api: add cloud run lease claim endpoint"
```

---

## Task 3: Lease Heartbeat Endpoint

**Files:**
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing heartbeat tests**

Add these tests after the claim tests:

```python
def test_cloud_run_lease_heartbeat_extends_current_lease(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    client.post(f"/tasks/{task_id}/cloud-runs", json={"repo_id": repo_id})
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 30,
        },
    ).json()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "remote-worker-1", "lease_seconds": 120},
    )

    assert response.status_code == 200
    heartbeat = response.json()
    assert heartbeat["lease_id"] == lease["lease_id"]
    assert heartbeat["cloud_run"]["status"] == "running"
    assert heartbeat["cloud_run"]["worker_id"] == "remote-worker-1"
    assert heartbeat["lease_expires_at"] > lease["lease_expires_at"]
    assert heartbeat["heartbeat_at"] >= lease["heartbeat_at"]


def test_cloud_run_lease_heartbeat_rejects_stale_or_wrong_worker(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    client.post(f"/tasks/{task_id}/cloud-runs", json={"repo_id": repo_id})
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 30,
        },
    ).json()

    wrong_worker = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "remote-worker-2", "lease_seconds": 120},
    )
    stale_lease = client.post(
        "/cloud-run-worker/leases/not-current/heartbeat",
        json={"worker_id": "remote-worker-1", "lease_seconds": 120},
    )

    assert wrong_worker.status_code == 409
    assert wrong_worker.json()["detail"] == "Cloud run lease is not current"
    assert stale_lease.status_code == 409
    assert stale_lease.json()["detail"] == "Cloud run lease is not current"
```

- [ ] **Step 2: Run the failing heartbeat tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "lease_heartbeat" -v
```

Expected: FAIL because heartbeat service and route do not exist.

- [ ] **Step 3: Add heartbeat service**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, add:

```python
def heartbeat_cloud_run_lease(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> CloudRunLeaseRead:
    cloud_run = _get_current_cloud_run_lease_or_409(
        session,
        lease_id=lease_id,
        worker_id=worker_id,
    )
    now = utc_now()
    if cloud_run.lease_expires_at is None or cloud_run.lease_expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    cloud_run.heartbeat_at = now
    cloud_run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    cloud_run.updated_at = now
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="lease_heartbeat",
        message="Cloud run lease heartbeat accepted.",
        payload={
            "worker_id": worker_id,
            "lease_id_suffix": lease_id[-6:],
            "lease_seconds": lease_seconds,
            "cancel_requested": cloud_run.cancel_requested,
        },
    )
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return _cloud_run_lease_read(cloud_run)
```

Add the current-lease helper:

```python
def _get_current_cloud_run_lease_or_409(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
) -> CloudRun:
    cloud_run = session.exec(
        select(CloudRun).where(
            CloudRun.lease_id == lease_id,
            CloudRun.worker_id == worker_id,
            CloudRun.status == "running",
            CloudRun.completed_at.is_(None),
        )
    ).first()
    if cloud_run is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    return cloud_run
```

- [ ] **Step 4: Add heartbeat route**

In `apps/api/app/ai_company_api/api/routes.py`, import `CloudRunLeaseHeartbeat` and `heartbeat_cloud_run_lease`.

Add:

```python
@router.post(
    "/cloud-run-worker/leases/{lease_id}/heartbeat",
    response_model=CloudRunLeaseRead,
)
def post_cloud_run_worker_lease_heartbeat(
    lease_id: str,
    data: CloudRunLeaseHeartbeat,
    session: SessionDep,
) -> CloudRunLeaseRead:
    return heartbeat_cloud_run_lease(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
        lease_seconds=data.lease_seconds,
    )
```

- [ ] **Step 5: Verify heartbeat tests pass**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "lease_heartbeat" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add apps/api/app/ai_company_api/api/routes.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "api: add cloud run lease heartbeats"
```

---

## Task 4: Remote Lease Completion and Shared Finalization

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing completion tests**

Add these tests after the heartbeat tests:

```python
def remote_stub_completion_payload(cloud_run_id: str) -> dict:
    return {
        "worker_id": "remote-worker-1",
        "result": {
            "status": "patch_ready",
            "runner_kind": "remote_stub",
            "base_sha": "base123",
            "head_sha": "head456",
            "worktree_ref": f"remote-stub://{cloud_run_id}",
            "summary": "Remote stub produced a patch artifact.",
            "files_changed": ["AI_SCDC_REMOTE_STUB.md"],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "diff --git a/AI_SCDC_REMOTE_STUB.md b/AI_SCDC_REMOTE_STUB.md\n+remote\n",
            "command_results": [],
            "test_command_results": [],
            "failure_reason": None,
        },
    }


def test_complete_current_cloud_run_lease_creates_patch_artifact(
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
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cloud_run"]["status"] == "patch_ready"
    assert body["cloud_run"]["lease_id"] == lease["lease_id"]
    assert body["cloud_run"]["remote_worker_kind"] == "remote_stub"
    assert body["patch_artifact"]["files_changed"] == ["AI_SCDC_REMOTE_STUB.md"]
    assert body["patch_artifact"]["test_result"] == "not_run"

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        task_after_completion = session.get(Task, task_id)
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert task_after_completion is not None
    assert local_run is not None
    assert task_after_completion.status == TaskStatus.PATCH_READY
    assert local_run.status == "patch_ready"
    assert "worker_completed" in log_events
    assert "patch_ready" in log_events


def test_complete_stale_cloud_run_lease_is_rejected_without_artifact(
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

    response = client.post(
        "/cloud-run-worker/leases/not-current/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Cloud run lease is not current"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        artifacts = session.exec(select(PatchArtifact)).all()
    assert artifacts == []
```

- [ ] **Step 2: Run the failing completion tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "complete_current_cloud_run_lease or complete_stale_cloud_run_lease" -v
```

Expected: FAIL because completion schemas/routes do not exist.

- [ ] **Step 3: Add completion schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class CloudRunCommandResultCreate(BaseModel):
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False


class CloudRunExecutionResultCreate(BaseModel):
    status: str
    runner_kind: str
    base_sha: str | None = None
    head_sha: str | None = None
    worktree_ref: str | None = None
    summary: str = ""
    files_changed: list[str] = Field(default_factory=list)
    tests_run: list[str] = Field(default_factory=list)
    test_result: str = "not_run"
    risks: list[str] = Field(default_factory=list)
    diff_text: str = ""
    command_results: list[CloudRunCommandResultCreate] = Field(default_factory=list)
    test_command_results: list[CloudRunCommandResultCreate] = Field(default_factory=list)
    failure_reason: str | None = None


class CloudRunLeaseComplete(BaseModel):
    worker_id: str = Field(min_length=1)
    result: CloudRunExecutionResultCreate
```

- [ ] **Step 4: Extract shared finalization**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, extract the body of `_execute_claimed_cloud_run()` after `execution_result` is created into a new helper:

```python
def _finalize_claimed_cloud_run_result(
    session: Session,
    *,
    cloud_run: CloudRun,
    execution_result: SandboxExecutionResult,
    secrets: list[str],
) -> CloudRunResultRead:
    cloud_run_id = cloud_run.id
    task_id = cloud_run.task_id
    cloud_run, task, repository, local_run = _reload_claimed_cloud_run(
        session,
        cloud_run_id=cloud_run_id,
        task_id=task_id,
        execution_result=execution_result,
    )
    ...
```

Move the existing cancellation rechecks, `_should_create_patch_artifact()`, `_claim_cloud_run_finalization()`, failed-result branch, artifact branch, task transition, and result return into this helper without changing their behavior.

Then make `_execute_claimed_cloud_run()` end with:

```python
    secrets = _redaction_secrets(sandbox_env, repository.repo_url, github_token)
    return _finalize_claimed_cloud_run_result(
        session,
        cloud_run=cloud_run,
        execution_result=execution_result,
        secrets=secrets,
    )
```

- [ ] **Step 5: Add lease completion service**

Import completion schemas:

```python
from ai_company_api.schemas.api import (
    CloudRunCreate,
    CloudRunExecutionResultCreate,
    CloudRunLeaseComplete,
    CloudRunLeaseRead,
    ...
)
```

Add conversion helpers:

```python
def _command_result_from_create(data) -> CommandResult:
    return CommandResult(
        command=data.command,
        exit_code=data.exit_code,
        stdout=data.stdout,
        stderr=data.stderr,
        duration_ms=data.duration_ms,
        timed_out=data.timed_out,
    )


def _sandbox_execution_result_from_create(data: CloudRunExecutionResultCreate) -> SandboxExecutionResult:
    return SandboxExecutionResult(
        status=data.status,
        runner_kind=data.runner_kind,
        base_sha=data.base_sha,
        head_sha=data.head_sha,
        worktree_ref=data.worktree_ref,
        summary=data.summary,
        files_changed=data.files_changed,
        tests_run=data.tests_run,
        test_result=data.test_result,
        risks=data.risks,
        diff_text=data.diff_text,
        command_results=[
            _command_result_from_create(result)
            for result in data.command_results
        ],
        test_command_results=[
            _command_result_from_create(result)
            for result in data.test_command_results
        ],
        failure_reason=data.failure_reason,
    )
```

Add completion:

```python
def complete_cloud_run_lease(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
    result: CloudRunExecutionResultCreate,
) -> CloudRunResultRead:
    cloud_run = _get_current_cloud_run_lease_or_409(
        session,
        lease_id=lease_id,
        worker_id=worker_id,
    )
    execution_result = _sandbox_execution_result_from_create(result)
    repository = get_repository(session, cloud_run.repo_id)
    secrets = _redaction_secrets({}, repository.repo_url, None)
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="worker_completed",
        message="Cloud run worker completion received.",
        payload={
            "worker_id": worker_id,
            "lease_id_suffix": lease_id[-6:],
            "status": execution_result.status,
            "runner_kind": execution_result.runner_kind,
        },
    )
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return _finalize_claimed_cloud_run_result(
        session,
        cloud_run=cloud_run,
        execution_result=execution_result,
        secrets=secrets,
    )
```

- [ ] **Step 6: Add completion route**

In `apps/api/app/ai_company_api/api/routes.py`, import `CloudRunLeaseComplete` and `complete_cloud_run_lease`.

Add:

```python
@router.post(
    "/cloud-run-worker/leases/{lease_id}/complete",
    response_model=CloudRunResultRead,
)
def post_cloud_run_worker_lease_complete(
    lease_id: str,
    data: CloudRunLeaseComplete,
    session: SessionDep,
) -> CloudRunResultRead:
    return complete_cloud_run_lease(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
        result=data.result,
    )
```

- [ ] **Step 7: Verify completion tests pass**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "complete_current_cloud_run_lease or complete_stale_cloud_run_lease" -v
```

Expected: PASS.

- [ ] **Step 8: Run Phase 9 compatibility tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "process_next_queued_fake_cloud_run_creates_patch_artifact or process_specific_docker_cloud_run_preserves_artifact_semantics or running_cancel_request_prevents_artifact_when_worker_finishes" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/api/routes.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "api: complete cloud runs through worker leases"
```

---

## Task 5: Expired Lease Requeue and Attempt Exhaustion

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing requeue tests**

Add:

```python
def test_requeue_expired_cloud_run_lease_returns_run_to_queue(
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
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        cloud_run.lease_expires_at = datetime(2026, 6, 2, tzinfo=timezone.utc)
        session.add(cloud_run)
        session.commit()

    response = client.post(
        "/cloud-run-worker/leases/requeue-expired",
        json={"limit": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [queued["id"]]
    assert body[0]["status"] == "queued"
    assert body[0]["lease_id"] is None
    assert body[0]["worker_id"] is None
    assert body[0]["attempt_count"] == 1

    stale_completion = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )
    assert stale_completion.status_code == 409

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert local_run is not None
    assert local_run.status == "queued"
    assert "lease_expired" in log_events
    assert "run_requeued" in log_events


def test_requeue_expired_cloud_run_lease_fails_at_max_attempts(
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
    client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        cloud_run.attempt_count = cloud_run.max_attempts
        cloud_run.lease_expires_at = datetime(2026, 6, 2, tzinfo=timezone.utc)
        session.add(cloud_run)
        session.commit()

    response = client.post(
        "/cloud-run-worker/leases/requeue-expired",
        json={"limit": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [queued["id"]]
    assert body[0]["status"] == "failed"
    assert body[0]["failure_reason"] == "lease_attempts_exhausted"
    assert body[0]["last_queue_error"] == "lease_attempts_exhausted"
```

- [ ] **Step 2: Run the failing requeue tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "requeue_expired_cloud_run_lease" -v
```

Expected: FAIL because requeue schema/service/route do not exist.

- [ ] **Step 3: Add requeue schema**

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class CloudRunLeaseRequeueExpired(BaseModel):
    limit: int = Field(default=25, ge=1, le=100)
```

- [ ] **Step 4: Add requeue service**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, add:

```python
def requeue_expired_cloud_run_leases(
    session: Session,
    *,
    limit: int = 25,
) -> list[CloudRunRead]:
    now = utc_now()
    expired_runs = session.exec(
        select(CloudRun)
        .where(
            CloudRun.status == "running",
            CloudRun.completed_at.is_(None),
            CloudRun.lease_expires_at.is_not(None),
            CloudRun.lease_expires_at < now,
        )
        .order_by(CloudRun.lease_expires_at, CloudRun.id)
        .limit(limit)
    ).all()

    changed: list[CloudRun] = []
    for cloud_run in expired_runs:
        local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="lease_expired",
            message="Cloud run lease expired.",
            level="warning",
            payload={
                "worker_id": cloud_run.worker_id,
                "lease_id_suffix": cloud_run.lease_id[-6:] if cloud_run.lease_id else None,
                "attempt_count": cloud_run.attempt_count,
                "max_attempts": cloud_run.max_attempts,
            },
        )
        if cloud_run.attempt_count >= cloud_run.max_attempts:
            completed_at = utc_now()
            cloud_run.status = "failed"
            cloud_run.failure_reason = "lease_attempts_exhausted"
            cloud_run.last_queue_error = "lease_attempts_exhausted"
            cloud_run.completed_at = completed_at
            cloud_run.updated_at = completed_at
            local_run.status = "failed"
            local_run.failure_reason = "lease_attempts_exhausted"
            local_run.updated_at = completed_at
            _append_cloud_run_log(
                session,
                cloud_run=cloud_run,
                event="failed",
                message="Cloud run exhausted lease attempts.",
                level="error",
                payload={"failure_reason": "lease_attempts_exhausted"},
            )
        else:
            cloud_run.status = "queued"
            cloud_run.worker_id = None
            cloud_run.lease_id = None
            cloud_run.lease_expires_at = None
            cloud_run.heartbeat_at = None
            cloud_run.last_queue_error = None
            cloud_run.updated_at = utc_now()
            local_run.status = "queued"
            local_run.updated_at = cloud_run.updated_at
            _append_cloud_run_log(
                session,
                cloud_run=cloud_run,
                event="run_requeued",
                message="Cloud run requeued after expired lease.",
                payload={"attempt_count": cloud_run.attempt_count},
            )
        session.add(local_run)
        session.add(cloud_run)
        changed.append(cloud_run)

    session.commit()
    for cloud_run in changed:
        session.refresh(cloud_run)
    return [_cloud_run_read(cloud_run) for cloud_run in changed]
```

- [ ] **Step 5: Add requeue route**

In `apps/api/app/ai_company_api/api/routes.py`, import `CloudRunLeaseRequeueExpired` and `requeue_expired_cloud_run_leases`.

Add before `/{lease_id}` lease routes:

```python
@router.post(
    "/cloud-run-worker/leases/requeue-expired",
    response_model=list[CloudRunRead],
)
def post_cloud_run_worker_requeue_expired_leases(
    data: CloudRunLeaseRequeueExpired,
    session: SessionDep,
) -> list[CloudRunRead]:
    return requeue_expired_cloud_run_leases(session, limit=data.limit)
```

- [ ] **Step 6: Verify requeue tests pass**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "requeue_expired_cloud_run_lease" -v
```

Expected: PASS.

- [ ] **Step 7: Run cloud-run tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/api/routes.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "api: requeue expired cloud run leases"
```

---

## Task 6: Cancellation Visibility Through Heartbeat and Lease Completion

**Files:**
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write cancellation lease tests**

Add:

```python
def test_heartbeat_reports_running_cancel_request(
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
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    cancel = client.post(f"/cloud-runs/{queued['id']}/cancel")

    heartbeat = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "remote-worker-1", "lease_seconds": 60},
    )

    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True
    assert heartbeat.status_code == 200
    assert heartbeat.json()["cancel_requested"] is True
    assert heartbeat.json()["cloud_run"]["cancel_requested"] is True


def test_complete_lease_after_cancel_request_finishes_cancelled_without_artifact(
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
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    client.post(f"/cloud-runs/{queued['id']}/cancel")

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cloud_run"]["status"] == "cancelled"
    assert body["cloud_run"]["cancel_requested"] is True
    assert body["cloud_run"]["cancelled_at"] is not None
    assert body["patch_artifact"] is None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        artifacts = session.exec(select(PatchArtifact)).all()
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
    assert artifacts == []
    assert local_run is not None
    assert local_run.status == "cancelled"
```

- [ ] **Step 2: Run cancellation lease tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "heartbeat_reports_running_cancel_request or complete_lease_after_cancel_request" -v
```

Expected: PASS if earlier lease completion correctly reuses Phase 9 cancellation finalization. If the second test fails by creating a patch artifact, fix `_finalize_claimed_cloud_run_result()` so it reloads and checks `cancel_requested` before `_claim_cloud_run_finalization()`.

- [ ] **Step 3: Commit if code changed**

If the tests pass without production changes:

```bash
git add apps/api/tests/test_cloud_run_api.py
git commit -m "test: cover cloud run lease cancellation"
```

If production code changed:

```bash
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "api: honor cancellation during lease completion"
```

---

## Task 7: Desktop Client Type Compatibility

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/test/client.test.ts`

- [ ] **Step 1: Write failing desktop client test**

In `apps/desktop/src/test/client.test.ts`, add this test immediately after the existing test that calls `client.processCloudRun("cloud_run_api")`, `client.cancelCloudRun("cloud_run_api")`, and `client.listCloudRunLogs("cloud_run_api")`:

```typescript
it("normalizes phase 10a cloud run lease fields", async () => {
  const cloudRun = {
    id: "cloud_run_1",
    workspace_id: "dev_workspace",
    project_id: "project_1",
    task_id: "task_1",
    repo_id: "repo_1",
    local_run_id: "local_run_1",
    sandbox_profile_id: null,
    patch_command_key: null,
    test_command_keys: [],
    command_results: [],
    base_branch: "main",
    head_branch: "ai-scdc/task-task_1-cloud_run_1",
    status: "running",
    sandbox_kind: "fake",
    patch_artifact_id: null,
    failure_reason: null,
    cancel_requested: false,
    cancel_requested_at: null,
    cancelled_at: null,
    worker_id: "remote-worker-1",
    claimed_at: "2026-06-02T00:00:00Z",
    completed_at: null,
    queue_provider: "local_db",
    remote_worker_kind: "remote_stub",
    lease_id: "lease_123",
    lease_expires_at: "2026-06-02T00:01:00Z",
    heartbeat_at: "2026-06-02T00:00:30Z",
    attempt_count: 1,
    max_attempts: 3,
    last_queue_error: null,
    created_at: "2026-06-02T00:00:00Z",
    updated_at: "2026-06-02T00:00:30Z",
  };
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
    jsonResponse({
      cloud_run: cloudRun,
      patch_artifact: null,
    })
  );
  vi.stubGlobal("fetch", fetchMock);

  const client = createHttpApiClient({
    baseUrl: "http://127.0.0.1:8000/",
    projectId: "project_demo",
  });

  const result = await client.processCloudRun("cloud_run_1");

  expect(result.cloud_run.lease_id).toBe("lease_123");
  expect(result.cloud_run.queue_provider).toBe("local_db");
  expect(result.cloud_run.remote_worker_kind).toBe("remote_stub");
  expect(result.cloud_run.attempt_count).toBe(1);
  expect(result.cloud_run.max_attempts).toBe(3);
});
```

- [ ] **Step 2: Run the failing desktop client test**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: FAIL because the TypeScript `CloudRun` type does not include the Phase 10A fields.

- [ ] **Step 3: Extend the TypeScript `CloudRun` type**

In `apps/desktop/src/api/client.ts`, add fields to the `CloudRun` interface:

```typescript
  queue_provider: string;
  remote_worker_kind: string | null;
  lease_id: string | null;
  lease_expires_at: string | null;
  heartbeat_at: string | null;
  attempt_count: number;
  max_attempts: number;
  last_queue_error: string | null;
```

If fake/demo client builders construct `CloudRun` objects, add defaults:

```typescript
queue_provider: "local_db",
remote_worker_kind: null,
lease_id: null,
lease_expires_at: null,
heartbeat_at: null,
attempt_count: 0,
max_attempts: 3,
last_queue_error: null,
```

- [ ] **Step 4: Verify desktop client tests pass**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "desktop: include cloud run lease metadata"
```

---

## Task 8: Documentation and Final Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Modify: `README.md`

- [ ] **Step 1: Update architecture Phase 10A boundary**

In `docs/architecture.md`, add after the Phase 9 boundary:

```markdown
## Phase 10A Boundary

Phase 10A adds a remote-worker control-plane contract for cloud runs. Workers
can claim a renewable lease, heartbeat while executing, complete a current
lease with a sandbox execution result, and requeue expired leases. The default
queue provider remains `local_db`, and the `remote_stub` worker kind exercises
the contract without provisioning remote VMs, containers, object storage, or
live streaming.

Phase 10A keeps the Phase 9 fake and `docker_local` development adapters and
does not add a production queue dependency, cloud runtime, object storage,
credential broker, automatic PR creation, or automatic merge.
```

Update the roadmap completed/future lists so Phase 10A is listed as completed
only after implementation and verification pass.

- [ ] **Step 2: Update status doc**

In `docs/superpowers/status.md`, change current phase to Phase 10A and add a completed item:

```markdown
11. Phase 10A remote worker control plane: local queue adapter, renewable
    worker leases, heartbeats, stale completion rejection, expired lease
    requeue, and remote stub completion contract.
```

Update verification counts after running the final test suite.

- [ ] **Step 3: Update README smoke section**

In `README.md`, add a compact Phase 10A API smoke after the Phase 9 smoke. Include PowerShell steps for:

```powershell
$lease = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/cloud-run-worker/leases" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    worker_id = "remote-worker-smoke"
    worker_kind = "remote_stub"
    lease_seconds = 60
  })

$heartbeat = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/cloud-run-worker/leases/$($lease.lease_id)/heartbeat" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    worker_id = "remote-worker-smoke"
    lease_seconds = 60
  })

$completion = Invoke-RestMethod `
  -Method Post `
  -Uri "$ApiBase/cloud-run-worker/leases/$($lease.lease_id)/complete" `
  -ContentType "application/json" `
  -Body (JsonBody @{
    worker_id = "remote-worker-smoke"
    result = @{
      status = "patch_ready"
      runner_kind = "remote_stub"
      base_sha = $null
      head_sha = $null
      worktree_ref = "remote-stub://$($lease.cloud_run.id)"
      summary = "Remote stub smoke patch."
      files_changed = @("AI_SCDC_REMOTE_STUB.md")
      tests_run = @()
      test_result = "not_run"
      risks = @()
      diff_text = "diff --git a/AI_SCDC_REMOTE_STUB.md b/AI_SCDC_REMOTE_STUB.md`n+remote smoke`n"
      command_results = @()
      test_command_results = @()
      failure_reason = $null
    }
  })
```

- [ ] **Step 4: Run final backend verification**

Run:

```bash
pytest apps/api/tests
```

Expected: all API tests pass.

- [ ] **Step 5: Run final desktop verification**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
```

Expected: all selected desktop tests pass.

- [ ] **Step 6: Run typecheck**

Run:

```bash
pnpm typecheck
```

Expected: TypeScript typecheck passes for `apps/desktop` and `packages/agent-protocol`.

- [ ] **Step 7: Run diff check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. CRLF conversion warnings are acceptable on Windows when the exit code is `0`.

- [ ] **Step 8: Commit docs**

Run:

```bash
git add docs/architecture.md docs/superpowers/status.md README.md
git commit -m "docs: document phase 10a worker leases"
```

---

## Final Review Checklist

- [ ] `pytest apps/api/tests/test_cloud_run_api.py` passes.
- [ ] `pytest apps/api/tests` passes.
- [ ] `pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx` passes.
- [ ] `pnpm typecheck` passes.
- [ ] `git diff --check` passes.
- [ ] Phase 9 process endpoints still return the same successful results.
- [ ] Stale lease completion cannot create a patch artifact.
- [ ] Expired leases below max attempts return to `queued`.
- [ ] Expired leases at max attempts become `failed`.
- [ ] Running cancellation prevents lease completion from creating artifacts.
- [ ] `docs/superpowers/status.md` verification counts match the final command output.
