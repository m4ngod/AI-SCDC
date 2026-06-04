# Phase 11 Real Remote Worker Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a protected real remote worker execution skeleton that can fetch a run payload, clone a private GitHub repo, run selected sandbox commands, upload artifacts, and complete a lease without leaking secrets.

**Architecture:** Add a token-protected worker payload endpoint that returns the current run's execution payload, including a one-run clone token. Keep API payload assembly in a focused service module, and split remote worker execution into fakeable components for payload fetch, checkout, command execution, artifact building, and completion.

**Tech Stack:** FastAPI, SQLModel, Pydantic, pytest, Python stdlib `urllib`, `subprocess`, `tempfile`, existing `SandboxExecutionRequest`, `CommandResult`, object-storage artifact refs, and Phase 10D callback token validation.

---

## File Structure

- Modify `apps/api/app/ai_company_api/schemas/api.py`
  - Add request/response schemas for the worker payload endpoint and reusable worker command payloads.
- Create `apps/api/app/ai_company_api/services/remote_worker_payload.py`
  - Assemble `RemoteWorkerPayloadRead` from `CloudRun`, `Task`, `Repository`, `SandboxProfile`, sandbox env, and active GitHub credential.
- Modify `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Expose a narrow helper for Phase 10D callback-token validation so `remote_worker_payload.py` does not duplicate token logic.
- Modify `apps/api/app/ai_company_api/api/routes.py`
  - Add `POST /cloud-run-worker/leases/{lease_id}/payload`.
- Modify `apps/api/app/ai_company_api/services/remote_worker.py`
  - Add payload fetching and real worker orchestration while preserving fakeable test boundaries.
- Modify `apps/api/tests/test_cloud_run_api.py`
  - Add protected payload endpoint tests.
- Modify `apps/api/tests/test_remote_worker.py`
  - Add worker client, executor, redaction, artifact, and cancellation tests.
- Modify `README.md`, `docs/architecture.md`, and `docs/superpowers/status.md`
  - Document Phase 11 behavior after implementation passes.

## Implementation Notes

- Use `POST /cloud-run-worker/leases/{lease_id}/payload`, not `GET`, because the callback token belongs in JSON body rather than URL/query logs.
- V1 requires a GitHub repository with an active GitHub credential. It can support public repository no-token clone in a later phase.
- The remote worker container itself is the sandbox boundary. Do not start Docker from inside the ECI worker.
- Do not push, merge, create PRs, call model routes, or consume MNS messages directly.
- Redact these values from command results, log artifacts, manifest content, and completion payloads: `clone_token`, `callback_token`, sandbox env values, and repo URL userinfo variants.

### Task 1: API Schemas For Worker Payload

**Files:**
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add the failing payload endpoint schema import expectations in tests**

Add this test near the Phase 10D callback token tests in `apps/api/tests/test_cloud_run_api.py`:

```python
def test_remote_worker_payload_schema_has_private_clone_fields() -> None:
    from ai_company_api.schemas.api import (
        RemoteWorkerCommandPayload,
        RemoteWorkerPayloadRead,
        RemoteWorkerPayloadRequest,
    )

    request = RemoteWorkerPayloadRequest(
        worker_id="worker_1",
        callback_token="callback-token-1",
    )
    command = RemoteWorkerCommandPayload(
        key="patch",
        label="Patch",
        command="python patch.py",
        timeout_seconds=120,
    )
    payload = RemoteWorkerPayloadRead(
        cloud_run_id="cloud_run_1",
        task_id="task_1",
        title="Run fake cloud sandbox",
        description="Create a patch",
        repo_url="https://github.com/example/demo",
        github_owner="example",
        github_repo="demo",
        base_branch="main",
        head_branch="ai-scdc/cloud-run",
        allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
        required_tests=["pytest -q"],
        patch_command=command,
        test_commands=[command],
        env={"SAFE_ENV": "secret-value"},
        network_enabled=True,
        clone_token="ghp_private_clone_token1234",
    )

    assert request.worker_id == "worker_1"
    assert payload.patch_command.command == "python patch.py"
    assert payload.clone_token == "ghp_private_clone_token1234"
```

- [ ] **Step 2: Run the schema test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_remote_worker_payload_schema_has_private_clone_fields -v
```

Expected: FAIL with `ImportError` for `RemoteWorkerPayloadRequest`.

- [ ] **Step 3: Add payload schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add these classes after `CloudRunLeaseHeartbeat`:

```python
class RemoteWorkerPayloadRequest(BaseModel):
    worker_id: str = Field(min_length=1)
    callback_token: str | None = Field(default=None, min_length=1)


class RemoteWorkerCommandPayload(BaseModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=24 * 60 * 60)


class RemoteWorkerPayloadRead(BaseModel):
    cloud_run_id: str
    task_id: str
    title: str
    description: str
    repo_url: str
    github_owner: str | None
    github_repo: str | None
    base_branch: str
    head_branch: str
    allowed_paths: list[str]
    required_tests: list[str]
    patch_command: RemoteWorkerCommandPayload
    test_commands: list[RemoteWorkerCommandPayload]
    env: dict[str, str]
    network_enabled: bool
    clone_token: str = Field(min_length=1)
```

- [ ] **Step 4: Run the schema test to verify it passes**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_remote_worker_payload_schema_has_private_clone_fields -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add remote worker payload schemas"
```

### Task 2: Protected Worker Payload API

**Files:**
- Create: `apps/api/app/ai_company_api/services/remote_worker_payload.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Test: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write the failing protected payload endpoint test**

Add this test near `test_protected_worker_endpoints_require_callback_token_after_claim`:

```python
def test_protected_worker_payload_requires_callback_token_and_returns_execution_payload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    monkeypatch.setenv("SAFE_REMOTE_ENV", "super-secret-env-value")
    _set_complete_aliyun_env(monkeypatch)
    fake_eci = FakeAliyunEciClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(
            mns=FakeAliyunMnsClient(),
            oss=FakeAliyunOssClient(),
            eci=fake_eci,
        ),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(
            session,
            project,
            repository,
            patch_commands=[
                {
                    "key": "patch",
                    "label": "Patch",
                    "command": "python patch.py",
                    "timeout_seconds": 120,
                    "is_default": True,
                }
            ],
            test_commands=[
                {
                    "key": "test",
                    "label": "Test",
                    "command": "pytest -q",
                    "timeout_seconds": 300,
                    "is_default": True,
                }
            ],
            allowed_env_vars=["SAFE_REMOTE_ENV"],
        )
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    cloud_run = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    ).json()["cloud_run"]
    worker_id = f"aliyun-eci-{cloud_run['id']}"
    callback_token = fake_eci.requests[0].environment["AI_SCDC_CALLBACK_TOKEN"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": worker_id,
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run["id"],
            "callback_token": callback_token,
            "lease_seconds": 60,
        },
    ).json()

    missing_token = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/payload",
        json={"worker_id": worker_id},
    )
    assert missing_token.status_code == 401

    payload_response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/payload",
        json={"worker_id": worker_id, "callback_token": callback_token},
    )

    assert payload_response.status_code == 200
    payload = payload_response.json()
    assert payload["cloud_run_id"] == cloud_run["id"]
    assert payload["task_id"] == task_id
    assert payload["repo_url"] == "https://github.com/example/demo"
    assert payload["github_owner"] == "example"
    assert payload["github_repo"] == "demo"
    assert payload["base_branch"] == "main"
    assert payload["head_branch"] == cloud_run["head_branch"]
    assert payload["allowed_paths"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert payload["required_tests"] == ["python -V"]
    assert payload["patch_command"]["key"] == "patch"
    assert payload["patch_command"]["command"] == "python patch.py"
    assert [command["key"] for command in payload["test_commands"]] == ["test"]
    assert payload["env"] == {"SAFE_REMOTE_ENV": "super-secret-env-value"}
    assert payload["network_enabled"] is True
    assert payload["clone_token"] == "ghp_cloud_runner_secret1234"
    assert callback_token not in str(payload)
```

- [ ] **Step 2: Run the payload endpoint test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_protected_worker_payload_requires_callback_token_and_returns_execution_payload -v
```

Expected: FAIL with HTTP 404 because `/payload` route does not exist.

- [ ] **Step 3: Expose callback-token validation for reuse**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, rename `_verify_cloud_run_callback_token_or_403` to `verify_cloud_run_callback_token_or_403`, and update all local callers:

```python
def verify_cloud_run_callback_token_or_403(
    cloud_run: CloudRun,
    *,
    worker_id: str,
    callback_token: str | None,
    now: datetime | None = None,
) -> None:
    if cloud_run.callback_token_hash is None:
        return
    if callback_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Worker callback token is required",
        )
    if cloud_run.callback_token_used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker callback token is not valid",
        )
    expires_at = cloud_run.callback_token_expires_at
    if expires_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker callback token is not valid",
        )
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < (now or utc_now()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker callback token is not valid",
        )
    if not verify_callback_token(
        cloud_run.id,
        worker_id,
        callback_token,
        cloud_run.callback_token_hash,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Worker callback token is not valid",
        )
```

Then replace these calls in the same file:

```python
verify_cloud_run_callback_token_or_403(
    cloud_run,
    worker_id=worker_id,
    callback_token=callback_token,
    now=now,
)
```

- [ ] **Step 4: Create remote worker payload service**

Create `apps/api/app/ai_company_api/services/remote_worker_payload.py`:

```python
from __future__ import annotations

import os

from fastapi import HTTPException
from sqlmodel import Session

from ai_company_api.models.entities import CloudRun, SandboxProfile, Task
from ai_company_api.schemas.api import (
    RemoteWorkerCommandPayload,
    RemoteWorkerPayloadRead,
    RemoteWorkerPayloadRequest,
)
from ai_company_api.services.cloud_runner import (
    verify_cloud_run_callback_token_or_403,
)
from ai_company_api.services.github_repository import (
    get_active_github_credential,
    validate_github_repository_url,
)
from ai_company_api.services.repository import get_repository
from ai_company_api.services.sandbox_profiles import validate_sandbox_profile_for_repo
from ai_company_api.services.secret_vault import DevSecretVault


def get_remote_worker_payload(
    session: Session,
    *,
    lease_id: str,
    data: RemoteWorkerPayloadRequest,
) -> RemoteWorkerPayloadRead:
    cloud_run = _get_current_worker_cloud_run_or_409(
        session,
        lease_id=lease_id,
        worker_id=data.worker_id,
    )
    verify_cloud_run_callback_token_or_403(
        cloud_run,
        worker_id=data.worker_id,
        callback_token=data.callback_token,
    )
    task = session.get(Task, cloud_run.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    repository = get_repository(session, cloud_run.repo_id)
    if repository.github_credential_id is None:
        raise HTTPException(status_code=404, detail="GitHub credential not found")
    validate_github_repository_url(
        repository.repo_url,
        owner=repository.github_owner or "",
        repo=repository.github_repo or "",
    )
    if cloud_run.sandbox_profile_id is None:
        raise HTTPException(
            status_code=400,
            detail="Remote worker payload requires a sandbox profile",
        )
    profile = validate_sandbox_profile_for_repo(
        session,
        cloud_run.sandbox_profile_id,
        project_id=task.project_id,
        repo_id=repository.id,
    )
    patch_command, test_commands = _select_profile_commands_for_cloud_run(
        profile,
        patch_command_key=cloud_run.patch_command_key,
        test_command_keys=cloud_run.test_command_keys or [],
    )
    credential = get_active_github_credential(session, repository.github_credential_id)
    clone_token = DevSecretVault().open(credential.encrypted_token)
    return RemoteWorkerPayloadRead(
        cloud_run_id=cloud_run.id,
        task_id=task.id,
        title=task.title,
        description=task.description,
        repo_url=repository.repo_url,
        github_owner=repository.github_owner,
        github_repo=repository.github_repo,
        base_branch=cloud_run.base_branch or repository.default_branch,
        head_branch=cloud_run.head_branch,
        allowed_paths=task.allowed_paths or [],
        required_tests=task.required_tests or [],
        patch_command=patch_command,
        test_commands=test_commands,
        env=_sandbox_profile_env(profile.allowed_env_vars or []),
        network_enabled=profile.network_enabled,
        clone_token=clone_token,
    )


def _get_current_worker_cloud_run_or_409(
    session: Session,
    *,
    lease_id: str,
    worker_id: str,
) -> CloudRun:
    cloud_run = session.query(CloudRun).filter(CloudRun.lease_id == lease_id).first()
    if cloud_run is None or cloud_run.worker_id != worker_id:
        raise HTTPException(status_code=409, detail="Cloud run lease is not current")
    if cloud_run.status != "running":
        raise HTTPException(status_code=409, detail="Cloud run lease is not current")
    return cloud_run


def _select_profile_commands_for_cloud_run(
    profile: SandboxProfile,
    *,
    patch_command_key: str | None,
    test_command_keys: list[str],
) -> tuple[RemoteWorkerCommandPayload, list[RemoteWorkerCommandPayload]]:
    patch_command = _select_command(
        profile.patch_commands or [],
        patch_command_key,
        kind="patch",
    )
    if test_command_keys:
        test_commands = [
            _select_command(profile.test_commands or [], key, kind="test")
            for key in test_command_keys
        ]
    else:
        test_commands = [
            _command_payload(command)
            for command in (profile.test_commands or [])
            if command.get("is_default") is True
        ]
        if profile.test_commands and len(test_commands) != 1:
            raise HTTPException(
                status_code=400,
                detail="Sandbox profile requires exactly one default test command",
            )
    return patch_command, test_commands


def _select_command(
    commands: list[dict],
    requested_key: str | None,
    *,
    kind: str,
) -> RemoteWorkerCommandPayload:
    if requested_key is not None:
        for command in commands:
            if command.get("key") == requested_key:
                return _command_payload(command)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sandbox {kind} command key",
        )
    defaults = [command for command in commands if command.get("is_default") is True]
    if len(defaults) != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Sandbox profile requires exactly one default {kind} command",
        )
    return _command_payload(defaults[0])


def _command_payload(command: dict) -> RemoteWorkerCommandPayload:
    return RemoteWorkerCommandPayload(
        key=command["key"],
        label=command["label"],
        command=command["command"],
        timeout_seconds=command.get("timeout_seconds", 300),
    )


def _sandbox_profile_env(allowed_env_vars: list[str]) -> dict[str, str]:
    return {name: os.environ[name] for name in allowed_env_vars if name in os.environ}
```

- [ ] **Step 5: Add the route**

In `apps/api/app/ai_company_api/api/routes.py`, add imports:

```python
from ai_company_api.schemas.api import RemoteWorkerPayloadRead, RemoteWorkerPayloadRequest
from ai_company_api.services.remote_worker_payload import get_remote_worker_payload
```

Then add this route after the heartbeat route:

```python
@router.post(
    "/cloud-run-worker/leases/{lease_id}/payload",
    response_model=RemoteWorkerPayloadRead,
)
def post_cloud_run_worker_payload(
    lease_id: str,
    data: RemoteWorkerPayloadRequest,
    session: SessionDep,
) -> RemoteWorkerPayloadRead:
    return get_remote_worker_payload(session, lease_id=lease_id, data=data)
```

- [ ] **Step 6: Run the payload endpoint test to verify it passes**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py::test_protected_worker_payload_requires_callback_token_and_returns_execution_payload -v
```

Expected: PASS.

- [ ] **Step 7: Add rejected credential case**

Add this test:

```python
def test_remote_worker_payload_rejects_repository_without_github_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    _set_complete_aliyun_env(monkeypatch)
    fake_eci = FakeAliyunEciClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(
            mns=FakeAliyunMnsClient(),
            oss=FakeAliyunOssClient(),
            eci=fake_eci,
        ),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        repository.github_credential_id = None
        session.add(repository)
        profile = create_profile_entity(session, project, repository)
        session.commit()
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    cloud_run = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    ).json()["cloud_run"]
    worker_id = f"aliyun-eci-{cloud_run['id']}"
    callback_token = fake_eci.requests[0].environment["AI_SCDC_CALLBACK_TOKEN"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": worker_id,
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run["id"],
            "callback_token": callback_token,
            "lease_seconds": 60,
        },
    ).json()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/payload",
        json={"worker_id": worker_id, "callback_token": callback_token},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "GitHub credential not found"
```

- [ ] **Step 8: Run protected payload tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "payload or callback_token" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/remote_worker_payload.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add protected remote worker payload"
```

### Task 3: Worker Client Fetches Payload

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_worker.py`
- Test: `apps/api/tests/test_remote_worker.py`

- [ ] **Step 1: Write failing client payload test**

Add this test after `test_http_remote_worker_client_sends_callback_token`:

```python
def test_http_remote_worker_client_fetches_payload_with_callback_token() -> None:
    class RecordingHttpRemoteWorkerClient(HttpRemoteWorkerClient):
        def __init__(self) -> None:
            super().__init__("https://api.example.test")
            self.requests: list[tuple[str, dict]] = []

        def _post_json(self, path: str, payload: dict) -> dict:
            self.requests.append((path, payload))
            return {
                "cloud_run_id": "cloud_run_1",
                "task_id": "task_1",
                "title": "Task",
                "description": "Description",
                "repo_url": "https://github.com/example/demo",
                "github_owner": "example",
                "github_repo": "demo",
                "base_branch": "main",
                "head_branch": "ai-scdc/cloud-run",
                "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
                "required_tests": ["pytest -q"],
                "patch_command": {
                    "key": "patch",
                    "label": "Patch",
                    "command": "python patch.py",
                    "timeout_seconds": 120,
                },
                "test_commands": [],
                "env": {},
                "network_enabled": True,
                "clone_token": "ghp_private_clone_token1234",
            }

    client = RecordingHttpRemoteWorkerClient()
    payload = client.payload("lease_1", "worker_1", "callback-token-1")

    assert payload["clone_token"] == "ghp_private_clone_token1234"
    assert client.requests == [
        (
            "/cloud-run-worker/leases/lease_1/payload",
            {"worker_id": "worker_1", "callback_token": "callback-token-1"},
        )
    ]
```

- [ ] **Step 2: Run the client payload test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py::test_http_remote_worker_client_fetches_payload_with_callback_token -v
```

Expected: FAIL with `AttributeError: 'RecordingHttpRemoteWorkerClient' object has no attribute 'payload'`.

- [ ] **Step 3: Add payload method to protocol and HTTP client**

In `apps/api/app/ai_company_api/services/remote_worker.py`, add to `RemoteWorkerClient`:

```python
    def payload(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        ...
```

Add to `HttpRemoteWorkerClient`:

```python
    def payload(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
    ) -> dict[str, Any]:
        return self._post_json(
            f"/cloud-run-worker/leases/{lease_id}/payload",
            {
                "worker_id": worker_id,
                "callback_token": callback_token,
            },
        )
```

- [ ] **Step 4: Run remote worker tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_remote_worker.py
git commit -m "feat: fetch remote worker payload"
```

### Task 4: Fakeable Worker Execution Components

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_worker.py`
- Test: `apps/api/tests/test_remote_worker.py`

- [ ] **Step 1: Write failing executor orchestration test**

Replace the current `FakeWorkerClient` in `apps/api/tests/test_remote_worker.py` with this expanded version:

```python
class FakeWorkerClient:
    def __init__(self, *, cancel_on_second_heartbeat: bool = False) -> None:
        self.claimed_config: RemoteWorkerConfig | None = None
        self.heartbeats: list[dict] = []
        self.uploaded: list[dict] = []
        self.completed: dict | None = None
        self.cancel_on_second_heartbeat = cancel_on_second_heartbeat

    def claim(self, config: RemoteWorkerConfig) -> dict:
        self.claimed_config = config
        return {"lease_id": "lease_1", "cloud_run": {"id": config.cloud_run_id}}

    def payload(self, lease_id: str, worker_id: str, callback_token: str) -> dict:
        return {
            "cloud_run_id": "cloud_run_1",
            "task_id": "task_1",
            "title": "Run remote worker",
            "description": "Create a real patch",
            "repo_url": "https://github.com/example/demo",
            "github_owner": "example",
            "github_repo": "demo",
            "base_branch": "main",
            "head_branch": "ai-scdc/cloud-run",
            "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
            "required_tests": ["pytest -q"],
            "patch_command": {
                "key": "patch",
                "label": "Patch",
                "command": "python patch.py",
                "timeout_seconds": 120,
            },
            "test_commands": [
                {
                    "key": "test",
                    "label": "Test",
                    "command": "pytest -q",
                    "timeout_seconds": 300,
                }
            ],
            "env": {"SAFE_REMOTE_ENV": "env-secret-value"},
            "network_enabled": True,
            "clone_token": "ghp_private_clone_token1234",
        }

    def heartbeat(self, lease_id: str, worker_id: str, callback_token: str) -> dict:
        self.heartbeats.append(
            {
                "lease_id": lease_id,
                "worker_id": worker_id,
                "callback_token": callback_token,
            }
        )
        return {
            "lease_id": lease_id,
            "cancel_requested": self.cancel_on_second_heartbeat
            and len(self.heartbeats) >= 2,
        }

    def upload_artifact(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        *,
        kind: str,
        content: str,
        content_type: str,
    ) -> dict:
        ref = {
            "kind": kind,
            "uri": f"oss://bucket/{kind}-{len(self.uploaded)}.txt",
            "sha256": "a" * 64,
            "size_bytes": len(content.encode("utf-8")),
            "content_type": content_type,
        }
        self.uploaded.append({"ref": ref, "content": content, "token": callback_token})
        return ref

    def complete(
        self,
        lease_id: str,
        worker_id: str,
        callback_token: str,
        result: dict,
    ) -> dict:
        self.completed = {
            "lease_id": lease_id,
            "worker_id": worker_id,
            "callback_token": callback_token,
            **result,
        }
        return {"cloud_run": {"status": result["result"]["status"]}}
```

Add fake components and the new test:

```python
class FakeCheckout:
    def checkout(self, payload: dict) -> str:
        return "/tmp/repo"


class FakeCommandRunner:
    def run(self, payload: dict, repo_path: str) -> dict:
        return {
            "status": "patch_ready",
            "runner_kind": "aliyun_eci",
            "base_sha": "base123",
            "head_sha": "head456",
            "worktree_ref": "remote-worker://cloud_run_1",
            "summary": "Remote worker produced a patch artifact.",
            "files_changed": ["AI_SCDC_CLOUD_RUN.md"],
            "tests_run": ["pytest -q"],
            "test_result": "passed",
            "risks": [],
            "diff_text": "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+ok\n",
            "command_results": [
                {
                    "command": "python patch.py ghp_private_clone_token1234",
                    "exit_code": 0,
                    "stdout": "patched env-secret-value",
                    "stderr": "",
                    "duration_ms": 12,
                    "timed_out": False,
                }
            ],
            "test_command_results": [
                {
                    "command": "pytest -q",
                    "exit_code": 0,
                    "stdout": "passed",
                    "stderr": "",
                    "duration_ms": 15,
                    "timed_out": False,
                }
            ],
            "failure_reason": None,
        }


def test_remote_worker_fetches_payload_runs_components_uploads_artifacts_and_completes() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )
    executor = RemoteWorkerExecutor(
        client=client,
        checkout=FakeCheckout(),
        command_runner=FakeCommandRunner(),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "patch_ready"
    assert len(client.heartbeats) == 2
    uploaded_kinds = [upload["ref"]["kind"] for upload in client.uploaded]
    assert uploaded_kinds == ["diff", "command_result", "test_result", "log", "manifest"]
    assert client.completed is not None
    completion = client.completed["result"]
    assert completion["diff_text"] == ""
    assert completion["artifact_refs"][0]["kind"] == "diff"
    assert completion["command_results"][0]["command"] == "python patch.py [redacted]"
    assert "ghp_private_clone_token1234" not in str(client.uploaded)
    assert "env-secret-value" not in str(client.uploaded)
    assert "callback-token-1" not in str(client.uploaded)
```

- [ ] **Step 2: Run orchestration test to verify it fails**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py::test_remote_worker_fetches_payload_runs_components_uploads_artifacts_and_completes -v
```

Expected: FAIL with `ImportError` for `RemoteWorkerExecutor`.

- [ ] **Step 3: Add redaction helper and executor skeleton**

In `apps/api/app/ai_company_api/services/remote_worker.py`, add imports:

```python
from dataclasses import dataclass
from typing import Any, Protocol
import json
```

Add these helpers after `HttpRemoteWorkerClient`:

```python
class RemoteWorkerCheckout(Protocol):
    def checkout(self, payload: dict[str, Any]) -> str:
        ...


class RemoteWorkerCommandRunner(Protocol):
    def run(self, payload: dict[str, Any], repo_path: str) -> dict[str, Any]:
        ...


def _redact_text(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in sorted((secret for secret in secrets if secret), key=len, reverse=True):
        redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _redacted_command_result(result: dict[str, Any], secrets: list[str]) -> dict[str, Any]:
    return {
        "command": _redact_text(result.get("command", ""), secrets),
        "exit_code": result.get("exit_code"),
        "stdout": _redact_text(result.get("stdout", ""), secrets),
        "stderr": _redact_text(result.get("stderr", ""), secrets),
        "duration_ms": result.get("duration_ms", 0),
        "timed_out": result.get("timed_out", False),
    }


@dataclass
class RemoteWorkerExecutor:
    client: RemoteWorkerClient
    checkout: RemoteWorkerCheckout
    command_runner: RemoteWorkerCommandRunner

    def run_once(self, config: RemoteWorkerConfig) -> dict[str, Any]:
        lease = self.client.claim(config)
        lease_id = lease["lease_id"]
        payload = self.client.payload(lease_id, config.worker_id, config.callback_token)
        first_heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        if first_heartbeat.get("cancel_requested") is True:
            return self._complete_cancelled(config, lease_id)
        repo_path = self.checkout.checkout(payload)
        execution = self.command_runner.run(payload, repo_path)
        second_heartbeat = self.client.heartbeat(
            lease_id,
            config.worker_id,
            config.callback_token,
        )
        if second_heartbeat.get("cancel_requested") is True:
            execution = {
                **execution,
                "status": "failed",
                "failure_reason": "cancelled",
                "test_result": execution.get("test_result", "not_run"),
            }
        secrets = [
            payload.get("clone_token", ""),
            config.callback_token,
            *[str(value) for value in payload.get("env", {}).values()],
        ]
        artifact_refs = self._upload_artifacts(config, lease_id, execution, secrets)
        completion = self._completion_payload(execution, artifact_refs, secrets)
        return self.client.complete(
            lease_id,
            config.worker_id,
            config.callback_token,
            {"result": completion},
        )

    def _complete_cancelled(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
    ) -> dict[str, Any]:
        return self.client.complete(
            lease_id,
            config.worker_id,
            config.callback_token,
            {
                "result": {
                    "status": "failed",
                    "runner_kind": "aliyun_eci",
                    "base_sha": None,
                    "head_sha": None,
                    "worktree_ref": None,
                    "summary": "Remote worker cancelled before checkout.",
                    "files_changed": [],
                    "tests_run": [],
                    "test_result": "not_run",
                    "risks": [],
                    "diff_text": "",
                    "artifact_refs": [],
                    "command_results": [],
                    "test_command_results": [],
                    "failure_reason": "cancelled",
                }
            },
        )

    def _upload_artifacts(
        self,
        config: RemoteWorkerConfig,
        lease_id: str,
        execution: dict[str, Any],
        secrets: list[str],
    ) -> list[dict[str, Any]]:
        command_results = [
            _redacted_command_result(result, secrets)
            for result in execution.get("command_results", [])
        ]
        test_results = [
            _redacted_command_result(result, secrets)
            for result in execution.get("test_command_results", [])
        ]
        uploads = [
            ("diff", execution.get("diff_text", ""), "text/x-diff"),
            ("command_result", json.dumps(command_results, sort_keys=True), "application/json"),
            ("test_result", json.dumps(test_results, sort_keys=True), "application/json"),
            (
                "log",
                _redact_text(execution.get("summary", ""), secrets),
                "text/plain",
            ),
        ]
        artifact_refs: list[dict[str, Any]] = []
        for kind, content, content_type in uploads:
            artifact_refs.append(
                self.client.upload_artifact(
                    lease_id,
                    config.worker_id,
                    config.callback_token,
                    kind=kind,
                    content=content,
                    content_type=content_type,
                )
            )
        manifest = {
            "cloud_run_id": config.cloud_run_id,
            "artifacts": artifact_refs,
            "status": execution.get("status"),
            "failure_reason": execution.get("failure_reason"),
        }
        artifact_refs.append(
            self.client.upload_artifact(
                lease_id,
                config.worker_id,
                config.callback_token,
                kind="manifest",
                content=json.dumps(manifest, sort_keys=True),
                content_type="application/json",
            )
        )
        return artifact_refs

    def _completion_payload(
        self,
        execution: dict[str, Any],
        artifact_refs: list[dict[str, Any]],
        secrets: list[str],
    ) -> dict[str, Any]:
        return {
            "status": execution.get("status", "failed"),
            "runner_kind": execution.get("runner_kind", "aliyun_eci"),
            "base_sha": execution.get("base_sha"),
            "head_sha": execution.get("head_sha"),
            "worktree_ref": execution.get("worktree_ref"),
            "summary": _redact_text(execution.get("summary", ""), secrets),
            "files_changed": execution.get("files_changed", []),
            "tests_run": execution.get("tests_run", []),
            "test_result": execution.get("test_result", "not_run"),
            "risks": execution.get("risks", []),
            "diff_text": "",
            "artifact_refs": artifact_refs,
            "command_results": [
                _redacted_command_result(result, secrets)
                for result in execution.get("command_results", [])
            ],
            "test_command_results": [
                _redacted_command_result(result, secrets)
                for result in execution.get("test_command_results", [])
            ],
            "failure_reason": execution.get("failure_reason"),
        }
```

- [ ] **Step 4: Update `run_remote_worker_once` to use the executor for injected components**

Change `run_remote_worker_once` signature:

```python
def run_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
    checkout: RemoteWorkerCheckout | None = None,
    command_runner: RemoteWorkerCommandRunner | None = None,
) -> dict[str, Any]:
```

Keep the deterministic smoke path only when no checkout/command runner is supplied:

```python
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
    if checkout is not None and command_runner is not None:
        return RemoteWorkerExecutor(
            client=resolved_client,
            checkout=checkout,
            command_runner=command_runner,
        ).run_once(config)
```

Leave the existing deterministic implementation below that branch, so old tests keep passing until Task 5 flips the production default.

- [ ] **Step 5: Run remote worker tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_remote_worker.py
git commit -m "feat: add remote worker execution components"
```

### Task 5: Real Git Checkout And Command Runner

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_worker.py`
- Test: `apps/api/tests/test_remote_worker.py`

- [ ] **Step 1: Write focused unit tests for checkout command construction and safe cleanup**

Add tests:

```python
def test_remote_worker_git_checkout_uses_askpass_without_token_in_command(tmp_path: Path) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerGitCheckout

    calls: list[dict] = []

    def fake_run(args, *, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        return Result()

    checkout = RemoteWorkerGitCheckout(workspace_root=tmp_path, process_run=fake_run)
    repo_path = checkout.checkout(
        {
            "cloud_run_id": "cloud_run_1",
            "repo_url": "https://github.com/example/demo",
            "base_branch": "main",
            "head_branch": "ai-scdc/cloud-run",
            "clone_token": "ghp_private_clone_token1234",
        }
    )

    assert Path(repo_path).name == "repo"
    clone_call = calls[0]
    assert clone_call["args"] == [
        "git",
        "clone",
        "--",
        "https://github.com/example/demo",
        ".",
    ]
    assert clone_call["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_ASKPASS" in clone_call["env"]
    assert "ghp_private_clone_token1234" not in str(calls)
```

```python
def test_remote_worker_command_runner_maps_patch_and_test_results(tmp_path: Path) -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerCommandRunnerImpl

    calls: list[dict] = []

    def fake_run(args, *, cwd=None, env=None, timeout=None):
        calls.append({"args": args, "cwd": cwd, "env": env, "timeout": timeout})
        command = args[-1]
        class Result:
            returncode = 0
            stdout = "AI_SCDC_CLOUD_RUN.md\n" if "diff --name-only" in command else "ok"
            stderr = ""
        if "git diff --no-ext-diff" in command:
            Result.stdout = "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+ok\n"
        if "rev-parse" in command:
            Result.stdout = "abc123\n"
        return Result()

    runner = RemoteWorkerCommandRunnerImpl(process_run=fake_run)
    result = runner.run(
        {
            "cloud_run_id": "cloud_run_1",
            "patch_command": {
                "key": "patch",
                "command": "python patch.py",
                "timeout_seconds": 120,
            },
            "test_commands": [
                {"key": "test", "command": "pytest -q", "timeout_seconds": 300}
            ],
            "allowed_paths": ["AI_SCDC_CLOUD_RUN.md"],
            "env": {"SAFE_REMOTE_ENV": "value"},
        },
        str(tmp_path),
    )

    assert result["status"] == "patch_ready"
    assert result["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert result["test_result"] == "passed"
    assert result["diff_text"].startswith("diff --git")
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -k "git_checkout or command_runner" -v
```

Expected: FAIL with missing `RemoteWorkerGitCheckout` and `RemoteWorkerCommandRunnerImpl`.

- [ ] **Step 3: Add process result helper, git checkout, and command runner implementations**

In `apps/api/app/ai_company_api/services/remote_worker.py`, add imports:

```python
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
```

Add implementations:

```python
@dataclass(frozen=True)
class RemoteProcessResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


def _run_process(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class RemoteWorkerGitCheckout:
    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        process_run=_run_process,
    ) -> None:
        self._workspace_root = workspace_root or (
            Path(tempfile.gettempdir()) / "ai-scdc-remote-worker"
        )
        self._process_run = process_run

    def checkout(self, payload: dict[str, Any]) -> str:
        root = self._workspace_root / payload["cloud_run_id"]
        if root.exists():
            shutil.rmtree(root)
        repo_path = root / "repo"
        repo_path.mkdir(parents=True, exist_ok=True)
        askpass = root / "git-askpass.py"
        askpass.write_text(
            "import os\nprint(os.environ.get('AI_SCDC_GIT_TOKEN', ''))\n",
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": str(askpass),
            "AI_SCDC_GIT_TOKEN": payload["clone_token"],
        }
        try:
            self._must_run(
                ["git", "clone", "--", payload["repo_url"], "."],
                cwd=repo_path,
                env=env,
                timeout=300,
            )
            self._must_run(
                ["git", "checkout", payload["base_branch"]],
                cwd=repo_path,
                env=env,
                timeout=60,
            )
            self._must_run(
                ["git", "checkout", "-B", payload["head_branch"]],
                cwd=repo_path,
                env=env,
                timeout=60,
            )
        finally:
            askpass.unlink(missing_ok=True)
        return str(repo_path)

    def _must_run(self, args, *, cwd, env, timeout):
        result = self._process_run(args, cwd=cwd, env=env, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError("repo_checkout_failed")
        return result
```

Add command runner:

```python
class RemoteWorkerCommandRunnerImpl:
    def __init__(self, *, process_run=_run_process) -> None:
        self._process_run = process_run

    def run(self, payload: dict[str, Any], repo_path: str) -> dict[str, Any]:
        command_results: list[dict[str, Any]] = []
        test_results: list[dict[str, Any]] = []
        env = {**os.environ, **payload.get("env", {})}
        patch = payload["patch_command"]
        patch_result = self._run_shell(
            patch["command"],
            cwd=repo_path,
            env=env,
            timeout=patch.get("timeout_seconds", 300),
        )
        command_results.append(patch_result.as_dict())
        if patch_result.exit_code != 0 or patch_result.timed_out:
            return self._failed("patch_command_failed", command_results, test_results)
        for command in [
            "git add -N .",
            "git diff --name-only",
            "git diff --no-ext-diff",
            f"git rev-parse origin/{payload['base_branch']}",
            "git rev-parse HEAD",
        ]:
            result = self._run_shell(command, cwd=repo_path, env=env, timeout=60)
            command_results.append(result.as_dict())
            if result.exit_code != 0 or result.timed_out:
                return self._failed("artifact_capture_failed", command_results, test_results)
        files_changed = [
            line.strip()
            for line in command_results[-4]["stdout"].splitlines()
            if line.strip()
        ]
        diff_text = command_results[-3]["stdout"]
        if not files_changed or diff_text.strip() == "":
            return self._failed("no_patch_produced", command_results, test_results)
        disallowed = [
            path
            for path in files_changed
            if payload.get("allowed_paths") and path not in payload["allowed_paths"]
        ]
        if disallowed:
            return {
                **self._failed("artifact_capture_failed", command_results, test_results),
                "files_changed": files_changed,
                "diff_text": diff_text,
            }
        test_status = "passed" if payload.get("test_commands") else "not_run"
        for command in payload.get("test_commands", []):
            result = self._run_shell(
                command["command"],
                cwd=repo_path,
                env=env,
                timeout=command.get("timeout_seconds", 300),
            )
            test_results.append(result.as_dict())
            if result.exit_code != 0 or result.timed_out:
                test_status = "failed"
        failure_reason = "test_failed" if test_status == "failed" else None
        return {
            "status": "failed" if failure_reason else "patch_ready",
            "runner_kind": "aliyun_eci",
            "base_sha": command_results[-2]["stdout"].strip() or None,
            "head_sha": command_results[-1]["stdout"].strip() or None,
            "worktree_ref": f"remote-worker://{payload['cloud_run_id']}",
            "summary": "Remote worker produced a patch artifact.",
            "files_changed": files_changed,
            "tests_run": [command["command"] for command in payload.get("test_commands", [])],
            "test_result": test_status,
            "risks": [],
            "diff_text": diff_text,
            "command_results": command_results,
            "test_command_results": test_results,
            "failure_reason": failure_reason,
        }

    def _run_shell(self, command: str, *, cwd: str, env: dict[str, str], timeout: int):
        started = time.monotonic()
        try:
            result = self._process_run(
                ["sh", "-lc", command],
                cwd=cwd,
                env=env,
                timeout=timeout,
            )
            return RemoteProcessResult(
                command=command,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            return RemoteProcessResult(
                command=command,
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )

    def _failed(
        self,
        failure_reason: str,
        command_results: list[dict[str, Any]],
        test_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "status": "failed",
            "runner_kind": "aliyun_eci",
            "base_sha": None,
            "head_sha": None,
            "worktree_ref": None,
            "summary": "",
            "files_changed": [],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "",
            "command_results": command_results,
            "test_command_results": test_results,
            "failure_reason": failure_reason,
        }
```

- [ ] **Step 4: Run focused remote worker tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -k "git_checkout or command_runner or fetches_payload" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_remote_worker.py
git commit -m "feat: add real remote worker command runner"
```

### Task 6: Production Worker Uses Real Components

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_worker.py`
- Test: `apps/api/tests/test_remote_worker.py`

- [ ] **Step 1: Write failing run function integration test**

Add this test:

```python
def test_run_remote_worker_once_uses_real_executor_when_components_are_supplied() -> None:
    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )

    result = run_remote_worker_once(
        config,
        client=client,
        checkout=FakeCheckout(),
        command_runner=FakeCommandRunner(),
    )

    assert result["cloud_run"]["status"] == "patch_ready"
    assert client.completed is not None
    assert client.completed["result"]["artifact_refs"][0]["kind"] == "diff"
```

- [ ] **Step 2: Run the integration test**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py::test_run_remote_worker_once_uses_real_executor_when_components_are_supplied -v
```

Expected: PASS if Task 4 branch was added correctly.

- [ ] **Step 3: Change production default to real components**

Update `run_remote_worker_once` so production no longer uses deterministic diff when no components are supplied:

```python
def run_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
    checkout: RemoteWorkerCheckout | None = None,
    command_runner: RemoteWorkerCommandRunner | None = None,
) -> dict[str, Any]:
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
    resolved_checkout = checkout or RemoteWorkerGitCheckout()
    resolved_command_runner = command_runner or RemoteWorkerCommandRunnerImpl()
    return RemoteWorkerExecutor(
        client=resolved_client,
        checkout=resolved_checkout,
        command_runner=resolved_command_runner,
    ).run_once(config)
```

Move the old deterministic smoke body into a test-only helper:

```python
def run_deterministic_remote_worker_once(
    config: RemoteWorkerConfig,
    *,
    client: RemoteWorkerClient | None = None,
) -> dict[str, Any]:
    resolved_client = client or HttpRemoteWorkerClient(config.api_base_url)
    lease = resolved_client.claim(config)
    lease_id = lease["lease_id"]
    resolved_client.heartbeat(lease_id, config.worker_id, config.callback_token)
    diff_text = _deterministic_diff(config.cloud_run_id)
    diff_ref = resolved_client.upload_artifact(
        lease_id,
        config.worker_id,
        config.callback_token,
        kind="diff",
        content=diff_text,
        content_type="text/x-diff",
    )
    completion = {
        "result": {
            "status": "patch_ready",
            "runner_kind": "aliyun_eci",
            "base_sha": None,
            "head_sha": None,
            "worktree_ref": f"aliyun-eci://{config.cloud_run_id}",
            "summary": "Aliyun ECI remote worker produced a deterministic smoke patch.",
            "files_changed": ["AI_SCDC_ALIYUN_ECI.md"],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "",
            "artifact_refs": [diff_ref],
            "command_results": [],
            "test_command_results": [],
            "failure_reason": None,
        }
    }
    return resolved_client.complete(
        lease_id,
        config.worker_id,
        config.callback_token,
        completion,
    )
```

Update the original deterministic test to import and call `run_deterministic_remote_worker_once`.

- [ ] **Step 4: Add cancellation test**

Add:

```python
def test_remote_worker_stops_at_command_boundary_when_cancel_requested() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    client = FakeWorkerClient(cancel_on_second_heartbeat=True)
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )
    executor = RemoteWorkerExecutor(
        client=client,
        checkout=FakeCheckout(),
        command_runner=FakeCommandRunner(),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    assert client.completed["result"]["failure_reason"] == "cancelled"
```

- [ ] **Step 5: Run remote worker tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_remote_worker.py
git commit -m "feat: enable real remote worker execution path"
```

### Task 7: End-To-End Payload And Completion Regression

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/tests/test_remote_worker.py`

- [ ] **Step 1: Add API regression for payload token rejection cases**

Add:

```python
def test_remote_worker_payload_rejects_wrong_expired_and_used_callback_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    _set_complete_aliyun_env(monkeypatch)
    fake_eci = FakeAliyunEciClient()
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(
            mns=FakeAliyunMnsClient(),
            oss=FakeAliyunOssClient(),
            eci=fake_eci,
        ),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    cloud_run = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    ).json()["cloud_run"]
    worker_id = f"aliyun-eci-{cloud_run['id']}"
    callback_token = fake_eci.requests[0].environment["AI_SCDC_CALLBACK_TOKEN"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": worker_id,
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run["id"],
            "callback_token": callback_token,
            "lease_seconds": 60,
        },
    ).json()

    wrong = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/payload",
        json={"worker_id": worker_id, "callback_token": "wrong"},
    )
    assert wrong.status_code == 403

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted = session.get(CloudRun, cloud_run["id"])
        assert persisted is not None
        persisted.callback_token_expires_at = datetime(2026, 6, 2, tzinfo=timezone.utc)
        session.add(persisted)
        session.commit()

    expired = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/payload",
        json={"worker_id": worker_id, "callback_token": callback_token},
    )
    assert expired.status_code == 403
```

- [ ] **Step 2: Run payload rejection tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "payload" -v
```

Expected: PASS.

- [ ] **Step 3: Add worker failure mapping tests**

Add parametrized tests in `apps/api/tests/test_remote_worker.py`:

```python
class FailingCommandRunner:
    def __init__(self, failure_reason: str) -> None:
        self.failure_reason = failure_reason

    def run(self, payload: dict, repo_path: str) -> dict:
        return {
            "status": "failed",
            "runner_kind": "aliyun_eci",
            "base_sha": None,
            "head_sha": None,
            "worktree_ref": None,
            "summary": "",
            "files_changed": [],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "",
            "command_results": [],
            "test_command_results": [],
            "failure_reason": self.failure_reason,
        }


def test_remote_worker_completes_failure_reason_from_command_runner() -> None:
    from ai_company_api.services.remote_worker import RemoteWorkerExecutor

    client = FakeWorkerClient()
    config = RemoteWorkerConfig(
        api_base_url="https://api.example.test",
        cloud_run_id="cloud_run_1",
        worker_id="worker_1",
        queue_provider="aliyun_mns",
        storage_provider="aliyun_oss",
        callback_token="callback-token-1",
    )
    executor = RemoteWorkerExecutor(
        client=client,
        checkout=FakeCheckout(),
        command_runner=FailingCommandRunner("no_patch_produced"),
    )

    result = executor.run_once(config)

    assert result["cloud_run"]["status"] == "failed"
    assert client.completed is not None
    assert client.completed["result"]["failure_reason"] == "no_patch_produced"
```

- [ ] **Step 4: Run regression tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py apps/api/tests/test_cloud_run_api.py -k "payload or remote_worker or callback_token" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/test_cloud_run_api.py apps/api/tests/test_remote_worker.py
git commit -m "test: cover phase 11 worker failure paths"
```

### Task 8: Documentation And Status Update

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`

- [ ] **Step 1: Update README Phase summary**

In `README.md`, update the opening phase summary to include Phase 11:

```markdown
Phase 10D run-scoped remote worker callback token hardening, and Phase 11 real remote worker execution skeleton
```

In the Phase 10C Aliyun smoke section, add:

```markdown
Phase 11 remote workers fetch a protected execution payload after claiming a
lease. The payload includes the selected sandbox profile commands and a
run-scoped clone credential for the configured GitHub repository. The clone
credential is sent only to the callback-token-authenticated worker and must not
appear in API responses, logs, artifacts, or completion payloads.
```

- [ ] **Step 2: Update architecture**

In `docs/architecture.md`, add:

```markdown
## Phase 11 Boundary

Phase 11 upgrades the Aliyun ECI remote worker from deterministic smoke output
to a real execution skeleton. The worker claims a protected lease, fetches a
callback-token-protected execution payload, clones the GitHub repository with a
run-scoped clone credential, runs selected sandbox profile commands inside the
worker container, captures diff and command/test output, uploads artifact refs,
and completes the lease.

Phase 11 does not add direct MNS receive/delete semantics, live log streaming,
model-backed debugging, Git push, PR creation, automatic merge, production KMS,
or a second cloud provider.
```

Add a completed roadmap item:

```markdown
15. Real remote worker execution skeleton with protected payload fetch, private GitHub clone credential boundary, command/test execution, diff capture, artifact uploads, and redacted completion.
```

- [ ] **Step 3: Update status**

In `docs/superpowers/status.md`, change current phase:

```markdown
The project is through Phase 11: real remote worker execution skeleton for
protected Aliyun ECI workers.
```

Add verification commands after running final verification in Task 9.

- [ ] **Step 4: Run markdown grep for accidental incomplete markers**

Run:

```powershell
$Pattern = ('TB' + 'D|ghp_private_clone_token1234|super-secret-env-value|callback-token-1')
rg -n $Pattern README.md docs/architecture.md docs/superpowers/status.md
```

Expected: no hits for fake secrets in user-facing docs and no incomplete markers in the touched documentation files.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/architecture.md docs/superpowers/status.md
git commit -m "docs: document phase 11 remote worker execution"
```

### Task 9: Final Verification

**Files:**
- Verify only; modify files only if a verification failure reveals a defect.

- [ ] **Step 1: Run focused worker tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run focused cloud-run tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -k "payload or callback_token or aliyun or artifact_ref or lease" -v
```

Expected: all selected tests pass with only the existing Starlette/httpx warning if present.

- [ ] **Step 3: Run full API test suite**

Run:

```bash
pytest apps/api/tests
```

Expected: all tests pass.

- [ ] **Step 4: Run typecheck**

Run:

```bash
pnpm typecheck
```

Expected: TypeScript typecheck passes for workspace packages.

- [ ] **Step 5: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no whitespace errors. Git may print LF-to-CRLF working-copy warnings on Windows.

- [ ] **Step 6: Run secret scan**

Run:

```bash
rg -n "ghp_|callback-token|AI_SCDC_CALLBACK_TOKEN|clone_token|AccessKey|ACCESS_KEY_SECRET" apps docs README.md
```

Expected: hits are limited to environment variable names, schema/field names, test fake values, and docs explaining redaction. No real credential values should appear.

- [ ] **Step 7: Commit any verification fixes**

If fixes were needed:

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/remote_worker_payload.py apps/api/app/ai_company_api/api/routes.py apps/api/app/ai_company_api/services/remote_worker.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_remote_worker.py README.md docs/architecture.md docs/superpowers/status.md
git commit -m "fix: stabilize phase 11 verification"
```

If no fixes were needed, do not create a commit.

### Task 10: Completion Handoff

**Files:**
- No file changes unless final verification finds a gap in status docs.

- [ ] **Step 1: Confirm git status**

Run:

```bash
git status --short --branch
git log --oneline --decorate -8
```

Expected: working tree is clean and `master` contains the Phase 11 commits.

- [ ] **Step 2: Use verification-before-completion**

Before claiming completion, open and follow:

```text
C:\Users\Administrator\.codex\plugins\cache\openai-curated\superpowers\2abb1c44\skills\verification-before-completion\SKILL.md
```

- [ ] **Step 3: Use finishing-a-development-branch**

If implementation happened on a feature branch, open and follow:

```text
C:\Users\Administrator\.codex\plugins\cache\openai-curated\superpowers\2abb1c44\skills\finishing-a-development-branch\SKILL.md
```

If implementation happened directly on `master`, report that `master` is ahead of `origin/master` and ask whether to push or keep local.

## Self-Review

Spec coverage:

- Protected payload endpoint: Tasks 1-2 and Task 7.
- Private GitHub clone token boundary: Tasks 2, 5, 7, 9.
- Worker real execution skeleton: Tasks 3-6.
- Artifact refs for diff, command result, test result, log, manifest: Task 4.
- Redaction: Tasks 4, 7, 9.
- Cancellation at command boundaries: Task 6.
- Docs/status: Task 8.
- Final verification: Task 9.

Incomplete marker scan: no incomplete task sections should remain in this plan.

Type consistency: The plan uses `RemoteWorkerPayloadRequest`, `RemoteWorkerCommandPayload`, `RemoteWorkerPayloadRead`, `RemoteWorkerExecutor`, `RemoteWorkerGitCheckout`, and `RemoteWorkerCommandRunnerImpl` consistently across tests and implementation steps.
