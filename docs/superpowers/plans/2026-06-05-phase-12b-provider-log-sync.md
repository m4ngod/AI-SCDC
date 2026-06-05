# Phase 12B Provider Log Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional provider-native log sync to the existing cloud-run log window polling API.

**Architecture:** Keep `GET /cloud-runs/{id}/logs/window` as the only read surface and add `sync_stream=false` as a backward-compatible opt-in query parameter. Put sync orchestration in a new `cloud_run_log_sync.py` service, keep `cloud_run_logs.py` focused on cursor pagination and safe stream reads, and extend runtime providers with a bounded `sync_logs()` contract. Aliyun ECI sync uses a tested `DescribeContainerLog` client seam and writes refreshed snapshots through existing object storage providers.

**Tech Stack:** FastAPI, SQLModel, SQLite, Pydantic, pytest, Aliyun ECI SDK, Aliyun OSS SDK, React/Vite TypeScript API client, Vitest.

---

## File Structure

- Modify `apps/api/app/ai_company_api/services/aliyun_clients.py`
  - Add `AliyunEciDescribeContainerLogRequest`.
  - Add `AliyunEciClient.describe_container_log()`.
  - Implement the SDK call in `SdkAliyunEciClient`.
- Modify `apps/api/app/ai_company_api/services/remote_runtime.py`
  - Add `RemoteRuntimeLogSyncRequest` and `RemoteRuntimeLogSyncResult`.
  - Extend `RemoteRuntimeProvider` with `sync_logs()`.
  - Implement deterministic `remote_stub` sync.
  - Implement Aliyun ECI log sync through `DescribeContainerLog` and OSS.
- Create `apps/api/app/ai_company_api/services/cloud_run_log_sync.py`
  - Own provider lookup, current log ref reconstruction, safe sync invocation, and `CloudRun.log_stream_*` metadata updates.
- Modify `apps/api/app/ai_company_api/services/cloud_run_logs.py`
  - Add `sync_stream` to `list_cloud_run_log_window()`.
  - Call `sync_cloud_run_log_stream()` only when `include_stream` and `sync_stream` are both true.
- Modify `apps/api/app/ai_company_api/api/routes.py`
  - Add `sync_stream: bool = False` to the log window endpoint.
- Modify `apps/api/tests/test_aliyun_clients.py`
  - Cover the SDK `DescribeContainerLog` request shape.
- Modify `apps/api/tests/test_cloud_run_api.py`
  - Cover route gating, remote stub sync, Aliyun sync, provider degradation, oversized snapshots, and redaction.
- Modify `apps/desktop/src/api/client.ts`
  - Add `syncStream?: boolean` to `CloudRunLogWindowOptions`.
  - Send `sync_stream=true|false` when provided.
- Modify `apps/desktop/src/test/client.test.ts`
  - Cover HTTP query generation for `syncStream`.
- Modify `docs/architecture.md`
  - Add Phase 12B boundary and move provider-native log sync to Completed.
- Modify `docs/superpowers/status.md`
  - Update current phase, verification evidence, known limits, and recommended next phase.

---

### Task 1: Aliyun ECI DescribeContainerLog Client Seam

**Files:**
- Modify: `apps/api/app/ai_company_api/services/aliyun_clients.py`
- Modify: `apps/api/tests/test_aliyun_clients.py`

- [ ] **Step 1: Write the failing SDK seam test**

In `apps/api/tests/test_aliyun_clients.py`, extend the import block from `ai_company_api.services.aliyun_clients` to include `AliyunEciDescribeContainerLogRequest`.

```python
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunEciCreateContainerGroupRequest,
    AliyunEciDescribeContainerLogRequest,
    AliyunMnsSendMessageRequest,
    AliyunOssPutObjectRequest,
    SdkAliyunEciClient,
    SdkAliyunOssClient,
    get_aliyun_client_bundle,
    set_aliyun_client_bundle_for_tests,
)
```

Add this test after `test_sdk_aliyun_eci_client_delete_container_group_builds_request`:

```python
def test_sdk_aliyun_eci_client_describe_container_log_builds_request(
    monkeypatch,
) -> None:
    captured = {}

    class FakeDescribeContainerLogRequest:
        def __init__(
            self,
            *,
            region_id,
            container_group_id,
            container_name,
            tail,
            limit_bytes,
            timestamps,
        ):
            self.region_id = region_id
            self.container_group_id = container_group_id
            self.container_name = container_name
            self.tail = tail
            self.limit_bytes = limit_bytes
            self.timestamps = timestamps

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def describe_container_log(self, request):
            captured["request"] = request
            body = SimpleNamespace(content="worker log line\n", request_id="req-log-1")
            return SimpleNamespace(body=body)

    class FakeConfig:
        def __init__(self, *, access_key_id, access_key_secret, region_id):
            self.access_key_id = access_key_id
            self.access_key_secret = access_key_secret
            self.region_id = region_id

    eci_package = ModuleType("alibabacloud_eci20180808")
    eci_client_module = ModuleType("alibabacloud_eci20180808.client")
    eci_models_module = ModuleType("alibabacloud_eci20180808.models")
    eci_client_module.Client = FakeClient
    eci_models_module.DescribeContainerLogRequest = FakeDescribeContainerLogRequest
    eci_package.models = eci_models_module

    openapi_package = ModuleType("alibabacloud_tea_openapi")
    openapi_models_module = ModuleType("alibabacloud_tea_openapi.models")
    openapi_models_module.Config = FakeConfig
    openapi_package.models = openapi_models_module

    monkeypatch.setitem(sys.modules, "alibabacloud_eci20180808", eci_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.client",
        eci_client_module,
    )
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_eci20180808.models",
        eci_models_module,
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_tea_openapi", openapi_package)
    monkeypatch.setitem(
        sys.modules,
        "alibabacloud_tea_openapi.models",
        openapi_models_module,
    )

    result = SdkAliyunEciClient(_aliyun_settings()).describe_container_log(
        AliyunEciDescribeContainerLogRequest(
            region_id="cn-hangzhou",
            container_group_id="eci-cg-run-1",
            container_name="ai-scdc-run-1",
            tail=500,
            limit_bytes=1024,
            timestamps=True,
        )
    )

    assert result == {"content": "worker log line\n", "request_id": "req-log-1"}
    assert captured["request"].region_id == "cn-hangzhou"
    assert captured["request"].container_group_id == "eci-cg-run-1"
    assert captured["request"].container_name == "ai-scdc-run-1"
    assert captured["request"].tail == 500
    assert captured["request"].limit_bytes == 1024
    assert captured["request"].timestamps is True
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
pytest apps/api/tests/test_aliyun_clients.py::test_sdk_aliyun_eci_client_describe_container_log_builds_request -v
```

Expected: FAIL with `ImportError` for `AliyunEciDescribeContainerLogRequest` or `AttributeError` for `describe_container_log`.

- [ ] **Step 3: Add the client request and protocol method**

In `apps/api/app/ai_company_api/services/aliyun_clients.py`, add this dataclass after `AliyunEciCreateContainerGroupRequest`:

```python
@dataclass(frozen=True)
class AliyunEciDescribeContainerLogRequest:
    region_id: str
    container_group_id: str
    container_name: str
    tail: int = 2000
    limit_bytes: int = 1024 * 1024
    timestamps: bool = False
```

Add this method to `class AliyunEciClient(Protocol)` after `create_container_group()`:

```python
    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict[str, Any]:
        ...
```

- [ ] **Step 4: Implement the SDK method**

In `SdkAliyunEciClient`, add this method between `create_container_group()` and `delete_container_group()`:

```python
    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict[str, Any]:
        from alibabacloud_eci20180808.client import Client
        from alibabacloud_eci20180808 import models as eci_models
        from alibabacloud_tea_openapi import models as openapi_models

        settings = require_aliyun_settings(
            provider_name="eci",
            required_names=(
                "access_key_id",
                "access_key_secret",
                "region_id",
            ),
            settings=self.settings,
        )
        client = Client(
            openapi_models.Config(
                access_key_id=settings.access_key_id,
                access_key_secret=settings.access_key_secret,
                region_id=request.region_id,
            )
        )
        result = client.describe_container_log(
            eci_models.DescribeContainerLogRequest(
                region_id=request.region_id,
                container_group_id=request.container_group_id,
                container_name=request.container_name,
                tail=request.tail,
                limit_bytes=request.limit_bytes,
                timestamps=request.timestamps,
            )
        )
        body = getattr(result, "body", None)
        return {
            "content": getattr(body, "content", "") or "",
            "request_id": getattr(body, "request_id", None),
        }
```

- [ ] **Step 5: Run the seam tests**

Run:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -v
```

Expected: PASS for all Aliyun client tests.

- [ ] **Step 6: Commit**

```powershell
git add apps/api/app/ai_company_api/services/aliyun_clients.py apps/api/tests/test_aliyun_clients.py
git commit -m "feat: add aliyun eci log describe seam"
```

---

### Task 2: Runtime Provider Log Sync Contract And Remote Stub

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_runtime.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Extend test imports**

In `apps/api/tests/test_cloud_run_api.py`, add these imports near the existing service imports:

```python
from ai_company_api.services.remote_runtime import (
    RemoteRuntimeLogSyncRequest,
    RemoteStubRuntimeProvider,
)
```

- [ ] **Step 2: Write failing direct provider test**

Add this test near `test_remote_stub_runtime_submission_persists_log_stream_object_metadata`:

```python
def test_remote_stub_log_sync_writes_deterministic_log_ref(tmp_path: Path) -> None:
    database_path = tmp_path / "remote-stub-log-sync.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    init_db(engine)
    with Session(engine) as session:
        result = RemoteStubRuntimeProvider().sync_logs(
            session,
            RemoteRuntimeLogSyncRequest(
                workspace_id="dev_workspace",
                project_id="project_1",
                task_id="task_1",
                cloud_run_id="cloud_run_1",
                runtime_job_id="remote-stub-job-cloud_run_1",
                storage_provider="local_inline",
                current_log_stream_ref=None,
            ),
        )

        assert result.status == "updated"
        assert result.log_stream_ref is not None
        assert result.log_stream_ref.kind == "log"
        assert result.log_stream_ref.content_type == "text/plain"
        text = get_object_storage_provider("local_inline").read_text(
            session,
            result.log_stream_ref,
        )
        assert text == (
            "Remote runtime submitted via remote_stub.\n"
            "Remote runtime log sync via remote_stub.\n"
        )
```

Add this second test immediately after it:

```python
def test_remote_stub_log_sync_is_unchanged_when_digest_matches(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "remote-stub-log-sync-unchanged.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    init_db(engine)
    with Session(engine) as session:
        provider = RemoteStubRuntimeProvider()
        first = provider.sync_logs(
            session,
            RemoteRuntimeLogSyncRequest(
                workspace_id="dev_workspace",
                project_id="project_1",
                task_id="task_1",
                cloud_run_id="cloud_run_1",
                runtime_job_id="remote-stub-job-cloud_run_1",
                storage_provider="local_inline",
                current_log_stream_ref=None,
            ),
        )
        assert first.log_stream_ref is not None

        second = provider.sync_logs(
            session,
            RemoteRuntimeLogSyncRequest(
                workspace_id="dev_workspace",
                project_id="project_1",
                task_id="task_1",
                cloud_run_id="cloud_run_1",
                runtime_job_id="remote-stub-job-cloud_run_1",
                storage_provider="local_inline",
                current_log_stream_ref=first.log_stream_ref,
            ),
        )

        assert second.status == "unchanged"
        assert second.log_stream_ref is None
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_remote_stub_log_sync_writes_deterministic_log_ref apps/api/tests/test_cloud_run_api.py::test_remote_stub_log_sync_is_unchanged_when_digest_matches -v
```

Expected: FAIL with `ImportError` for `RemoteRuntimeLogSyncRequest` or `AttributeError` for `sync_logs`.

- [ ] **Step 4: Add runtime sync dataclasses and protocol**

In `apps/api/app/ai_company_api/services/remote_runtime.py`, update imports:

```python
from hashlib import sha256
from typing import Literal, Protocol
```

Add this exception after `RemoteRuntimeSubmissionError`:

```python
class RemoteRuntimeLogSyncError(Exception):
    pass
```

Add this method to `class RemoteRuntimeProvider(Protocol)` after `submit()`:

```python
    def sync_logs(
        self,
        session: Session,
        request: "RemoteRuntimeLogSyncRequest",
    ) -> "RemoteRuntimeLogSyncResult":
        ...
```

Add these dataclasses after `RemoteRuntimeSubmissionResult`:

```python
@dataclass(frozen=True)
class RemoteRuntimeLogSyncRequest:
    workspace_id: str
    project_id: str
    task_id: str
    cloud_run_id: str
    runtime_job_id: str | None
    storage_provider: str | None
    current_log_stream_ref: ObjectStorageRef | None


@dataclass(frozen=True)
class RemoteRuntimeLogSyncResult:
    status: Literal["updated", "unchanged", "skipped", "unsupported"]
    log_stream_ref: ObjectStorageRef | None = None
    reason: str | None = None
```

- [ ] **Step 5: Implement `RemoteStubRuntimeProvider.sync_logs()`**

Add this method to `RemoteStubRuntimeProvider` after `submit()`:

```python
    def sync_logs(
        self,
        session: Session,
        request: RemoteRuntimeLogSyncRequest,
    ) -> RemoteRuntimeLogSyncResult:
        if request.storage_provider != "local_inline":
            return RemoteRuntimeLogSyncResult(
                status="skipped",
                reason="remote_stub_log_sync_requires_local_inline_storage",
            )

        content = (
            "Remote runtime submitted via remote_stub.\n"
            "Remote runtime log sync via remote_stub.\n"
        )
        digest = sha256(content.encode("utf-8")).hexdigest()
        if (
            request.current_log_stream_ref is not None
            and request.current_log_stream_ref.sha256 == digest
        ):
            return RemoteRuntimeLogSyncResult(status="unchanged")

        ref = get_object_storage_provider("local_inline").put_text(
            session,
            ObjectStorageWrite(
                workspace_id=request.workspace_id,
                cloud_run_id=request.cloud_run_id,
                kind="log",
                content=content,
                content_type="text/plain",
            ),
        )
        return RemoteRuntimeLogSyncResult(status="updated", log_stream_ref=ref)
```

- [ ] **Step 6: Add an unsupported default to Aliyun for now**

Add this method to `AliyunEciRuntimeProvider` after `submit()`; Task 4 replaces it with real sync:

```python
    def sync_logs(
        self,
        session: Session,
        request: RemoteRuntimeLogSyncRequest,
    ) -> RemoteRuntimeLogSyncResult:
        return RemoteRuntimeLogSyncResult(
            status="unsupported",
            reason="aliyun_eci_log_sync_not_enabled",
        )
```

- [ ] **Step 7: Run the direct provider tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_remote_stub_log_sync_writes_deterministic_log_ref apps/api/tests/test_cloud_run_api.py::test_remote_stub_log_sync_is_unchanged_when_digest_matches -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add apps/api/app/ai_company_api/services/remote_runtime.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: add runtime log sync contract"
```

---

### Task 3: Log Sync Service And API Query Parameter

**Files:**
- Create: `apps/api/app/ai_company_api/services/cloud_run_log_sync.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_run_logs.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write route gating tests**

Add this test near the existing log window tests:

```python
def test_cloud_run_log_window_does_not_sync_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def sync_should_not_run(session, *, cloud_run):
        raise AssertionError("sync should not run by default")

    monkeypatch.setattr(
        "ai_company_api.services.cloud_run_logs.sync_cloud_run_log_stream",
        sync_should_not_run,
        raising=False,
    )

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

    response = client.get(f"/cloud-runs/{queued['id']}/logs/window")

    assert response.status_code == 200
```

Add this test immediately after it:

```python
def test_cloud_run_log_window_include_stream_false_does_not_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def sync_should_not_run(session, *, cloud_run):
        raise AssertionError("sync should not run when include_stream is false")

    monkeypatch.setattr(
        "ai_company_api.services.cloud_run_logs.sync_cloud_run_log_stream",
        sync_should_not_run,
        raising=False,
    )

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
        params={"include_stream": "false", "sync_stream": "true"},
    )

    assert response.status_code == 200
```

- [ ] **Step 2: Write remote stub API sync test**

Add this test after the route gating tests:

```python
def test_cloud_run_log_window_sync_stream_refreshes_remote_stub_log_stream(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "runtime_provider": "remote_stub",
            "storage_provider": "local_inline",
        },
    ).json()["cloud_run"]
    before_uri = queued["log_stream_uri"]

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true", "sync_stream": "true", "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    stream_messages = [
        entry["message"]
        for entry in body["entries"]
        if entry["source"] == "log_stream"
    ]
    assert stream_messages == [
        "Remote runtime submitted via remote_stub.",
        "Remote runtime log sync via remote_stub.",
    ]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        assert cloud_run.log_stream_uri != before_uri
        assert cloud_run.log_stream_sha256 is not None
        assert cloud_run.log_stream_size_bytes is not None
        assert cloud_run.log_stream_content_type == "text/plain"
```

Add this test immediately after it:

```python
def test_cloud_run_log_window_sync_stream_does_not_churn_remote_stub_metadata(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "runtime_provider": "remote_stub",
            "storage_provider": "local_inline",
        },
    ).json()["cloud_run"]

    first = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true", "sync_stream": "true", "limit": 20},
    )
    assert first.status_code == 200
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        first_uri = cloud_run.log_stream_uri
        first_sha256 = cloud_run.log_stream_sha256

    second = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true", "sync_stream": "true", "limit": 20},
    )
    assert second.status_code == 200
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        assert cloud_run.log_stream_uri == first_uri
        assert cloud_run.log_stream_sha256 == first_sha256
```

- [ ] **Step 3: Run route tests and verify they fail**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "sync_stream and remote_stub" -v
```

Expected: FAIL because the route has no `sync_stream` behavior and `cloud_run_log_sync.py` does not exist.

- [ ] **Step 4: Create the sync service**

Create `apps/api/app/ai_company_api/services/cloud_run_log_sync.py`:

```python
from __future__ import annotations

from sqlmodel import Session

from ai_company_api.models.entities import CloudRun
from ai_company_api.services.aliyun_config import AliyunConfigurationError
from ai_company_api.services.object_storage import ObjectStorageError, ObjectStorageRef
from ai_company_api.services.remote_runtime import (
    RemoteRuntimeLogSyncRequest,
    RemoteRuntimeLogSyncResult,
    RemoteRuntimeProviderNotFound,
    get_remote_runtime_provider,
)


def sync_cloud_run_log_stream(
    session: Session,
    *,
    cloud_run: CloudRun,
) -> RemoteRuntimeLogSyncResult:
    try:
        provider = get_remote_runtime_provider(cloud_run.runtime_provider)
    except RemoteRuntimeProviderNotFound:
        return RemoteRuntimeLogSyncResult(
            status="unsupported",
            reason="unknown_runtime_provider",
        )

    if provider is None:
        return RemoteRuntimeLogSyncResult(
            status="skipped",
            reason="missing_runtime_provider",
        )

    request = RemoteRuntimeLogSyncRequest(
        workspace_id=cloud_run.workspace_id,
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        cloud_run_id=cloud_run.id,
        runtime_job_id=cloud_run.runtime_job_id,
        storage_provider=cloud_run.storage_provider,
        current_log_stream_ref=_current_log_stream_ref(cloud_run),
    )
    try:
        result = provider.sync_logs(session, request)
    except (AliyunConfigurationError, ObjectStorageError):
        return RemoteRuntimeLogSyncResult(
            status="skipped",
            reason="log_sync_provider_unavailable",
        )
    except Exception:
        return RemoteRuntimeLogSyncResult(
            status="skipped",
            reason="log_sync_provider_failed",
        )

    if result.status == "updated" and result.log_stream_ref is not None:
        _persist_log_stream_ref(cloud_run, result.log_stream_ref)
        session.add(cloud_run)
        session.flush()
    return result


def _current_log_stream_ref(cloud_run: CloudRun) -> ObjectStorageRef | None:
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


def _persist_log_stream_ref(cloud_run: CloudRun, ref: ObjectStorageRef) -> None:
    cloud_run.log_stream_uri = ref.uri
    cloud_run.log_stream_sha256 = ref.sha256
    cloud_run.log_stream_size_bytes = ref.size_bytes
    cloud_run.log_stream_content_type = ref.content_type
```

- [ ] **Step 5: Wire sync into log window assembly**

In `apps/api/app/ai_company_api/services/cloud_run_logs.py`, add the import:

```python
from ai_company_api.services.cloud_run_log_sync import sync_cloud_run_log_stream
```

Replace the `list_cloud_run_log_window()` signature and first block with:

```python
def list_cloud_run_log_window(
    session: Session,
    *,
    cloud_run_id: str,
    after: str | None = None,
    limit: int = 100,
    include_stream: bool = True,
    sync_stream: bool = False,
) -> CloudRunLogWindowRead:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")

    cursor = _decode_cursor(after)
    if include_stream and sync_stream:
        sync_cloud_run_log_stream(session, cloud_run=cloud_run)

    entries = _control_plane_entries(
        session,
        cloud_run=cloud_run,
        cursor=cursor,
        limit=limit + 1,
    )
```

Keep the rest of the function unchanged.

- [ ] **Step 6: Add the route query parameter**

In `apps/api/app/ai_company_api/api/routes.py`, replace `get_cloud_run_log_window()` with:

```python
def get_cloud_run_log_window(
    cloud_run_id: str,
    session: SessionDep,
    after: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    include_stream: bool = True,
    sync_stream: bool = False,
) -> CloudRunLogWindowRead:
    return list_cloud_run_log_window(
        session,
        cloud_run_id=cloud_run_id,
        after=after,
        limit=limit,
        include_stream=include_stream,
        sync_stream=sync_stream,
    )
```

- [ ] **Step 7: Run the route tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "sync_stream and remote_stub" -v
```

Expected: PASS for the new route sync tests.

- [ ] **Step 8: Run existing Phase 12A log window tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or phase_12a" -v
```

Expected: PASS; Phase 12A behavior remains compatible.

- [ ] **Step 9: Commit**

```powershell
git add apps/api/app/ai_company_api/services/cloud_run_log_sync.py apps/api/app/ai_company_api/services/cloud_run_logs.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: sync remote stub log streams"
```

---

### Task 4: Aliyun ECI Provider Log Sync

**Files:**
- Modify: `apps/api/app/ai_company_api/services/remote_runtime.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Update test imports**

In `apps/api/tests/test_cloud_run_api.py`, extend the Aliyun client import block:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunClientBundle,
    AliyunEciCreateContainerGroupRequest,
    AliyunEciDescribeContainerLogRequest,
    AliyunMnsSendMessageRequest,
    AliyunOssPutObjectRequest,
)
```

- [ ] **Step 2: Extend `FakeAliyunEciClient`**

Replace `FakeAliyunEciClient` in `apps/api/tests/test_cloud_run_api.py` with:

```python
class FakeAliyunEciClient:
    def __init__(self, log_content: str = "aliyun worker started\n") -> None:
        self.requests: list[AliyunEciCreateContainerGroupRequest] = []
        self.describe_log_requests: list[AliyunEciDescribeContainerLogRequest] = []
        self.log_content = log_content

    def create_container_group(
        self,
        request: AliyunEciCreateContainerGroupRequest,
    ) -> dict:
        self.requests.append(request)
        return {"container_group_id": f"eci-cg-{request.cloud_run_id}"}

    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict:
        self.describe_log_requests.append(request)
        return {"content": self.log_content, "request_id": "req-log-1"}
```

Add this fake after `FailingAliyunEciClient`:

```python
class FailingAliyunEciLogClient(FakeAliyunEciClient):
    def describe_container_log(
        self,
        request: AliyunEciDescribeContainerLogRequest,
    ) -> dict:
        self.describe_log_requests.append(request)
        raise RuntimeError("describe log failed with secret=provider-secret")
```

- [ ] **Step 3: Write Aliyun API sync test**

Add this test near the Aliyun provider tests:

```python
def test_cloud_run_log_window_sync_stream_refreshes_aliyun_eci_log_stream(
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
    fake_mns = FakeAliyunMnsClient()
    fake_oss = FakeAliyunOssClient()
    fake_eci = FakeAliyunEciClient(
        log_content=(
            "aliyun worker started\n"
            "provider token=aliyun-secret Bearer abc.def\n"
        )
    )
    monkeypatch.setattr(
        "ai_company_api.services.aliyun_clients._CLIENT_BUNDLE_OVERRIDE",
        AliyunClientBundle(mns=fake_mns, oss=fake_oss, eci=fake_eci),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    ).json()["cloud_run"]

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true", "sync_stream": "true", "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    stream_messages = [
        entry["message"]
        for entry in body["entries"]
        if entry["source"] == "log_stream"
    ]
    assert stream_messages == [
        "aliyun worker started",
        "provider token=[redacted] Bearer [redacted]",
    ]
    assert "aliyun-secret" not in str(body)
    assert "abc.def" not in str(body)
    assert len(fake_eci.describe_log_requests) == 1
    request = fake_eci.describe_log_requests[0]
    assert request.region_id == "cn-hangzhou"
    assert request.container_group_id == queued["runtime_job_id"]
    assert request.container_name.startswith("ai-scdc-")
    assert request.tail == 2000
    assert request.limit_bytes == 1024 * 1024
    assert fake_oss.put_requests[-1].content == (
        b"aliyun worker started\nprovider token=aliyun-secret Bearer abc.def\n"
    )
```

- [ ] **Step 4: Run the Aliyun sync test and verify it fails**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_cloud_run_log_window_sync_stream_refreshes_aliyun_eci_log_stream -v
```

Expected: FAIL because `AliyunEciRuntimeProvider.sync_logs()` still returns `unsupported`.

- [ ] **Step 5: Update runtime imports**

In `apps/api/app/ai_company_api/services/remote_runtime.py`, add `AliyunEciDescribeContainerLogRequest` to the Aliyun import block:

```python
from ai_company_api.services.aliyun_clients import (
    AliyunEciCreateContainerGroupRequest,
    AliyunEciDescribeContainerLogRequest,
    get_aliyun_client_bundle,
)
```

- [ ] **Step 6: Implement Aliyun ECI log sync**

Replace `AliyunEciRuntimeProvider.sync_logs()` with:

```python
    def sync_logs(
        self,
        session: Session,
        request: RemoteRuntimeLogSyncRequest,
    ) -> RemoteRuntimeLogSyncResult:
        if request.storage_provider != "aliyun_oss":
            return RemoteRuntimeLogSyncResult(
                status="skipped",
                reason="aliyun_eci_log_sync_requires_aliyun_oss_storage",
            )
        if request.runtime_job_id is None:
            return RemoteRuntimeLogSyncResult(
                status="skipped",
                reason="aliyun_eci_log_sync_missing_runtime_job_id",
            )

        settings = require_aliyun_settings(
            provider_name=self.name,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
                "oss_endpoint",
                "oss_bucket",
            ),
        )
        container_name = _eci_container_group_name(
            settings.eci_container_group_prefix,
            request.cloud_run_id,
        )
        result = get_aliyun_client_bundle(settings).eci.describe_container_log(
            AliyunEciDescribeContainerLogRequest(
                region_id=settings.region_id or "",
                container_group_id=request.runtime_job_id,
                container_name=container_name,
                tail=2000,
                limit_bytes=1024 * 1024,
                timestamps=False,
            )
        )
        content = str(result.get("content") or "")
        if not content:
            return RemoteRuntimeLogSyncResult(
                status="unchanged",
                reason="aliyun_eci_log_sync_empty_provider_content",
            )

        digest = sha256(content.encode("utf-8")).hexdigest()
        if (
            request.current_log_stream_ref is not None
            and request.current_log_stream_ref.sha256 == digest
        ):
            return RemoteRuntimeLogSyncResult(status="unchanged")

        ref = get_object_storage_provider("aliyun_oss").put_text(
            session,
            ObjectStorageWrite(
                workspace_id=request.workspace_id,
                cloud_run_id=request.cloud_run_id,
                kind="log",
                content=content,
                content_type="text/plain",
            ),
        )
        return RemoteRuntimeLogSyncResult(status="updated", log_stream_ref=ref)
```

- [ ] **Step 7: Run the Aliyun sync test**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py::test_cloud_run_log_window_sync_stream_refreshes_aliyun_eci_log_stream -v
```

Expected: PASS.

- [ ] **Step 8: Run Aliyun provider regression tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "aliyun_eci or aliyun_mns or phase_10c" -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add apps/api/app/ai_company_api/services/remote_runtime.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat: sync aliyun eci log streams"
```

---

### Task 5: Provider Failure And Size Hardening

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_run_log_sync.py`
- Modify: `apps/api/app/ai_company_api/services/remote_runtime.py`

- [ ] **Step 1: Write provider failure degradation test**

Add this test after `test_cloud_run_log_window_sync_stream_refreshes_aliyun_eci_log_stream`:

```python
def test_cloud_run_log_window_sync_stream_skips_aliyun_provider_failure(
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
    fake_eci = FailingAliyunEciLogClient()
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

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    ).json()["cloud_run"]

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true", "sync_stream": "true", "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert "describe log failed" not in str(body)
    assert "provider-secret" not in str(body)
    assert len(fake_eci.describe_log_requests) == 1
```

- [ ] **Step 2: Write oversized provider snapshot test**

Add this test immediately after the failure degradation test:

```python
def test_cloud_run_log_window_sync_stream_skips_oversized_aliyun_snapshot(
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
    fake_eci = FakeAliyunEciClient(log_content="x" * (1024 * 1024 + 1))
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

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "runtime_provider": "aliyun_eci",
            "storage_provider": "aliyun_oss",
        },
    ).json()["cloud_run"]

    response = client.get(
        f"/cloud-runs/{queued['id']}/logs/window",
        params={"include_stream": "true", "sync_stream": "true", "limit": 20},
    )

    assert response.status_code == 200
    body = response.json()
    assert all(entry["source"] == "control_plane" for entry in body["entries"])
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        assert cloud_run.log_stream_size_bytes == 1024 * 1024 + 1
```

- [ ] **Step 3: Run hardening tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "sync_stream and aliyun" -v
```

Expected: PASS. Provider failures return the pre-sync window, and oversized
snapshots are persisted as metadata but skipped by the existing Phase 12A stream
read size guard.

- [ ] **Step 4: Run full targeted backend log suite**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or log_sync or sync_stream or phase_12a or phase_12b" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add apps/api/tests/test_cloud_run_api.py apps/api/app/ai_company_api/services/cloud_run_log_sync.py apps/api/app/ai_company_api/services/remote_runtime.py
git commit -m "test: harden provider log sync failures"
```

---

### Task 6: Desktop API Client `syncStream` Option

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/test/client.test.ts`

- [ ] **Step 1: Write failing HTTP client query test**

In `apps/desktop/src/test/client.test.ts`, update the existing `"lists cloud run log window"` test call:

```ts
    const window = await client.listCloudRunLogWindow("cloud_run_api", {
      after: "cursor_0",
      limit: 25,
      includeStream: true,
      syncStream: true
    });
```

Update the URL expectation in that test:

```ts
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/cloud-runs/cloud_run_api/logs/window?after=cursor_0&limit=25&include_stream=true&sync_stream=true"
    );
```

- [ ] **Step 2: Run the failing desktop test**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected: FAIL because `syncStream` is not part of `CloudRunLogWindowOptions` and the HTTP client does not serialize `sync_stream`.

- [ ] **Step 3: Add the TypeScript option**

In `apps/desktop/src/api/client.ts`, replace `CloudRunLogWindowOptions` with:

```ts
export type CloudRunLogWindowOptions = {
  after?: string | null;
  limit?: number;
  includeStream?: boolean;
  syncStream?: boolean;
};
```

- [ ] **Step 4: Serialize `sync_stream` in the HTTP client**

In `createHttpApiClient().listCloudRunLogWindow()`, add this block after `includeStream` serialization:

```ts
      if (windowOptions.syncStream !== undefined) {
        params.set("sync_stream", String(windowOptions.syncStream));
      }
```

- [ ] **Step 5: Keep fake client compatibility**

No fake client code change is needed. The fake client accepts `CloudRunLogWindowOptions` and ignores unknown window options by design. Verify that `apps/desktop/src/api/client.ts` compiles after the type extension.

- [ ] **Step 6: Run desktop tests**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "feat: add desktop log sync option"
```

---

### Task 7: Documentation And Final Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`

- [ ] **Step 1: Update architecture Phase 12 boundary**

In `docs/architecture.md`, replace the current `## Phase 12A Boundary` section with:

```markdown
## Phase 12 Boundary

Phase 12A adds a bounded log polling surface for cloud runs. The API keeps the
legacy full log list endpoint and adds a cursor-based log window endpoint that
can return persisted control-plane log rows and redacted remote log-stream
lines when the run has complete object-storage ref metadata.

Phase 12B adds optional provider-native log sync to that polling surface.
`GET /cloud-runs/{cloud_run_id}/logs/window` accepts `sync_stream=true`; when
paired with `include_stream=true`, the API asks the configured runtime provider
to refresh the run's log stream object before returning the same cursor window.
Provider failures degrade to the pre-sync window, refreshed logs still flow
through object-storage integrity metadata, and Aliyun ECI log sync uses a
tested `DescribeContainerLog` client seam.

Phase 12 does not add WebSockets, Server-Sent Events, direct MNS receive/delete
semantics, SLS-managed log stores, artifact browser UI, model-backed reviewer
or debugger agents, production KMS, or a broad provider package split.
```

In the `Completed` list, replace item 16 with:

```markdown
16. Bounded cloud-run log polling with cursor windows, safe remote log-stream reads, and optional provider-native log sync.
```

In the `Future` list, remove the provider-native live log streaming item and keep direct MNS receive/delete as the first item:

```markdown
Future:

1. Direct MNS receive/delete worker semantics while preserving callback-token-protected payload access and completion boundaries.
```

- [ ] **Step 2: Update status summary**

In `docs/superpowers/status.md`, replace the opening status paragraph with:

```markdown
The project is through Phase 12B: bounded cloud-run log polling, safe remote
log-stream reads, and optional provider-native log sync over the existing
polling API.
```

In the Completed list, replace item 16 with:

```markdown
16. Phase 12B provider log sync: cursor-based log windows, persisted
    log-stream metadata, safe object-storage reads, optional `sync_stream`
    provider refresh, deterministic `remote_stub` sync, and Aliyun ECI
    `DescribeContainerLog` sync seam.
```

Replace the latest verification block with:

````markdown
Latest Phase 12B final verification:

```text
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or log_sync or sync_stream or phase_12a or phase_12b" -v
pytest apps/api/tests/test_aliyun_clients.py -v
pytest apps/api/tests/test_cloud_object_storage.py -v
pytest apps/api/tests -v
pnpm --filter @ai-scdc/desktop test -- client.test.ts
pnpm typecheck
git diff --check
```
````

In Known Limits, replace the Phase 12A log bullet with:

```markdown
- Phase 12B adds optional provider-native log sync over the polling API, but it
  does not add WebSockets, Server-Sent Events, direct MNS receive/delete
  semantics, SLS-managed log stores, Kubernetes/ACK orchestration, billing, or
  model-backed reviewer/debugger agents.
```

In Recommended Next Phase, replace the first two items with:

```markdown
1. Add or harden direct Aliyun MNS receive/delete worker semantics while keeping
   callback-token-protected payload access and completion boundaries.
2. Harden Aliyun operations with cleanup automation, least-privilege RAM policy
   examples, provider failure runbooks, and production KMS boundaries.
```

- [ ] **Step 3: Run documentation diff check**

Run:

```powershell
git diff --check
```

Expected: PASS with no whitespace errors.

- [ ] **Step 4: Run targeted backend verification**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -k "log_window or log_stream or log_sync or sync_stream or phase_12a or phase_12b" -v
```

Expected: PASS.

- [ ] **Step 5: Run Aliyun client verification**

Run:

```powershell
pytest apps/api/tests/test_aliyun_clients.py -v
```

Expected: PASS.

- [ ] **Step 6: Run object storage verification**

Run:

```powershell
pytest apps/api/tests/test_cloud_object_storage.py -v
```

Expected: PASS.

- [ ] **Step 7: Run full API tests**

Run:

```powershell
pytest apps/api/tests -v
```

Expected: PASS. The existing Starlette/httpx deprecation warning may still appear.

- [ ] **Step 8: Run desktop client tests**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected: PASS.

- [ ] **Step 9: Run typecheck**

Run:

```powershell
pnpm typecheck
```

Expected: PASS.

- [ ] **Step 10: Commit docs**

```powershell
git add docs/architecture.md docs/superpowers/status.md
git commit -m "docs: document phase 12b provider log sync"
```

---

## Execution Notes

- Keep commits task-sized. If a test task reveals a small missing helper, include it in that task's commit rather than creating a broad cleanup commit.
- Do not add WebSocket, SSE, SLS, MNS receive/delete, artifact browser UI, or provider package split work in Phase 12B.
- Do not append a new `CloudRunLogEntry` row on every `sync_stream=true` poll. The sync status is internal and the user-facing value is the refreshed log window.
- Do not expose raw provider exception messages in API responses or log-window payloads.
- Preserve Phase 12A cursor semantics and response schema.

## Final Handoff

After all tasks pass, use `superpowers:requesting-code-review` for a focused review of:

- provider sync failure handling;
- object-storage integrity metadata updates;
- Aliyun `DescribeContainerLog` SDK request shape;
- desktop query serialization compatibility;
- documentation accuracy.

Then use `superpowers:verification-before-completion` before reporting completion.
