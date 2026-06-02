# Phase 10B Provider-Neutral Remote Contract Implementation Plan

> **For:** AI-SCDC Phase 10B provider-neutral remote execution contract
> **Created:** 2026-06-03
> **Spec:** `docs/superpowers/specs/2026-06-03-phase-10b-provider-neutral-remote-contract-design.md`
> **Branch:** `codex/phase-10b-provider-neutral-remote-contract`
> **Worktree:** `.worktrees\phase-10b-provider-neutral-remote-contract`

## Goal

Add provider-neutral contracts for queue, object storage, and remote runtime providers without integrating a real cloud vendor. Phase 10B must preserve Phase 9 local execution and Phase 10A worker lease behavior while adding deterministic stub/provider surfaces that can support future production backends.

## Constraints

- Do not add real cloud SDKs, credentials, or network calls.
- Keep existing Phase 9/10A API behavior compatible by defaulting to `local_db`.
- Do not expose `queue_receipt` in standard cloud run read responses.
- Validate provider names at API boundaries and return non-secret 400 errors for unknown providers.
- Prefer redacted, non-sensitive logs and responses for external URIs/errors.
- Use TDD for each implementation task: add failing tests first, implement minimal code, then verify the focused test set.

## Task 1: Add Phase 10B CloudRun Provider Metadata

### Tests

Add these API tests in `apps/api/tests/test_cloud_run_api.py`.

```python
def test_start_cloud_run_accepts_phase_10b_provider_metadata(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": "repo-phase-10b",
            "base_sha": "abc123",
            "prompt": "Run remote contract",
            "queue_provider": "local_db",
            "runtime_provider": "remote_stub",
            "storage_provider": "local_inline",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["queue_provider"] == "local_db"
    assert payload["runtime_provider"] == "remote_stub"
    assert payload["storage_provider"] == "local_inline"
    assert payload["queue_message_id"] is None
    assert payload["runtime_job_id"] is None
    assert payload["artifact_manifest_uri"] is None
    assert payload["log_stream_uri"] is None
    assert payload["external_status"] is None
    assert payload["external_error"] is None
    assert "queue_receipt" not in payload
```

Add this SQLite upgrade test in `apps/api/tests/test_db_session.py` if the file already hosts migration tests; otherwise add it near the existing cloud run migration tests.

```python
def test_init_db_adds_phase_10b_cloud_run_provider_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "phase10b.sqlite"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE cloudrun (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                status TEXT NOT NULL,
                runner_kind TEXT NOT NULL,
                queue_provider TEXT DEFAULT 'local_db'
            )
            """
        )

    init_db(str(db_path))

    with engine.connect() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(cloudrun)").fetchall()
    names = {row[1] for row in rows}
    assert {
        "queue_message_id",
        "queue_receipt",
        "runtime_provider",
        "runtime_job_id",
        "storage_provider",
        "artifact_manifest_uri",
        "log_stream_uri",
        "external_status",
        "external_error",
    }.issubset(names)
```

Add a desktop client mapping test in `apps/desktop/src/test/client.test.ts`.

```ts
it("maps phase 10b cloud run provider metadata", () => {
  const card = mapCloudRunCard({
    ...baseCloudRun,
    queue_provider: "local_db",
    queue_message_id: "message-1",
    runtime_provider: "remote_stub",
    runtime_job_id: "job-1",
    storage_provider: "local_inline",
    artifact_manifest_uri: "local-inline://cloud-run-objects/manifest",
    log_stream_uri: "local-inline://cloud-run-objects/log",
    external_status: "submitted",
    external_error: "redacted error",
  });

  expect(card.queueProvider).toBe("local_db");
  expect(card.queueMessageId).toBe("message-1");
  expect(card.runtimeProvider).toBe("remote_stub");
  expect(card.runtimeJobId).toBe("job-1");
  expect(card.storageProvider).toBe("local_inline");
  expect(card.artifactManifestUri).toBe("local-inline://cloud-run-objects/manifest");
  expect(card.logStreamUri).toBe("local-inline://cloud-run-objects/log");
  expect(card.externalStatus).toBe("submitted");
  expect(card.externalError).toBe("redacted error");
});
```

Run the focused failing tests:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k phase_10b_provider_metadata
pytest apps/api/tests/test_db_session.py -k phase_10b_cloud_run_provider_columns
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts -t "phase 10b cloud run provider metadata"
```

### Implementation

Update `apps/api/app/ai_company_api/models/entities.py`.

- Add these nullable fields to `CloudRun`:
  - `queue_message_id: str | None = Field(default=None, index=True)`
  - `queue_receipt: str | None = Field(default=None)`
  - `runtime_provider: str | None = Field(default=None, index=True)`
  - `runtime_job_id: str | None = Field(default=None, index=True)`
  - `storage_provider: str | None = Field(default=None, index=True)`
  - `artifact_manifest_uri: str | None = Field(default=None)`
  - `log_stream_uri: str | None = Field(default=None)`
  - `external_status: str | None = Field(default=None, index=True)`
  - `external_error: str | None = Field(default=None)`

Update `apps/api/app/ai_company_api/schemas/api.py`.

- Add request fields to `CloudRunCreate`:
  - `queue_provider: str = "local_db"`
  - `runtime_provider: str | None = None`
  - `storage_provider: str | None = None`
- Add non-sensitive response fields to `CloudRunRead`:
  - `queue_message_id`
  - `runtime_provider`
  - `runtime_job_id`
  - `storage_provider`
  - `artifact_manifest_uri`
  - `log_stream_uri`
  - `external_status`
  - `external_error`
- Do not add `queue_receipt` to `CloudRunRead`.

Update `apps/api/app/ai_company_api/db/session.py`.

- Add `_upgrade_sqlite_cloud_run_phase_10b_columns(engine: Engine) -> None`.
- Call it from `init_db()` after `_upgrade_sqlite_cloud_run_phase_10a_columns(engine)` and before `SQLModel.metadata.create_all(engine)`.
- Add the nine new columns only if absent.
- Add indexes for:
  - `queue_message_id`
  - `runtime_provider`
  - `runtime_job_id`
  - `storage_provider`
  - `external_status`

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- In `enqueue_cloud_run()`, persist `queue_provider`, `runtime_provider`, and `storage_provider` from `CloudRunCreate`.
- In `_cloud_run_read()`, map the new read fields.

Update desktop files:

- `apps/desktop/src/api/client.ts`
  - Extend `CloudRunCard` with camelCase Phase 10B fields.
  - Extend API response type with snake_case Phase 10B fields.
  - Map new fields in `mapCloudRunCard()`.
  - Set defaults in `fakeCloudRunFromInput()`.
- `apps/desktop/src/test/client.test.ts`
  - Add `maps phase 10b cloud run provider metadata` near the Phase 10A mapping tests.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k phase_10b_provider_metadata
pytest apps/api/tests/test_db_session.py -k phase_10b_cloud_run_provider_columns
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts -t "phase 10b cloud run provider metadata"
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_db_session.py apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "feat: add phase 10b provider metadata"
```

## Task 2: Add Local Inline Object Storage Contract

### Tests

Create `apps/api/tests/test_cloud_object_storage.py`.

```python
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlmodel import Session

from ai_company_api.services.object_storage import (
    ObjectStorageReadError,
    ObjectStorageWrite,
    get_object_storage_provider,
)


def test_local_inline_storage_puts_and_reads_text_ref(session: Session) -> None:
    provider = get_object_storage_provider("local_inline")
    workspace_id = uuid4()
    cloud_run_id = uuid4()
    text = "diff --git a/file.txt b/file.txt\n+hello\n"

    ref = provider.put_text(
        session,
        ObjectStorageWrite(
            workspace_id=workspace_id,
            cloud_run_id=cloud_run_id,
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


def test_local_inline_storage_rejects_hash_mismatch(session: Session) -> None:
    provider = get_object_storage_provider("local_inline")
    ref = provider.put_text(
        session,
        ObjectStorageWrite(
            workspace_id=uuid4(),
            cloud_run_id=uuid4(),
            kind="log",
            content="safe log",
            content_type="text/plain",
        ),
    )
    ref.sha256 = "0" * 64

    with pytest.raises(ObjectStorageReadError):
        provider.read_text(session, ref)
```

Run the failing tests:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py
```

### Implementation

Update `apps/api/app/ai_company_api/models/entities.py`.

- Add a `CloudRunStoredObject` table with these fields:
  - `id: UUID`
  - `workspace_id: UUID = Field(foreign_key="workspace.id", index=True)`
  - `cloud_run_id: UUID = Field(foreign_key="cloudrun.id", index=True)`
  - `kind: str = Field(index=True)`
  - `uri: str = Field(index=True, unique=True)`
  - `sha256: str`
  - `size_bytes: int`
  - `content_type: str = "text/plain"`
  - `text_content: str`
  - `created_at: datetime`

Create `apps/api/app/ai_company_api/services/object_storage.py`.

- Define:
  - `ARTIFACT_KINDS = {"diff", "log", "command_result", "test_result", "manifest"}`
  - `ObjectStorageError`
  - `ObjectStorageReadError`
  - `ObjectStorageProviderNotFound`
  - `ObjectStorageWrite`
  - `ObjectStorageRef`
  - `ObjectStorageProvider` protocol
  - `LocalInlineObjectStorageProvider`
  - `get_object_storage_provider(name: str) -> ObjectStorageProvider`
- Generate URIs as `local-inline://cloud-run-objects/{stored_object.id}`.
- `put_text()` must compute SHA-256 and byte size from UTF-8 encoded content.
- `read_text()` must:
  - accept only `local-inline://cloud-run-objects/{uuid}` URIs;
  - verify stored object exists;
  - verify kind, SHA-256, and size match the provided ref;
  - raise `ObjectStorageReadError` on mismatch.

Update `apps/api/app/ai_company_api/schemas/api.py`.

- Add `CloudRunArtifactRefCreate`:

```python
class CloudRunArtifactRefCreate(BaseModel):
    kind: Literal["diff", "log", "command_result", "test_result", "manifest"]
    uri: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int = Field(ge=0)
    content_type: str = "text/plain"
```

### Verify

```powershell
pytest apps/api/tests/test_cloud_object_storage.py
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/object_storage.py apps/api/tests/test_cloud_object_storage.py
git commit -m "feat: add local inline cloud object storage"
```

## Task 3: Resolve Artifact References During Remote Completion

### Tests

Add these tests to `apps/api/tests/test_cloud_run_api.py` near the Phase 10A lease completion tests.

```python
def test_complete_cloud_run_lease_uses_diff_artifact_ref(client: TestClient, session: Session) -> None:
    project_id, task_id = _create_project_and_task(client)
    run = _start_cloud_run(client, task_id, storage_provider="local_inline")
    lease = _claim_cloud_run_lease(client)
    diff_text = "diff --git a/app.py b/app.py\n+print('artifact')\n"
    ref = get_object_storage_provider("local_inline").put_text(
        session,
        ObjectStorageWrite(
            workspace_id=UUID(run["workspace_id"]),
            cloud_run_id=UUID(run["id"]),
            kind="diff",
            content=diff_text,
            content_type="text/x-diff",
        ),
    )

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json={
            "worker_id": lease["worker_id"],
            "result": {
                "status": "patch_ready",
                "runner_kind": "remote_stub",
                "base_sha": "abc123",
                "head_sha": "def456",
                "worktree_ref": "remote-job-1",
                "summary": "Completed from artifact ref",
                "files_changed": ["app.py"],
                "tests_run": ["pytest"],
                "test_result": "passed",
                "risks": [],
                "diff_text": "diff --git a/ignored.py b/ignored.py\n+ignored\n",
                "artifact_refs": [ref.model_dump()],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "patch_ready"
    artifact_response = client.get(f"/artifacts/{payload['artifact_id']}")
    assert artifact_response.status_code == 200
    assert artifact_response.json()["diff_text"] == diff_text
    log_messages = [entry["message"] for entry in payload["logs"]]
    assert any("artifact_ref" in message for message in log_messages)


def test_complete_cloud_run_lease_rejects_invalid_artifact_ref_without_artifact(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)
    _start_cloud_run(client, task_id, storage_provider="local_inline")
    lease = _claim_cloud_run_lease(client)

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json={
            "worker_id": lease["worker_id"],
            "result": {
                "status": "patch_ready",
                "runner_kind": "remote_stub",
                "base_sha": "abc123",
                "head_sha": "def456",
                "worktree_ref": "remote-job-1",
                "summary": "Invalid artifact ref",
                "files_changed": ["app.py"],
                "tests_run": ["pytest"],
                "test_result": "passed",
                "risks": [],
                "artifact_refs": [
                    {
                        "kind": "diff",
                        "uri": "local-inline://cloud-run-objects/00000000-0000-0000-0000-000000000000",
                        "sha256": "0" * 64,
                        "size_bytes": 10,
                        "content_type": "text/x-diff",
                    }
                ],
            },
        },
    )

    assert response.status_code == 400
    artifacts = client.get(f"/tasks/{task_id}/artifacts").json()
    assert artifacts == []
```

Run the focused failing tests:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "artifact_ref"
```

### Implementation

Update `apps/api/app/ai_company_api/schemas/api.py`.

- Add `artifact_refs: list[CloudRunArtifactRefCreate] = Field(default_factory=list)` to `CloudRunExecutionResultCreate`.

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- Import `CloudRunArtifactRefCreate`, `ObjectStorageReadError`, `ObjectStorageRef`, and `get_object_storage_provider`.
- Add `_resolve_cloud_run_completion_artifacts(session: Session, cloud_run: CloudRun, result: CloudRunExecutionResultCreate) -> CloudRunExecutionResultCreate`.
- For each ref:
  - Choose provider from URI scheme:
    - `local-inline://` maps to `local_inline`.
  - Reject unsupported schemes as a bad request.
  - Resolve only known artifact kinds.
- If a `diff` ref exists:
  - Read text from storage.
  - Replace `result.diff_text` with the resolved artifact content before `_sandbox_execution_result_from_create()`.
  - Append a cloud run log such as `Remote completion diff resolved from artifact_ref`.
  - If inline `diff_text` is also present, append a cloud run log such as `Inline diff_text ignored because diff artifact_ref was provided`.
- If a ref fails validation, return HTTP 400 and do not finalize the run or create a patch artifact.
- Keep existing inline `diff_text` behavior when no `diff` artifact ref is provided.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "artifact_ref"
pytest apps/api/tests/test_cloud_run_api.py -k "complete_cloud_run_lease"
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: resolve remote completion artifact refs"
```

## Task 4: Validate Provider Names at API Boundaries

### Tests

Add these tests in `apps/api/tests/test_cloud_run_api.py`.

```python
def test_start_cloud_run_rejects_unknown_queue_provider(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": "repo-phase-10b",
            "base_sha": "abc123",
            "prompt": "Unknown queue provider",
            "queue_provider": "aws_sqs",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown cloud queue provider: aws_sqs"


def test_start_cloud_run_rejects_unknown_storage_provider(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": "repo-phase-10b",
            "base_sha": "abc123",
            "prompt": "Unknown storage provider",
            "storage_provider": "s3",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown object storage provider: s3"


def test_start_cloud_run_rejects_unknown_runtime_provider(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": "repo-phase-10b",
            "base_sha": "abc123",
            "prompt": "Unknown runtime provider",
            "runtime_provider": "cloud_run_jobs",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown remote runtime provider: cloud_run_jobs"
```

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "unknown_provider"
```

### Implementation

Create `apps/api/app/ai_company_api/services/remote_runtime.py`.

- Define:
  - `RemoteRuntimeProviderNotFound`
  - `RemoteRuntimeProvider` protocol
  - `RemoteRuntimeSubmission`
  - `RemoteRuntimeSubmissionResult`
  - `RemoteStubRuntimeProvider`
  - `get_remote_runtime_provider(name: str | None) -> RemoteRuntimeProvider | None`
- Known runtime providers:
  - `None`
  - `"remote_stub"`

Create `apps/api/app/ai_company_api/services/cloud_queue_providers.py`.

- Define:
  - `CloudQueueProviderNotFound`
  - `CloudQueueProvider` protocol
  - `get_cloud_queue_provider(name: str) -> CloudQueueProvider`
- Known queue providers:
  - `"local_db"`
  - `"external_stub"`
- The protocol methods can be thin markers at this stage because the lifecycle dispatch is added in Tasks 5 and 6.

Update `apps/api/app/ai_company_api/services/object_storage.py`.

- Ensure `get_object_storage_provider(name: str | None)` accepts:
  - `None`
  - `"local_inline"`
- Unknown storage provider names raise `ObjectStorageProviderNotFound`.

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- In `enqueue_cloud_run()`, validate:
  - `cloud_run_create.queue_provider`
  - `cloud_run_create.storage_provider`
  - `cloud_run_create.runtime_provider`
- Convert provider-not-found exceptions into `HTTPException(status_code=400, detail=...)`.
- Preserve default behavior for omitted provider fields.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "unknown_provider or phase_10b_provider_metadata"
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/services/cloud_queue_providers.py apps/api/app/ai_company_api/services/remote_runtime.py apps/api/app/ai_company_api/services/object_storage.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: validate cloud provider selections"
```

## Task 5: Introduce Queue Provider Dispatch for Local DB

### Tests

Add regression tests in `apps/api/tests/test_cloud_run_api.py`.

```python
def test_local_db_queue_provider_preserves_claim_heartbeat_complete_flow(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)
    run = _start_cloud_run(client, task_id, queue_provider="local_db")

    lease_response = client.post(
        "/cloud-run-worker/leases",
        json={"worker_id": "worker-local-db", "queue_provider": "local_db"},
    )
    assert lease_response.status_code == 200
    lease = lease_response.json()
    assert lease["cloud_run_id"] == run["id"]

    heartbeat_response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "worker-local-db"},
    )
    assert heartbeat_response.status_code == 200

    complete_response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json={
            "worker_id": "worker-local-db",
            "result": _patch_ready_result_payload(),
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "patch_ready"


def test_local_db_requeue_filters_by_queue_provider(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)
    _start_cloud_run(client, task_id, queue_provider="local_db")
    _start_cloud_run(client, task_id, queue_provider="external_stub")

    lease = client.post(
        "/cloud-run-worker/leases",
        json={"worker_id": "worker-local-db", "queue_provider": "local_db", "lease_seconds": 1},
    ).json()
    _expire_cloud_run_lease(lease["cloud_run_id"])

    response = client.post(
        "/cloud-run-worker/leases/requeue-expired",
        json={"worker_id": "worker-local-db", "queue_provider": "local_db"},
    )

    assert response.status_code == 200
    assert response.json()["requeued"] == 1
```

Update `CloudRunLeaseCreate` tests to pass `queue_provider` only where needed; existing tests must continue to pass without it because the default remains `local_db`.

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "local_db_queue_provider or local_db_requeue_filters"
```

### Implementation

Update `apps/api/app/ai_company_api/schemas/api.py`.

- Add `queue_provider: str = "local_db"` to:
  - `CloudRunLeaseCreate`
  - `CloudRunLeaseRequeueExpired`
- Add `queue_provider: str` and `queue_message_id: str | None = None` to `CloudRunLeaseRead`.
- Do not add `queue_receipt` to public task/project read schemas.

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- Rename the current local DB lifecycle functions internally:
  - `claim_next_cloud_run_lease()` -> `_claim_next_cloud_run_lease_local_db()`
  - `heartbeat_cloud_run_lease()` -> `_heartbeat_cloud_run_lease_local_db()`
  - `requeue_expired_cloud_run_leases()` -> `_requeue_expired_cloud_run_leases_local_db()`
  - `complete_cloud_run_lease()` -> `_complete_cloud_run_lease_local_db()`
- Recreate public functions with the original names as dispatchers:
  - `claim_next_cloud_run_lease(session, lease_create)`
  - `heartbeat_cloud_run_lease(session, lease_id, heartbeat)`
  - `requeue_expired_cloud_run_leases(session, requeue_request)`
  - `complete_cloud_run_lease(session, lease_id, completion)`
- In `claim_next_cloud_run_lease()`:
  - Validate `lease_create.queue_provider` with `get_cloud_queue_provider()`.
  - For `local_db`, call `_claim_next_cloud_run_lease_local_db()`.
  - Filter claim candidates by `CloudRun.queue_provider == lease_create.queue_provider`.
- In `requeue_expired_cloud_run_leases()`:
  - Validate `requeue_request.queue_provider`.
  - For `local_db`, call `_requeue_expired_cloud_run_leases_local_db()`.
  - Filter expired leases by `CloudRun.queue_provider == requeue_request.queue_provider`.
- In `_cloud_run_lease_read()`, include `queue_provider` and `queue_message_id`.
- Ensure existing route functions in `apps/api/app/ai_company_api/api/routes.py` keep calling the public function names, so route code remains stable.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "lease or requeue"
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: dispatch local cloud queue provider"
```

## Task 6: Add External Stub Queue Provider

### Tests

Add these tests in `apps/api/tests/test_cloud_run_api.py`.

```python
def test_external_stub_queue_provider_claims_with_external_metadata(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)
    run = _start_cloud_run(client, task_id, queue_provider="external_stub")

    response = client.post(
        "/cloud-run-worker/leases",
        json={"worker_id": "worker-external", "queue_provider": "external_stub"},
    )

    assert response.status_code == 200
    lease = response.json()
    assert lease["cloud_run_id"] == run["id"]
    assert lease["queue_provider"] == "external_stub"
    assert lease["queue_message_id"].startswith("external-stub-message-")

    read_response = client.get(f"/tasks/{task_id}/cloud-runs/{run['id']}")
    assert read_response.status_code == 200
    payload = read_response.json()
    assert payload["queue_message_id"] == lease["queue_message_id"]
    assert payload["external_status"] == "claimed"
    assert "queue_receipt" not in payload


def test_external_stub_requeue_marks_external_status_without_leaking_receipt(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)
    run = _start_cloud_run(client, task_id, queue_provider="external_stub")
    lease = client.post(
        "/cloud-run-worker/leases",
        json={"worker_id": "worker-external", "queue_provider": "external_stub", "lease_seconds": 1},
    ).json()
    _expire_cloud_run_lease(lease["cloud_run_id"])

    response = client.post(
        "/cloud-run-worker/leases/requeue-expired",
        json={"worker_id": "worker-external", "queue_provider": "external_stub"},
    )

    assert response.status_code == 200
    assert response.json()["requeued"] == 1
    payload = client.get(f"/tasks/{task_id}/cloud-runs/{run['id']}").json()
    assert payload["external_status"] == "requeued"
    assert "queue_receipt" not in payload
```

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "external_stub"
```

### Implementation

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- Add constants:
  - `EXTERNAL_STUB_QUEUE_PROVIDER = "external_stub"`
  - `EXTERNAL_STUB_MESSAGE_PREFIX = "external-stub-message-"`
  - `EXTERNAL_STUB_RECEIPT_PREFIX = "external-stub-receipt-"`
- In `enqueue_cloud_run()`:
  - For `queue_provider == "external_stub"`, set `external_status = "queued"`.
  - Leave `queue_message_id` and `queue_receipt` unset until claim.
- In `claim_next_cloud_run_lease()`:
  - For `external_stub`, reuse the local DB lease transaction mechanics but filter by `external_stub`.
  - When a run is claimed and `queue_message_id` is absent, set:
    - `queue_message_id = f"external-stub-message-{cloud_run.id}"`
    - `queue_receipt = f"external-stub-receipt-{lease_id}"`
    - `external_status = "claimed"`
  - Return `queue_message_id` in `CloudRunLeaseRead`.
  - Do not return `queue_receipt`.
- In `requeue_expired_cloud_run_leases()`:
  - For `external_stub`, use the same expired lease mechanics as local DB.
  - Set `external_status = "requeued"` and rotate `queue_receipt` to `None`.
- In `complete_cloud_run_lease()`:
  - For `external_stub` runs, clear `queue_receipt` after successful completion and set `external_status = "completed"` or `"failed"` based on result status.
- Add log entries for `external_stub` claim, requeue, and completion using non-sensitive message IDs.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "external_stub or lease or requeue"
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add external stub queue provider"
```

## Task 7: Add Remote Runtime Stub Submission

### Tests

Add this test in `apps/api/tests/test_cloud_run_api.py`.

```python
def test_remote_stub_runtime_submission_records_job_metadata(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": "repo-phase-10b",
            "base_sha": "abc123",
            "prompt": "Submit remote runtime",
            "queue_provider": "external_stub",
            "runtime_provider": "remote_stub",
            "storage_provider": "local_inline",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["runtime_provider"] == "remote_stub"
    assert payload["runtime_job_id"].startswith("remote-stub-job-")
    assert payload["external_status"] == "submitted"
    assert payload["log_stream_uri"].startswith("local-inline://cloud-run-objects/")
    assert payload["artifact_manifest_uri"].startswith("local-inline://cloud-run-objects/")
```

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "remote_stub_runtime"
```

### Implementation

Update `apps/api/app/ai_company_api/services/remote_runtime.py`.

- Implement `RemoteStubRuntimeProvider.submit(session, submission)`.
- The stub must not execute code or make network calls.
- It returns:
  - `runtime_job_id = f"remote-stub-job-{cloud_run_id}"`
  - `external_status = "submitted"`
  - `artifact_manifest_uri`
  - `log_stream_uri`
- It writes manifest and log seed objects through `local_inline` storage when `storage_provider == "local_inline"`.
- The manifest content must be JSON text containing:
  - `cloud_run_id`
  - `queue_provider`
  - `runtime_provider`
  - `storage_provider`
  - `status`

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- After creating a queued cloud run, call the runtime provider only when `runtime_provider` is not `None`.
- Persist `runtime_job_id`, `artifact_manifest_uri`, `log_stream_uri`, and `external_status`.
- Append a cloud run log such as `Remote runtime submitted via remote_stub`.
- Keep omitted runtime provider behavior unchanged.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "remote_stub_runtime or phase_10b_provider_metadata"
pytest apps/api/tests/test_cloud_object_storage.py
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/services/remote_runtime.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add remote runtime stub submission"
```

## Task 8: Add Redaction and Payload Size Guards

### Tests

Add these tests in `apps/api/tests/test_cloud_run_api.py`.

```python
def test_cloud_run_read_redacts_external_uri_query_and_external_error(client: TestClient, session: Session) -> None:
    project_id, task_id = _create_project_and_task(client)
    run = _start_cloud_run(client, task_id, queue_provider="external_stub")
    cloud_run = session.get(CloudRun, UUID(run["id"]))
    assert cloud_run is not None
    cloud_run.log_stream_uri = "local-inline://cloud-run-objects/log?token=secret#frag"
    cloud_run.artifact_manifest_uri = "local-inline://cloud-run-objects/manifest?sig=secret"
    cloud_run.external_error = "failed with token=abc123 and Authorization: Bearer secret-token"
    session.add(cloud_run)
    session.commit()

    response = client.get(f"/tasks/{task_id}/cloud-runs/{run['id']}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["log_stream_uri"] == "local-inline://cloud-run-objects/log"
    assert payload["artifact_manifest_uri"] == "local-inline://cloud-run-objects/manifest"
    assert "secret" not in payload["external_error"].lower()
    assert "token=abc123" not in payload["external_error"]


def test_complete_cloud_run_lease_rejects_oversized_summary(client: TestClient) -> None:
    project_id, task_id = _create_project_and_task(client)
    _start_cloud_run(client, task_id)
    lease = _claim_cloud_run_lease(client)
    payload = _patch_ready_result_payload()
    payload["summary"] = "x" * (16 * 1024 + 1)

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json={"worker_id": lease["worker_id"], "result": payload},
    )

    assert response.status_code == 422
```

Add a unit test for command output size guards if command result schemas have direct model tests in the repo; otherwise add it through the lease completion endpoint.

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "redacts_external_uri_query or oversized_summary"
```

### Implementation

Update `apps/api/app/ai_company_api/schemas/api.py`.

- Add max lengths:
  - `CloudRunExecutionResultCreate.diff_text`: `max_length=2 * 1024 * 1024`
  - `CloudRunExecutionResultCreate.summary`: `max_length=16 * 1024`
  - any execution `external_error` field added in this phase: `max_length=16 * 1024`
  - command result `stdout` and `stderr`: `max_length=512 * 1024`
- Keep status literals unchanged.

Update `apps/api/app/ai_company_api/services/cloud_runner.py`.

- Add `_redact_external_uri(value: str | None) -> str | None`:
  - Remove query string and fragment.
  - Preserve the scheme, authority, and path.
- Add `_redact_external_error(value: str | None) -> str | None`:
  - Reuse the existing sensitive payload redaction helpers where available.
  - Redact token-like key/value pairs and bearer tokens.
- Apply these redactions in `_cloud_run_read()` for:
  - `artifact_manifest_uri`
  - `log_stream_uri`
  - `external_error`
- Ensure `_append_cloud_run_log()` receives already-redacted external messages when logging provider events.

### Verify

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "redacts_external_uri_query or oversized_summary or artifact_ref or external_stub"
git diff --check
```

### Commit

```powershell
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: harden remote provider payload handling"
```

## Task 9: Documentation and Full Verification

### Documentation

Update `docs/superpowers/architecture.md`.

- Mark Phase 10B as implemented.
- Add a concise section for:
  - `local_db` queue provider
  - `external_stub` queue provider
  - `local_inline` object storage provider
  - `remote_stub` runtime provider
  - redaction/size-limit behavior
- State explicitly that Phase 10B does not include real cloud SDKs or provider credentials.

Update `docs/superpowers/specs/2026-06-03-phase-10b-provider-neutral-remote-contract-design.md` only if implementation intentionally narrows a detail from the approved design. Otherwise leave the spec as the source of original design intent.

### Verify

Run the full Phase 10B verification suite:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py
pytest apps/api/tests/test_cloud_run_api.py
pytest apps/api/tests
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
pnpm typecheck
git diff --check
```

Capture the exact pass counts and warnings in the final response and in the final review notes.

### Commit

```powershell
git add docs/superpowers/architecture.md
git commit -m "docs: update architecture for phase 10b"
```

## Final Review Checklist

- `CloudRunRead` includes non-sensitive provider metadata.
- `CloudRunRead` does not include `queue_receipt`.
- `CloudRunLeaseRead` includes worker-safe queue metadata only.
- Unknown queue, storage, and runtime providers return 400 with no secrets.
- `local_db` worker lease flow still passes existing Phase 10A tests.
- `external_stub` records message IDs and external status without real cloud calls.
- `local_inline` artifact refs validate URI, kind, SHA-256, and byte size.
- Remote completion can resolve diff text from an artifact ref.
- Invalid artifact refs do not create patch artifacts.
- Remote stub submission records deterministic job, manifest, and log URIs.
- URI query strings and fragments are redacted from read responses.
- Oversized remote completion payloads are rejected at schema validation.
- Desktop client mappings include the new read-only provider metadata fields.
- No real vendor SDKs, credentials, or network calls were added.
- Full backend, focused desktop, typecheck, and `git diff --check` verification pass.

## Execution Options

1. **TDD by task in this worktree.** Execute Task 1 through Task 9 sequentially, committing after each task. This is the recommended path because the feature touches API schemas, database upgrade code, backend services, and desktop mappings.
2. **Subagent-assisted implementation.** Dispatch independent work for storage, desktop mapping, and documentation after Task 1 establishes the schema contract. Keep queue/runtime lifecycle work in the main session because it shares mutable cloud runner state.
3. **Plan-only handoff.** Stop after committing this plan so another session can execute it from `.worktrees\phase-10b-provider-neutral-remote-contract` on `codex/phase-10b-provider-neutral-remote-contract`.
