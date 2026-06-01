# Phase 9 Local Cloud Run Queue Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Cloud Run from synchronous execution into a local queued worker flow: API enqueue returns quickly, a worker endpoint processes queued runs, cancellation is available, and ordered run logs are exposed to the desktop console.

**Architecture:** Keep execution in the existing FastAPI process for Phase 9. `POST /tasks/{task_id}/cloud-runs` creates a durable queued `CloudRun` and matching `LocalTaskRun`; explicit worker endpoints claim and execute queued rows using the existing fake/docker executors. Add a `CloudRunLogEntry` table for ordered audit logs. Desktop shows queued/running/cancelled state, local dev process control, cancellation, and compact logs.

**Tech Stack:** FastAPI, SQLModel, SQLite dev migrations in `init_db()`, existing fake/docker cloud executors, React + TypeScript desktop console, pytest, Vitest.

---

## Task 1: Add Phase 9 Persistence And API Shapes

**Files:**
- `apps/api/app/ai_company_api/models/entities.py`
- `apps/api/app/ai_company_api/models/__init__.py`
- `apps/api/app/ai_company_api/schemas/api.py`
- `apps/api/app/ai_company_api/db/session.py`
- `apps/api/tests/test_cloud_run_api.py`

**Step 1: Write failing model/schema migration coverage**

- [ ] Add a test that starts from a SQLite schema missing Phase 9 fields and verifies `init_db()` creates:
  - `cloudrun.cancel_requested`
  - `cloudrun.cancel_requested_at`
  - `cloudrun.cancelled_at`
  - `cloudrun.worker_id`
  - `cloudrun.claimed_at`
  - `cloudrun.completed_at`
  - `cloudrunlogentry` table

Use SQLAlchemy inspector assertions so the test fails before model and migration changes:

```python
def test_init_db_adds_phase_9_cloud_run_columns_and_log_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "phase9.db"
    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)

    with engine.begin() as connection:
        # Simulate an older dev database.
        connection.exec_driver_sql("ALTER TABLE cloudrun RENAME TO cloudrun_old")
        connection.exec_driver_sql(
            """
            CREATE TABLE cloudrun (
                id VARCHAR NOT NULL PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                project_id VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                repo_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                sandbox_kind VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO cloudrun (
                id, workspace_id, project_id, task_id, repo_id, status, sandbox_kind, created_at, updated_at
            )
            SELECT id, workspace_id, project_id, task_id, repo_id, status, sandbox_kind, created_at, updated_at
            FROM cloudrun_old
            """
        )
        connection.exec_driver_sql("DROP TABLE cloudrun_old")
        connection.exec_driver_sql("DROP TABLE IF EXISTS cloudrunlogentry")

    monkeypatch.setattr(session_module, "engine", engine)

    session_module.init_db()

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cloudrun")}
    assert {
        "cancel_requested",
        "cancel_requested_at",
        "cancelled_at",
        "worker_id",
        "claimed_at",
        "completed_at",
    }.issubset(columns)
    assert "cloudrunlogentry" in inspector.get_table_names()
```

**Step 2: Extend `CloudRun` and add `CloudRunLogEntry`**

- [ ] Add Phase 9 fields to `CloudRun`:

```python
cancel_requested: bool = Field(default=False, index=True)
cancel_requested_at: datetime | None = None
cancelled_at: datetime | None = None
worker_id: str | None = Field(default=None, index=True)
claimed_at: datetime | None = None
completed_at: datetime | None = None
```

- [ ] Add `CloudRunLogEntry`:

```python
class CloudRunLogEntry(SQLModel, table=True):
    id: str = Field(default_factory=uuid_hex, primary_key=True)
    cloud_run_id: str = Field(foreign_key="cloudrun.id", index=True)
    workspace_id: str = Field(foreign_key="workspace.id", index=True)
    level: str = Field(default="info", index=True)
    event: str = Field(index=True)
    message: str
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] Export `CloudRunLogEntry` from `models/__init__.py` if the file re-exports entities.

**Step 3: Extend response schemas**

- [ ] Add fields to `CloudRunRead`:

```python
cancel_requested: bool
cancel_requested_at: datetime | None
cancelled_at: datetime | None
worker_id: str | None
claimed_at: datetime | None
completed_at: datetime | None
```

- [ ] Add `CloudRunLogEntryRead`:

```python
class CloudRunLogEntryRead(BaseModel):
    id: str
    cloud_run_id: str
    level: str
    event: str
    message: str
    payload: dict[str, Any] | None
    created_at: datetime
```

**Step 4: Add SQLite dev upgrade helper**

- [ ] Add `_upgrade_sqlite_cloud_run_phase_9_columns(engine)` with exact `ALTER TABLE` statements:

```python
def _upgrade_sqlite_cloud_run_phase_9_columns(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        inspector = inspect(connection)
        if not _table_exists(inspector, "cloudrun"):
            return
        columns = {column["name"] for column in inspector.get_columns("cloudrun")}
        additions = {
            "cancel_requested": "ALTER TABLE cloudrun ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0",
            "cancel_requested_at": "ALTER TABLE cloudrun ADD COLUMN cancel_requested_at DATETIME",
            "cancelled_at": "ALTER TABLE cloudrun ADD COLUMN cancelled_at DATETIME",
            "worker_id": "ALTER TABLE cloudrun ADD COLUMN worker_id VARCHAR",
            "claimed_at": "ALTER TABLE cloudrun ADD COLUMN claimed_at DATETIME",
            "completed_at": "ALTER TABLE cloudrun ADD COLUMN completed_at DATETIME",
        }
        for column, statement in additions.items():
            if column not in columns:
                connection.exec_driver_sql(statement)
```

- [ ] Call this helper before `SQLModel.metadata.create_all(engine)` in `init_db()`. `create_all()` creates `cloudrunlogentry` when missing.

**Verification:**

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "phase_9 or init_db_adds_phase_9"
```

---

## Task 2: Make Cloud Run Start Enqueue Only

**Files:**
- `apps/api/app/ai_company_api/services/cloud_runner.py`
- `apps/api/app/ai_company_api/api/routes.py`
- `apps/api/tests/test_cloud_run_api.py`

**Step 1: Add failing enqueue tests**

- [ ] Add a fake executor test proving `POST /tasks/{task_id}/cloud-runs` returns a queued run with no artifact and no executor work:

```python
def test_start_cloud_run_enqueues_fake_run_without_executor_work(client: TestClient, seeded_task: TaskSeed) -> None:
    response = client.post(f"/tasks/{seeded_task.task_id}/cloud-runs", json={"sandbox_kind": "fake"})

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["patch_artifact"] is None
    assert body["failure_reason"] is None
    assert body["local_run_id"] is not None
```

- [ ] Add a docker enqueue test that verifies profile and command keys are stored, but no token value appears in the response:

```python
def test_docker_cloud_run_enqueue_stores_metadata_without_token(client: TestClient, docker_seed: DockerSeed) -> None:
    response = client.post(
        f"/tasks/{docker_seed.task_id}/cloud-runs",
        json={
            "sandbox_kind": "docker_local",
            "sandbox_profile_id": docker_seed.profile_id,
            "patch_command_key": "default",
            "test_command_keys": ["unit"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["sandbox_kind"] == "docker_local"
    assert body["sandbox_profile_id"] == docker_seed.profile_id
    assert body["patch_command_key"] == "default"
    assert body["test_command_keys"] == ["unit"]
    assert "token" not in json.dumps(body).lower()
```

**Step 2: Add log helper**

- [ ] Add `_append_cloud_run_log()` to `cloud_runner.py`:

```python
def _append_cloud_run_log(
    session: Session,
    *,
    cloud_run: CloudRun,
    event: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> CloudRunLogEntry:
    entry = CloudRunLogEntry(
        cloud_run_id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        event=event,
        message=message,
        level=level,
        payload=redact_sensitive_values(payload) if payload else None,
    )
    session.add(entry)
    return entry
```

- [ ] Add a small redaction helper in the same module:

```python
SENSITIVE_PAYLOAD_KEYS = {"token", "github_token", "access_token", "authorization", "password", "secret"}


def redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in SENSITIVE_PAYLOAD_KEYS else redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_values(item) for item in value]
    return value
```

**Step 3: Replace synchronous start with enqueue**

- [ ] Rename or replace `start_cloud_run()` with `enqueue_cloud_run()` and keep a compatibility alias only if existing imports require it:

```python
def enqueue_cloud_run(session: Session, *, task_id: str, request: CloudRunStartRequest) -> CloudRunResult:
    task = _get_task_or_404(session, task_id)
    repo = _get_repo_or_404(session, task.repo_id)
    sandbox_kind = request.sandbox_kind or "fake"

    _validate_cloud_run_request(session, repo=repo, sandbox_kind=sandbox_kind, request=request)

    cloud_run = CloudRun(
        workspace_id=task.workspace_id,
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repo.id,
        sandbox_profile_id=request.sandbox_profile_id,
        patch_command_key=request.patch_command_key,
        test_command_keys=request.test_command_keys,
        base_branch=request.base_branch,
        head_branch=request.head_branch,
        status="queued",
        sandbox_kind=sandbox_kind,
    )
    session.add(cloud_run)
    session.flush()

    local_run = LocalTaskRun(
        workspace_id=task.workspace_id,
        project_id=task.project_id,
        task_id=task.id,
        run_type="cloud",
        status="queued",
    )
    session.add(local_run)
    session.flush()

    cloud_run.local_run_id = local_run.id
    _append_cloud_run_log(session, cloud_run=cloud_run, event="queued", message="Cloud run queued.")
    session.commit()
    session.refresh(cloud_run)
    return _cloud_run_result(session, cloud_run)
```

- [ ] Ensure `_validate_cloud_run_request()` validates docker profile and command keys but does not open the GitHub token secret or instantiate an executor.

**Step 4: Update route import and handler**

- [ ] Change `create_cloud_run()` to call `enqueue_cloud_run()` and continue returning HTTP 201.

**Verification:**

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "enqueue or token"
```

---

## Task 3: Add Worker Processing Endpoints

**Files:**
- `apps/api/app/ai_company_api/services/cloud_runner.py`
- `apps/api/app/ai_company_api/api/routes.py`
- `apps/api/tests/test_cloud_run_api.py`

**Step 1: Add failing worker tests**

- [ ] Add a process-next test proving the oldest queued run is executed:

```python
def test_process_next_queued_fake_cloud_run_creates_patch_artifact(client: TestClient, seeded_task: TaskSeed) -> None:
    queued = client.post(f"/tasks/{seeded_task.task_id}/cloud-runs", json={"sandbox_kind": "fake"}).json()

    response = client.post("/cloud-run-worker/process-next", params={"worker_id": "local-test-worker"})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == queued["id"]
    assert body["status"] == "patch_ready"
    assert body["worker_id"] == "local-test-worker"
    assert body["claimed_at"] is not None
    assert body["completed_at"] is not None
    assert body["patch_artifact"]["cloud_run_id"] == queued["id"]
```

- [ ] Add a specific-run processing test for docker_local using the existing stubbed executor pattern from Phase 8:

```python
def test_process_specific_docker_cloud_run_preserves_artifact_semantics(client: TestClient, docker_seed: DockerSeed) -> None:
    queued = client.post(
        f"/tasks/{docker_seed.task_id}/cloud-runs",
        json={
            "sandbox_kind": "docker_local",
            "sandbox_profile_id": docker_seed.profile_id,
            "patch_command_key": "default",
            "test_command_keys": ["unit"],
        },
    ).json()

    response = client.post(f"/cloud-runs/{queued['id']}/process", params={"worker_id": "docker-test-worker"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "patch_ready"
    assert body["sandbox_kind"] == "docker_local"
    assert body["patch_artifact"]["cloud_run_id"] == queued["id"]
```

- [ ] Add a 409 test for processing non-queued runs:

```python
def test_processing_non_queued_cloud_run_returns_conflict(client: TestClient, seeded_task: TaskSeed) -> None:
    queued = client.post(f"/tasks/{seeded_task.task_id}/cloud-runs", json={"sandbox_kind": "fake"}).json()
    first = client.post(f"/cloud-runs/{queued['id']}/process")
    assert first.status_code == 200

    second = client.post(f"/cloud-runs/{queued['id']}/process")
    assert second.status_code == 409
```

**Step 2: Implement claim and process helpers**

- [ ] Add `process_next_cloud_run()`:

```python
def process_next_cloud_run(session: Session, *, worker_id: str = "local-worker") -> CloudRunResult | None:
    cloud_run = session.exec(
        select(CloudRun)
        .where(CloudRun.status == "queued")
        .order_by(CloudRun.created_at, CloudRun.id)
    ).first()
    if cloud_run is None:
        return None
    return process_cloud_run(session, cloud_run_id=cloud_run.id, worker_id=worker_id)
```

- [ ] Add `process_cloud_run()`:

```python
def process_cloud_run(session: Session, *, cloud_run_id: str, worker_id: str = "local-worker") -> CloudRunResult:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    if cloud_run.status != "queued":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cloud run is not queued.")
    if cloud_run.cancel_requested:
        cloud_run.status = "cancelled"
        cloud_run.cancelled_at = utc_now()
        cloud_run.completed_at = cloud_run.cancelled_at
        _append_cloud_run_log(session, cloud_run=cloud_run, event="cancelled", message="Cloud run cancelled before processing.")
        session.commit()
        session.refresh(cloud_run)
        return _cloud_run_result(session, cloud_run)

    now = utc_now()
    cloud_run.status = "running"
    cloud_run.worker_id = worker_id
    cloud_run.claimed_at = now
    cloud_run.updated_at = now
    _set_local_run_status(session, cloud_run.local_run_id, "running")
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="claimed",
        message="Cloud run claimed by local worker.",
        payload={"worker_id": worker_id},
    )
    session.commit()
    session.refresh(cloud_run)
    return _execute_claimed_cloud_run(session, cloud_run=cloud_run)
```

- [ ] Move the existing synchronous executor body into `_execute_claimed_cloud_run()`. Preserve Phase 8 behavior:
  - fake and docker executor selection
  - docker profile command resolution
  - token use only inside docker execution
  - `PatchArtifact` persistence
  - `CloudReview` persistence
  - `WorkflowTestRun` persistence
  - `command_results` storage
  - task status transition to patch ready or failed
  - local task run status transition
  - token redaction in API responses

- [ ] Add execution logs:
  - `started` when executor starts
  - `patch_ready` when patch artifact is created
  - `failed` when executor returns failure
  - `completed` when terminal status is set
  - `cancel_requested` if a running row has the flag when execution completes

Use `finally` or terminal-status branches so `completed_at` is set for all terminal results.

**Step 3: Add routes**

- [ ] Add imports for `process_cloud_run` and `process_next_cloud_run`.
- [ ] Add endpoint returning 204 when no queued run exists:

```python
@router.post("/cloud-run-worker/process-next", response_model=CloudRunResult)
def process_next_cloud_run_endpoint(
    worker_id: str = "local-worker",
    session: Session = Depends(get_session),
) -> CloudRunResult | Response:
    result = process_next_cloud_run(session, worker_id=worker_id)
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result
```

- [ ] Add endpoint:

```python
@router.post("/cloud-runs/{cloud_run_id}/process", response_model=CloudRunResult)
def process_cloud_run_endpoint(
    cloud_run_id: str,
    worker_id: str = "local-worker",
    session: Session = Depends(get_session),
) -> CloudRunResult:
    return process_cloud_run(session, cloud_run_id=cloud_run_id, worker_id=worker_id)
```

**Verification:**

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "process_next or process_specific or non_queued"
```

---

## Task 4: Add Cancellation And Logs API

**Files:**
- `apps/api/app/ai_company_api/services/cloud_runner.py`
- `apps/api/app/ai_company_api/api/routes.py`
- `apps/api/tests/test_cloud_run_api.py`

**Step 1: Add failing cancellation/log tests**

- [ ] Add queued cancellation test:

```python
def test_cancel_queued_cloud_run_prevents_processing(client: TestClient, seeded_task: TaskSeed) -> None:
    queued = client.post(f"/tasks/{seeded_task.task_id}/cloud-runs", json={"sandbox_kind": "fake"}).json()

    cancelled = client.post(f"/cloud-runs/{queued['id']}/cancel").json()

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["cancelled_at"] is not None

    process = client.post(f"/cloud-runs/{queued['id']}/process")
    assert process.status_code == 409
```

- [ ] Add running cancellation service test by placing a row in `running` state directly:

```python
def test_cancel_running_cloud_run_records_cancel_request(session: Session, seeded_task: TaskSeed) -> None:
    cloud_run = CloudRun(
        workspace_id=seeded_task.workspace_id,
        project_id=seeded_task.project_id,
        task_id=seeded_task.task_id,
        repo_id=seeded_task.repo_id,
        status="running",
        sandbox_kind="fake",
    )
    session.add(cloud_run)
    session.commit()

    result = cancel_cloud_run(session, cloud_run_id=cloud_run.id)

    assert result.status == "running"
    assert result.cancel_requested is True
    assert result.cancel_requested_at is not None
```

- [ ] Add ordered logs and redaction test:

```python
def test_cloud_run_logs_are_ordered_and_redacted(client: TestClient, seeded_task: TaskSeed) -> None:
    queued = client.post(f"/tasks/{seeded_task.task_id}/cloud-runs", json={"sandbox_kind": "fake"}).json()
    client.post(f"/cloud-runs/{queued['id']}/process")

    response = client.get(f"/cloud-runs/{queued['id']}/logs")

    assert response.status_code == 200
    body = response.json()
    assert [entry["created_at"] for entry in body] == sorted(entry["created_at"] for entry in body)
    assert "queued" in {entry["event"] for entry in body}
    assert "completed" in {entry["event"] for entry in body}
    assert "token" not in json.dumps(body).lower()
```

**Step 2: Implement cancellation service**

- [ ] Add terminal status constant:

```python
CLOUD_RUN_TERMINAL_STATUSES = {"patch_ready", "failed", "cancelled"}
```

- [ ] Add `cancel_cloud_run()`:

```python
def cancel_cloud_run(session: Session, *, cloud_run_id: str) -> CloudRunRead:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    now = utc_now()
    if cloud_run.status in CLOUD_RUN_TERMINAL_STATUSES:
        return _cloud_run_read(session, cloud_run)

    cloud_run.cancel_requested = True
    cloud_run.cancel_requested_at = cloud_run.cancel_requested_at or now
    cloud_run.updated_at = now

    if cloud_run.status == "queued":
        cloud_run.status = "cancelled"
        cloud_run.cancelled_at = now
        cloud_run.completed_at = now
        _set_local_run_status(session, cloud_run.local_run_id, "cancelled")
        _append_cloud_run_log(session, cloud_run=cloud_run, event="cancelled", message="Queued cloud run cancelled.")
    else:
        _append_cloud_run_log(session, cloud_run=cloud_run, event="cancel_requested", message="Cancellation requested.")

    session.commit()
    session.refresh(cloud_run)
    return _cloud_run_read(session, cloud_run)
```

**Step 3: Implement logs service**

- [ ] Add `list_cloud_run_logs()`:

```python
def list_cloud_run_logs(session: Session, *, cloud_run_id: str) -> list[CloudRunLogEntryRead]:
    _get_cloud_run_or_404(session, cloud_run_id)
    entries = session.exec(
        select(CloudRunLogEntry)
        .where(CloudRunLogEntry.cloud_run_id == cloud_run_id)
        .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
    ).all()
    return [CloudRunLogEntryRead.model_validate(entry) for entry in entries]
```

**Step 4: Add routes**

- [ ] Add:

```python
@router.post("/cloud-runs/{cloud_run_id}/cancel", response_model=CloudRunRead)
def cancel_cloud_run_endpoint(cloud_run_id: str, session: Session = Depends(get_session)) -> CloudRunRead:
    return cancel_cloud_run(session, cloud_run_id=cloud_run_id)


@router.get("/cloud-runs/{cloud_run_id}/logs", response_model=list[CloudRunLogEntryRead])
def list_cloud_run_logs_endpoint(cloud_run_id: str, session: Session = Depends(get_session)) -> list[CloudRunLogEntryRead]:
    return list_cloud_run_logs(session, cloud_run_id=cloud_run_id)
```

**Verification:**

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "cancel or logs"
```

---

## Task 5: Update Existing Backend Tests For Queue Semantics

**Files:**
- `apps/api/tests/test_cloud_run_api.py`
- `apps/api/tests/test_console_api.py`
- Any backend test that calls `POST /tasks/{task_id}/cloud-runs`

**Step 1: Find old synchronous expectations**

- [ ] Run:

```powershell
rg -n "cloud-runs|patch_artifact|patch_ready|start_cloud_run" apps/api/tests
```

**Step 2: Update assertions**

- [ ] For tests that only validate creation, assert:
  - `status == "queued"`
  - `patch_artifact is None`
  - `failure_reason is None`

- [ ] For tests that validate patch artifact, review, workflow test, command result, or token redaction behavior:
  - first enqueue with `POST /tasks/{task_id}/cloud-runs`
  - then process with `POST /cloud-runs/{cloud_run_id}/process`
  - assert terminal result on the process response

Example replacement:

```python
queued = client.post(f"/tasks/{task_id}/cloud-runs", json=request_body).json()
assert queued["status"] == "queued"
assert queued["patch_artifact"] is None

processed = client.post(f"/cloud-runs/{queued['id']}/process").json()
assert processed["status"] == "patch_ready"
assert processed["patch_artifact"] is not None
```

**Step 3: Run backend suite slices**

- [ ] Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py
pytest apps/api/tests/test_console_api.py
```

---

## Task 6: Update Desktop API Client And Fake Client

**Files:**
- `apps/desktop/src/api/client.ts`
- `apps/desktop/src/test/client.test.ts`

**Step 1: Add client tests**

- [ ] Add HTTP client tests for:
  - `startCloudRun()` returns queued result
  - `processCloudRun()` posts to `/cloud-runs/{id}/process`
  - `cancelCloudRun()` posts to `/cloud-runs/{id}/cancel`
  - `listCloudRunLogs()` gets `/cloud-runs/{id}/logs`

Example:

```typescript
it("processes a queued cloud run", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    jsonResponse({
      id: "run-1",
      status: "patch_ready",
      sandbox_kind: "fake",
      patch_artifact: fakePatchArtifact,
    }),
  );
  const client = new ConsoleApiClient("http://api.test", fetchMock);

  const result = await client.processCloudRun("run-1");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://api.test/cloud-runs/run-1/process",
    expect.objectContaining({ method: "POST" }),
  );
  expect(result.status).toBe("patch_ready");
});
```

**Step 2: Extend shared TypeScript types**

- [ ] Add fields to `CloudRunCard`:

```typescript
cancel_requested: boolean;
cancel_requested_at: string | null;
cancelled_at: string | null;
worker_id: string | null;
claimed_at: string | null;
completed_at: string | null;
```

- [ ] Add `CloudRunLogEntryCard`:

```typescript
export interface CloudRunLogEntryCard {
  id: string;
  cloud_run_id: string;
  level: string;
  event: string;
  message: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}
```

**Step 3: Add real client methods**

- [ ] Add interface methods:

```typescript
processCloudRun(cloudRunId: string): Promise<CloudRunResult>;
cancelCloudRun(cloudRunId: string): Promise<CloudRunCard>;
listCloudRunLogs(cloudRunId: string): Promise<CloudRunLogEntryCard[]>;
```

- [ ] Implement:

```typescript
async processCloudRun(cloudRunId: string): Promise<CloudRunResult> {
  return this.request<CloudRunResult>(`/cloud-runs/${cloudRunId}/process`, {
    method: "POST",
  });
}

async cancelCloudRun(cloudRunId: string): Promise<CloudRunCard> {
  return this.request<CloudRunCard>(`/cloud-runs/${cloudRunId}/cancel`, {
    method: "POST",
  });
}

async listCloudRunLogs(cloudRunId: string): Promise<CloudRunLogEntryCard[]> {
  return this.request<CloudRunLogEntryCard[]>(`/cloud-runs/${cloudRunId}/logs`);
}
```

**Step 4: Update fake client**

- [ ] Make fake `startCloudRun()` return queued state:

```typescript
const cloudRun: CloudRunCard = {
  id: `cloud-run-${Date.now()}`,
  workspace_id: task.workspace_id,
  project_id: task.project_id,
  task_id: task.id,
  repo_id: task.repo_id,
  local_run_id: `local-run-${Date.now()}`,
  status: "queued",
  sandbox_kind: "fake",
  patch_artifact_id: null,
  failure_reason: null,
  cancel_requested: false,
  cancel_requested_at: null,
  cancelled_at: null,
  worker_id: null,
  claimed_at: null,
  completed_at: null,
  created_at: nowIso(),
  updated_at: nowIso(),
};
```

- [ ] Add fake `processCloudRun()` that returns `patch_ready` plus the existing fake patch artifact/review/test result structure.
- [ ] Add fake `cancelCloudRun()` returning cancelled for queued runs.
- [ ] Add fake `listCloudRunLogs()` returning ordered entries for queued/claimed/completed states.

**Verification:**

```powershell
npm --prefix apps/desktop test -- --run src/test/client.test.ts
```

---

## Task 7: Update Desktop Queue Controls And Log View

**Files:**
- `apps/desktop/src/App.tsx`
- `apps/desktop/src/components/TaskBoard.tsx`
- `apps/desktop/src/test/App.test.tsx`
- `apps/desktop/src/test/TaskBoard.test.tsx`

**Step 1: Add failing UI tests**

- [ ] Add a test that starting a cloud run shows queued state instead of immediately showing patch ready.
- [ ] Add a test that clicking process on a queued run calls `processCloudRun()` and then shows patch ready.
- [ ] Add a test that clicking cancel on a queued run calls `cancelCloudRun()` and shows cancelled state.
- [ ] Add a test that compact logs render in chronological order when `cloud_run_logs` are present.

Example App test:

```typescript
it("processes a queued cloud run from the task board", async () => {
  const client = createMockClient({
    startCloudRun: vi.fn().mockResolvedValue(queuedCloudRunResult),
    processCloudRun: vi.fn().mockResolvedValue(processedCloudRunResult),
    listCloudRunLogs: vi.fn().mockResolvedValue(cloudRunLogs),
  });
  render(<App apiClient={client} />);

  await user.click(await screen.findByRole("button", { name: /run cloud/i }));
  expect(await screen.findByText(/queued/i)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: /process/i }));
  expect(client.processCloudRun).toHaveBeenCalledWith(queuedCloudRunResult.id);
  expect(await screen.findByText(/patch ready/i)).toBeInTheDocument();
});
```

**Step 2: Extend task view model**

- [ ] Add optional `cloud_run_logs?: CloudRunLogEntryCard[]` wherever task cards are built in `App.tsx`.
- [ ] After `startCloudRun()`, store `cloud_run` on the task and fetch logs with `listCloudRunLogs(result.id)`.
- [ ] After `processCloudRun()` or `cancelCloudRun()`, replace `cloud_run` and refresh logs.

**Step 3: Add App handlers**

- [ ] Add:

```typescript
const handleProcessCloudRun = async (task: TaskCard) => {
  if (!task.cloud_run) return;
  setTaskAction(task.id, "cloud_process");
  try {
    const result = await apiClient.processCloudRun(task.cloud_run.id);
    const logs = await apiClient.listCloudRunLogs(result.id);
    applyCloudRunResult(task.id, result, logs);
  } finally {
    clearTaskAction(task.id);
  }
};

const handleCancelCloudRun = async (task: TaskCard) => {
  if (!task.cloud_run) return;
  setTaskAction(task.id, "cloud_cancel");
  try {
    const cloudRun = await apiClient.cancelCloudRun(task.cloud_run.id);
    const logs = await apiClient.listCloudRunLogs(cloudRun.id);
    updateTaskCloudRun(task.id, cloudRun, logs);
  } finally {
    clearTaskAction(task.id);
  }
};
```

- [ ] Keep existing patch-ready update behavior only after `processCloudRun()` returns a `patch_artifact`.

**Step 4: Add TaskBoard controls**

- [ ] Add props:

```typescript
onProcessCloudRun: (task: TaskCard) => void;
onCancelCloudRun: (task: TaskCard) => void;
```

- [ ] Render controls:
  - `Run cloud` when no `task.cloud_run`
  - `Process` when `task.cloud_run.status === "queued"`
  - `Cancel` when status is `queued` or `running`
  - disabled button state when the matching task action is active

- [ ] Render compact logs:

```tsx
{task.cloud_run_logs?.length ? (
  <ol className="cloud-run-log-list">
    {task.cloud_run_logs.map((entry) => (
      <li key={entry.id}>
        <span>{entry.event}</span>
        <span>{entry.message}</span>
      </li>
    ))}
  </ol>
) : null}
```

**Step 5: Style compactly**

- [ ] Add CSS using existing task-board naming conventions:
  - keep logs small
  - prevent button text wrapping into overlapping content
  - use existing status chip patterns

**Verification:**

```powershell
npm --prefix apps/desktop test -- --run src/test/App.test.tsx src/test/TaskBoard.test.tsx
```

---

## Task 8: Documentation And End-To-End Verification

**Files:**
- `docs/architecture.md`
- `docs/superpowers/status.md`
- `README.md`
- `AI_SCDC_DOCKER_SMOKE.md` if the smoke run output changes

**Step 1: Update docs**

- [ ] In `docs/architecture.md`, mark Phase 9 local queue worker as completed once implementation and verification pass.
- [ ] In `docs/superpowers/status.md`, record:
  - queued enqueue behavior
  - worker endpoints
  - cancellation endpoint
  - log endpoint
  - verification commands and results
- [ ] In `README.md`, document the local Phase 9 operator flow:

```text
1. Create a Cloud Run from the desktop console or API.
2. Process one queued run locally with POST /cloud-run-worker/process-next.
3. Process a specific run with POST /cloud-runs/{cloud_run_id}/process.
4. Cancel a queued/running run with POST /cloud-runs/{cloud_run_id}/cancel.
5. Inspect logs with GET /cloud-runs/{cloud_run_id}/logs.
```

**Step 2: Run verification suite**

- [ ] Backend targeted:

```powershell
pytest apps/api/tests/test_cloud_run_api.py
pytest apps/api/tests/test_console_api.py
```

- [ ] Desktop targeted:

```powershell
npm --prefix apps/desktop test -- --run src/test/client.test.ts src/test/App.test.tsx src/test/TaskBoard.test.tsx
```

- [ ] Full available suites:

```powershell
pytest apps/api/tests
npm --prefix apps/desktop test -- --run
npm --prefix apps/desktop run build
```

- [ ] If the API and desktop dev servers are needed for manual verification, start them on available ports and test the queue flow through the UI.

**Step 3: Final checks**

- [ ] Run:

```powershell
git diff --check
$placeholderPatterns = @(
  "TO" + "DO",
  "TB" + "D",
  "implement " + "later",
  "appropriate " + "error handling",
  "Similar " + "to",
  "Write tests for the " + "above"
)
rg -n ($placeholderPatterns -join "|") apps docs README.md
git status --short
```

- [ ] Request code review using `superpowers:requesting-code-review` before declaring Phase 9 complete.
- [ ] Use `superpowers:verification-before-completion` before the final completion message.

---

## Acceptance Criteria

- [ ] `POST /tasks/{task_id}/cloud-runs` returns `201` with `status="queued"` and `patch_artifact=null`.
- [ ] Enqueue path performs validation but does not run fake/docker executors.
- [ ] `POST /cloud-run-worker/process-next` processes the oldest queued run and returns `204` when none are queued.
- [ ] `POST /cloud-runs/{cloud_run_id}/process` claims and processes exactly that queued run.
- [ ] Non-queued process attempts return `409`.
- [ ] `POST /cloud-runs/{cloud_run_id}/cancel` cancels queued runs and records cancellation requests on running runs.
- [ ] `GET /cloud-runs/{cloud_run_id}/logs` returns ordered, token-redacted log entries.
- [ ] Phase 8 patch artifact, review, workflow test, command result, and token-redaction behavior still passes after worker processing.
- [ ] Desktop console supports queued state, manual processing, cancellation, and compact logs.
- [ ] Backend and desktop targeted tests pass.

## Execution Choice

After saving this plan, ask the user to choose one:

1. `subagent-driven-development` - recommended for Phase 9 because backend persistence/service work and desktop UI/client work can be implemented in parallel after the shared contract is clear.
2. `executing-plans` - single-agent sequential execution with review checkpoints.
