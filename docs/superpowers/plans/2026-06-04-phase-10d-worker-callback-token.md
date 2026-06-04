# Phase 10D Worker Callback Token Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add run-scoped callback token authentication to protected remote worker endpoints.

**Architecture:** Store only a callback token hash on `CloudRun`, inject the raw token into remote runtime environment, and require the token on protected worker claim, heartbeat, artifact upload, and complete calls. Keep local/stub development runs compatible by requiring the token only when a run has a stored callback token hash.

**Tech Stack:** FastAPI, SQLModel, pytest, Python `secrets`, SHA-256, existing remote runtime and worker modules.

---

## File Structure

- Create `apps/api/app/ai_company_api/services/worker_callback_auth.py`
  - Token generation, hashing, and constant-time validation helpers.
- Modify `apps/api/app/ai_company_api/models/entities.py`
  - Add nullable callback token columns to `CloudRun`.
- Modify `apps/api/app/ai_company_api/db/session.py`
  - Add SQLite upgrade helper for Phase 10D columns and indexes.
- Modify `apps/api/app/ai_company_api/schemas/api.py`
  - Add optional `callback_token` fields to worker request schemas.
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Generate token for remote runtime submissions.
  - Validate tokens on protected claim, heartbeat, upload, and complete.
  - Invalidate token on completion and cancellation.
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`
  - Carry callback token metadata and inject `AI_SCDC_CALLBACK_TOKEN`.
- Modify `apps/api/app/ai_company_api/services/remote_worker.py`
  - Read `AI_SCDC_CALLBACK_TOKEN` and send it on all callback requests.
- Modify `apps/api/tests/test_cloud_run_api.py`
  - Add missing, wrong, expired, reused, and cross-run token tests.
- Modify `apps/api/tests/test_remote_worker.py`
  - Add remote worker config/client payload tests.

---

### Task 1: Baseline After Phase 10C-H

**Files:**
- No code changes.

- [ ] **Step 1: Run Phase 10C-H verification**

Run after Phase 10C-H is committed:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease" -q
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_remote_worker.py -q
```

Expected: PASS.

---

### Task 2: Token Helper

**Files:**
- Create `apps/api/app/ai_company_api/services/worker_callback_auth.py`
- Modify `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write helper tests**

Add this test to `apps/api/tests/test_cloud_run_api.py`:

```python
def test_callback_token_helper_binds_token_to_run_and_worker() -> None:
    from ai_company_api.services.worker_callback_auth import (
        generate_callback_token,
        hash_callback_token,
        verify_callback_token,
    )

    token = generate_callback_token()
    assert len(token) >= 32
    stored_hash = hash_callback_token("cloud_run_1", "worker_1", token)

    assert verify_callback_token("cloud_run_1", "worker_1", token, stored_hash)
    assert not verify_callback_token("cloud_run_1", "worker_2", token, stored_hash)
    assert not verify_callback_token("cloud_run_2", "worker_1", token, stored_hash)
    assert not verify_callback_token("cloud_run_1", "worker_1", "wrong", stored_hash)
```

- [ ] **Step 2: Implement helper**

Create:

```python
from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_callback_token() -> str:
    return secrets.token_urlsafe(32)


def hash_callback_token(cloud_run_id: str, worker_id: str, token: str) -> str:
    payload = f"{cloud_run_id}:{worker_id}:{token}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_callback_token(
    cloud_run_id: str,
    worker_id: str,
    token: str,
    expected_hash: str,
) -> bool:
    actual_hash = hash_callback_token(cloud_run_id, worker_id, token)
    return hmac.compare_digest(actual_hash, expected_hash)
```

- [ ] **Step 3: Run helper tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "callback_token_helper" -v
```

Expected: PASS.

---

### Task 3: Model and SQLite Upgrade

**Files:**
- Modify `apps/api/app/ai_company_api/models/entities.py`
- Modify `apps/api/app/ai_company_api/db/session.py`
- Modify `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add failing SQLite upgrade test**

Add:

```python
def test_init_db_adds_phase_10d_callback_token_columns(tmp_path: Path) -> None:
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
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql("DROP TABLE cloud_run_old")

    init_db(engine)
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cloud_run")}
    assert {
        "callback_token_hash",
        "callback_token_expires_at",
        "callback_token_used_at",
    }.issubset(columns)
```

- [ ] **Step 2: Add columns to `CloudRun`**

Add nullable fields:

```python
    callback_token_hash: str | None = Field(default=None, index=True)
    callback_token_expires_at: datetime | None = Field(default=None, index=True)
    callback_token_used_at: datetime | None = None
```

- [ ] **Step 3: Add SQLite upgrade helper**

In `init_db()`, call `_upgrade_sqlite_cloud_run_phase_10d_columns(engine)` after
the 10B helper. Implement:

```python
def _upgrade_sqlite_cloud_run_phase_10d_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "callback_token_hash": "VARCHAR",
        "callback_token_expires_at": "DATETIME",
        "callback_token_used_at": "DATETIME",
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
        for column_name in ("callback_token_hash", "callback_token_expires_at"):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_{column_name} "
                    f"ON cloud_run ({column_name})"
                )
            )
```

- [ ] **Step 4: Run upgrade test**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_init_db_adds_phase_10d_callback_token_columns -v
```

Expected: PASS.

---

### Task 4: Protected Claim and Runtime Injection

**Files:**
- Modify `apps/api/app/ai_company_api/schemas/api.py`
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`
- Modify `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add failing protected claim tests**

Write a test named
`test_protected_aliyun_worker_claim_requires_callback_token` to
`apps/api/tests/test_cloud_run_api.py`. It starts an Aliyun ECI run with fake
clients, captures the raw token from the fake ECI environment, then asserts:

```python
claim_without_token = client.post(
    "/cloud-run-worker/leases",
    json={
        "worker_id": f"aliyun-eci-{cloud_run_id}",
        "worker_kind": "aliyun_eci",
        "queue_provider": "aliyun_mns",
        "cloud_run_id": cloud_run_id,
        "lease_seconds": 60,
    },
)
assert claim_without_token.status_code == 401

claim_wrong_token = client.post(
    "/cloud-run-worker/leases",
    json={
        "worker_id": f"aliyun-eci-{cloud_run_id}",
        "worker_kind": "aliyun_eci",
        "queue_provider": "aliyun_mns",
        "cloud_run_id": cloud_run_id,
        "callback_token": "wrong",
        "lease_seconds": 60,
    },
)
assert claim_wrong_token.status_code == 403
```

Also assert the fake ECI request environment contains
`AI_SCDC_CALLBACK_TOKEN` and does not contain the token hash.

- [ ] **Step 2: Add schema fields**

Add `callback_token: str | None = Field(default=None, min_length=1)` to
`CloudRunLeaseCreate`, `CloudRunLeaseHeartbeat`,
`CloudRunArtifactUploadCreate`, and `CloudRunLeaseComplete`.

- [ ] **Step 3: Generate token before runtime submission**

In `start_cloud_run()`, when `runtime_provider is not None`, derive:

```python
worker_id = f"{data.runtime_provider}-{cloud_run.id}"
callback_token = generate_callback_token()
cloud_run.callback_token_hash = hash_callback_token(
    cloud_run.id,
    worker_id,
    callback_token,
)
cloud_run.callback_token_expires_at = utc_now() + timedelta(hours=1)
```

Pass `worker_id`, `callback_token`, and expiry through `RemoteRuntimeSubmission`.
Use the existing Aliyun worker ID format in `remote_runtime.py`:
`aliyun-eci-{cloud_run_id}`. Do not introduce a second worker ID format.

- [ ] **Step 4: Validate token on claim**

In `claim_next_cloud_run_lease()`, when `cloud_run.callback_token_hash` is set,
validate `callback_token` against that run and worker. Missing token returns 401.
Wrong, expired, used, or cross-run token returns 403.

- [ ] **Step 5: Run claim tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "callback_token and claim" -v
```

Expected: PASS.

---

### Task 5: Protected Heartbeat, Upload, and Complete

**Files:**
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify `apps/api/app/ai_company_api/api/routes.py`
- Modify `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add failing endpoint tests**

For a protected Aliyun run claimed with the correct token, assert missing and
wrong token are rejected on:

```text
POST /cloud-run-worker/leases/{lease_id}/heartbeat
POST /cloud-run-worker/leases/{lease_id}/artifacts
POST /cloud-run-worker/leases/{lease_id}/complete
```

Then assert the correct token succeeds.

- [ ] **Step 2: Thread token through service functions**

Update service signatures:

```python
def heartbeat_cloud_run_lease(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
    callback_token: str | None = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> CloudRunLeaseRead:
    cloud_run = _get_current_cloud_run_lease_or_409(
        session,
        lease_id=lease_id,
        worker_id=worker_id,
    )
    _verify_cloud_run_callback_token_or_403(
        cloud_run,
        worker_id=worker_id,
        callback_token=callback_token,
    )
    # Existing heartbeat update logic continues here.


def upload_cloud_run_lease_artifact(
    session: Session,
    *,
    lease_id: str,
    data: CloudRunArtifactUploadCreate,
) -> CloudRunArtifactRefCreate:
    cloud_run = _get_current_cloud_run_lease_or_409(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
    )
    _verify_cloud_run_callback_token_or_403(
        cloud_run,
        worker_id=data.worker_id,
        callback_token=data.callback_token,
    )
    # Existing artifact upload logic continues here.


def complete_cloud_run_lease(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
    callback_token: str | None = None,
    result: CloudRunExecutionResultCreate,
) -> CloudRunResultRead:
    cloud_run = _get_current_cloud_run_lease_or_409(
        session,
        lease_id=lease_id,
        worker_id=worker_id,
    )
    _verify_cloud_run_callback_token_or_403(
        cloud_run,
        worker_id=worker_id,
        callback_token=callback_token,
    )
    # Existing completion finalization logic continues here.
```

Call a shared validator after `_get_current_cloud_run_lease_or_409()`.

- [ ] **Step 3: Invalidate token on completion**

Before finalizing the run:

```python
if cloud_run.callback_token_hash is not None:
    cloud_run.callback_token_used_at = utc_now()
```

Add a reused-token completion test that calls complete twice and gets 403 or 409
on the second call.

- [ ] **Step 4: Invalidate token on cancellation**

In `cancel_cloud_run()`, when the run has a callback token hash and no used time,
set `callback_token_used_at = utc_now()`.

- [ ] **Step 5: Run endpoint tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "callback_token" -v
```

Expected: PASS.

---

### Task 6: Remote Worker Token Propagation

**Files:**
- Modify `apps/api/app/ai_company_api/services/remote_worker.py`
- Modify `apps/api/tests/test_remote_worker.py`

- [ ] **Step 1: Add remote worker tests**

Assert `config_from_env()` reads `AI_SCDC_CALLBACK_TOKEN` and that fake client
calls receive the token:

```python
monkeypatch.setenv("AI_SCDC_CALLBACK_TOKEN", "token-1")
config = config_from_env()
assert config.callback_token == "token-1"
```

For `HttpRemoteWorkerClient`, monkeypatch `_post_json` or use a fake client to
assert claim, heartbeat, upload, and complete payloads include
`callback_token`.

- [ ] **Step 2: Add config field**

```python
@dataclass(frozen=True)
class RemoteWorkerConfig:
    api_base_url: str
    cloud_run_id: str
    worker_id: str
    queue_provider: str
    storage_provider: str
    callback_token: str
```

- [ ] **Step 3: Include token in HTTP payloads**

Add `callback_token` to claim, heartbeat, artifact upload, and complete payloads.

- [ ] **Step 4: Run remote worker tests**

Run:

```powershell
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

---

### Task 7: Final Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run focused backend verification**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun or worker_uploads or artifact_ref or lease or callback_token" -v
pytest apps/api/tests/test_aliyun_config.py apps/api/tests/test_aliyun_clients.py apps/api/tests/test_cloud_object_storage.py apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

- [ ] **Step 2: Run broader regression**

Run:

```powershell
pytest apps/api/tests
pnpm typecheck
git diff --check
```

Expected: PASS.

- [ ] **Step 3: Commit**

Run:

```powershell
git add apps/api/app/ai_company_api/services/worker_callback_auth.py apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/remote_runtime.py apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_remote_worker.py docs/superpowers/specs/2026-06-04-phase-10d-worker-callback-token-design.md docs/superpowers/plans/2026-06-04-phase-10d-worker-callback-token.md
git commit -m "feat: require worker callback tokens"
```

Expected: commit succeeds.
