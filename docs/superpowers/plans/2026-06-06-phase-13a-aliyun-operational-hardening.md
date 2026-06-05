# Phase 13A Aliyun Operational Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add test-backed Aliyun MNS receipt recovery, best-effort Aliyun ECI terminal cleanup, and operator documentation for RAM, runbooks, cleanup boundaries, and production KMS handoff.

**Architecture:** Keep operational recovery service-level only: no unauthenticated HTTP route deletes provider resources. `cloud_runner.py` owns cloud-run state transitions and redacted control-plane logs, so it gets two narrow helpers that reuse the existing MNS queue provider seam and Aliyun client bundle seam. Documentation records the operational boundary for MNS, OSS, ECI, and production KMS without adding OSS deletion or real KMS dependencies.

**Tech Stack:** FastAPI service layer, SQLModel sessions, pytest with fake Aliyun clients, Aliyun MNS/OSS/ECI provider seams, Markdown operations docs, existing pnpm desktop tests and typecheck.

---

## File Structure

Create or update these files:

```text
apps/api/app/ai_company_api/services/cloud_runner.py
apps/api/tests/test_cloud_run_api.py
docs/operations/aliyun-operational-runbook.md
docs/operations/aliyun-ram-policies.md
README.md
docs/architecture.md
docs/superpowers/status.md
STATUS.md
```

Do not modify `apps/api/app/ai_company_api/api/routes.py` for Phase 13A. The cleanup helpers are service seams for future authenticated operator tooling and direct tests; they are not public destructive endpoints.

---

## Task 1: Add Retained MNS Receipt Recovery

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`

**Purpose:** Give operators a tested service helper that can retry deletion of an Aliyun MNS receipt retained after terminal completion, while keeping `queue_receipt` internal-only.

- [ ] **Step 1: Add the MNS recovery success and failure tests**

Insert these tests after `test_aliyun_mns_completion_delete_failure_keeps_terminal_state_and_redacts_receipt` in `apps/api/tests/test_cloud_run_api.py`:

```python
def test_aliyun_mns_retained_receipt_recovery_deletes_and_clears_receipt_without_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    cloud_run_id, lease_id, fake_mns = _start_claimed_aliyun_mns_run(
        tmp_path,
        monkeypatch,
    )
    fake_mns.delete_error = RuntimeError(
        "delete failed for receipt-1 secret=first-provider-secret"
    )
    queued_payload = json.loads(fake_mns.requests[0].body)
    client = build_client(database_path)
    payload = remote_stub_completion_payload(cloud_run_id)
    payload["worker_id"] = queued_payload["worker_id"]
    payload["callback_token"] = queued_payload["callback_token"]

    completion = client.post(
        f"/cloud-run-worker/leases/{lease_id}/complete",
        json=payload,
    )
    assert completion.status_code == 200
    assert completion.json()["cloud_run"]["status"] == "patch_ready"

    fake_mns.delete_error = None
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with Session(engine) as session:
        result = cloud_runner.retry_retained_mns_queue_receipt_delete(
            session,
            cloud_run_id=cloud_run_id,
        )

    assert result.status == "succeeded"
    assert result.reason == "mns_message_deleted"
    assert result.cloud_run.status == "patch_ready"
    assert result.cloud_run.external_status == "mns_message_deleted"
    assert result.cloud_run.external_error is None
    assert fake_mns.delete_requests[-1].receipt_handle == "receipt-1"

    response = client.get(f"/cloud-runs/{cloud_run_id}")
    assert response.status_code == 200
    assert "queue_receipt" not in response.text
    assert "receipt-1" not in response.text

    with Session(engine) as session:
        persisted = session.get(CloudRun, cloud_run_id)
        log_entries = session.exec(
            select(CloudRunLogEntry)
            .where(CloudRunLogEntry.cloud_run_id == cloud_run_id)
            .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
        ).all()

    assert persisted is not None
    assert persisted.status == "patch_ready"
    assert persisted.queue_receipt is None
    assert persisted.external_status == "mns_message_deleted"
    serialized_logs = "\n".join(
        f"{entry.event}\n{entry.message}\n"
        f"{json.dumps(entry.payload, sort_keys=True, default=str)}"
        for entry in log_entries
    )
    assert "mns_message_delete_retry_attempted" in serialized_logs
    assert "mns_message_delete_recovered" in serialized_logs
    assert "receipt-1" not in serialized_logs
    assert "first-provider-secret" not in serialized_logs


def test_aliyun_mns_retained_receipt_recovery_failure_keeps_terminal_state_and_redacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    cloud_run_id, lease_id, fake_mns = _start_claimed_aliyun_mns_run(
        tmp_path,
        monkeypatch,
    )
    fake_mns.delete_error = RuntimeError("delete failed for receipt-1")
    queued_payload = json.loads(fake_mns.requests[0].body)
    client = build_client(database_path)
    payload = remote_stub_completion_payload(cloud_run_id)
    payload["worker_id"] = queued_payload["worker_id"]
    payload["callback_token"] = queued_payload["callback_token"]

    completion = client.post(
        f"/cloud-run-worker/leases/{lease_id}/complete",
        json=payload,
    )
    assert completion.status_code == 200

    fake_mns.delete_error = RuntimeError(
        "delete failed for receipt-1 secret=provider-secret token=abc123"
    )
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with Session(engine) as session:
        result = cloud_runner.retry_retained_mns_queue_receipt_delete(
            session,
            cloud_run_id=cloud_run_id,
        )

    assert result.status == "failed"
    assert result.reason == "mns_message_delete_failed"
    assert result.cloud_run.status == "patch_ready"
    assert result.cloud_run.external_status == "mns_message_delete_failed"
    assert "provider-secret" not in (result.cloud_run.external_error or "")
    assert "abc123" not in (result.cloud_run.external_error or "")

    response = client.get(f"/cloud-runs/{cloud_run_id}")
    assert response.status_code == 200
    assert "queue_receipt" not in response.text
    assert "receipt-1" not in response.text
    assert "provider-secret" not in response.text
    assert "abc123" not in response.text

    with Session(engine) as session:
        persisted = session.get(CloudRun, cloud_run_id)
        log_entries = session.exec(
            select(CloudRunLogEntry)
            .where(CloudRunLogEntry.cloud_run_id == cloud_run_id)
            .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
        ).all()

    assert persisted is not None
    assert persisted.status == "patch_ready"
    assert persisted.queue_receipt == "receipt-1"
    assert persisted.external_status == "mns_message_delete_failed"
    assert "provider-secret" not in (persisted.external_error or "")
    assert "abc123" not in (persisted.external_error or "")
    serialized_logs = "\n".join(
        f"{entry.event}\n{entry.message}\n"
        f"{json.dumps(entry.payload, sort_keys=True, default=str)}"
        for entry in log_entries
    )
    assert "mns_message_delete_retry_failed" in serialized_logs
    assert "receipt-1" not in serialized_logs
    assert "provider-secret" not in serialized_logs
    assert "abc123" not in serialized_logs
```

- [ ] **Step 2: Run the new MNS tests and verify they fail for the missing helper**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery"
```

Expected:

```text
FAILED ... AttributeError: module 'ai_company_api.services.cloud_runner' has no attribute 'retry_retained_mns_queue_receipt_delete'
```

- [ ] **Step 3: Add the service result type and MNS recovery helper**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, change the imports at the top:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
```

Add the result type after the provider constants:

```python
CloudRunProviderOperationStatus = Literal["skipped", "succeeded", "failed"]


@dataclass(frozen=True)
class CloudRunProviderOperationResult:
    status: CloudRunProviderOperationStatus
    reason: str
    cloud_run: CloudRunRead
```

Add these helpers immediately after `_ack_mns_queue_receipt_after_terminal_commit`:

```python
def retry_retained_mns_queue_receipt_delete(
    session: Session,
    *,
    cloud_run_id: str,
) -> CloudRunProviderOperationResult:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    skip_reason = _mns_receipt_recovery_skip_reason(cloud_run)
    if skip_reason is not None:
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="mns_message_delete_retry_skipped",
            message="Aliyun MNS receipt recovery skipped.",
            payload={
                "queue_provider": cloud_run.queue_provider,
                "reason": skip_reason,
            },
        )
        cloud_run.updated_at = utc_now()
        session.add(cloud_run)
        session.commit()
        session.refresh(cloud_run)
        return CloudRunProviderOperationResult(
            status="skipped",
            reason=skip_reason,
            cloud_run=_cloud_run_read(cloud_run),
        )

    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="mns_message_delete_retry_attempted",
        message="Aliyun MNS receipt recovery attempted.",
        payload={"queue_provider": cloud_run.queue_provider},
    )
    try:
        get_cloud_queue_provider(ALIYUN_MNS_QUEUE_PROVIDER).delete(
            queue_receipt=cloud_run.queue_receipt or ""
        )
    except CloudQueueProviderError as exc:
        cloud_run.external_status = "mns_message_delete_failed"
        cloud_run.external_error = (
            _redact_external_error(str(exc)) or "mns_message_delete_failed"
        )
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="mns_message_delete_retry_failed",
            message="Aliyun MNS receipt recovery failed.",
            level="warning",
            payload={
                "queue_provider": cloud_run.queue_provider,
                "reason": "mns_message_delete_failed",
                "external_error": cloud_run.external_error,
            },
        )
        result_status: CloudRunProviderOperationStatus = "failed"
        result_reason = "mns_message_delete_failed"
    else:
        cloud_run.queue_receipt = None
        cloud_run.external_status = "mns_message_deleted"
        cloud_run.external_error = None
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="mns_message_delete_recovered",
            message="Aliyun MNS receipt recovered and deleted.",
            payload={"queue_provider": cloud_run.queue_provider},
        )
        result_status = "succeeded"
        result_reason = "mns_message_deleted"

    cloud_run.updated_at = utc_now()
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return CloudRunProviderOperationResult(
        status=result_status,
        reason=result_reason,
        cloud_run=_cloud_run_read(cloud_run),
    )


def _mns_receipt_recovery_skip_reason(cloud_run: CloudRun) -> str | None:
    if cloud_run.queue_provider != ALIYUN_MNS_QUEUE_PROVIDER:
        return "mns_receipt_recovery_requires_aliyun_mns"
    if cloud_run.status not in CLOUD_RUN_TERMINAL_STATUSES:
        return "mns_receipt_recovery_requires_terminal_state"
    if not cloud_run.queue_receipt:
        return "mns_receipt_recovery_missing_receipt"
    return None
```

- [ ] **Step 4: Run the focused MNS recovery tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery"
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Run the existing MNS completion and claim tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns_completion_delete_failure or aliyun_mns_completion_deletes_receipt or aliyun_mns_claim_persists_receipt or aliyun_mns_claim_rejects_mismatched_message_id"
```

Expected:

```text
4 passed
```

- [ ] **Step 6: Commit the MNS recovery seam**

Run:

```bash
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "Add Aliyun MNS receipt recovery seam"
```

Expected:

```text
[codex/...] Add Aliyun MNS receipt recovery seam
```

---

## Task 2: Add Best-Effort ECI Terminal Cleanup

**Files:**
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`

**Purpose:** Add a service-only helper that deletes a persisted Aliyun ECI container group by `runtime_job_id` after a cloud run is terminal, logs attempted/skipped/succeeded/failed states, and never rewinds terminal cloud-run status.

- [ ] **Step 1: Add a fake ECI delete-failure client**

Insert this class immediately after `CleanupRecordingAliyunEciClient` in `apps/api/tests/test_cloud_run_api.py`:

```python
class DeleteFailingAliyunEciClient(CleanupRecordingAliyunEciClient):
    def delete_container_group(self, *, region_id: str, container_group_id: str) -> None:
        self.deleted_container_group_ids.append(container_group_id)
        raise RuntimeError(
            "delete failed secret=provider-secret token=abc123 Signature=signed-url"
        )
```

- [ ] **Step 2: Add a completed assigned-run ECI helper for cleanup tests**

Insert this helper near `_start_claimed_aliyun_mns_run` in `apps/api/tests/test_cloud_run_api.py`:

```python
def _start_completed_aliyun_eci_run(
    tmp_path: Path,
    monkeypatch,
    fake_eci,
) -> tuple[str, str]:
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

    queued_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    )
    assert queued_response.status_code == 201
    cloud_run = queued_response.json()["cloud_run"]
    worker_id = f"aliyun-eci-{cloud_run['id']}"
    callback_token = fake_eci.requests[0].environment["AI_SCDC_CALLBACK_TOKEN"]

    lease_response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": worker_id,
            "worker_kind": "aliyun_eci",
            "queue_provider": "aliyun_mns",
            "cloud_run_id": cloud_run["id"],
            "callback_token": callback_token,
            "lease_seconds": 60,
        },
    )
    assert lease_response.status_code == 201

    completion_payload = remote_stub_completion_payload(cloud_run["id"])
    completion_payload["worker_id"] = worker_id
    completion_payload["callback_token"] = callback_token
    completion_response = client.post(
        f"/cloud-run-worker/leases/{lease_response.json()['lease_id']}/complete",
        json=completion_payload,
    )
    assert completion_response.status_code == 200
    assert completion_response.json()["cloud_run"]["status"] == "patch_ready"
    return cloud_run["id"], cloud_run["runtime_job_id"]
```

- [ ] **Step 3: Add ECI cleanup success, failure, and skip tests**

Insert these tests after the new `_start_completed_aliyun_eci_run` helper:

```python
def test_aliyun_eci_terminal_cleanup_deletes_persisted_runtime_job_and_logs_safe_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    fake_eci = CleanupRecordingAliyunEciClient()
    cloud_run_id, runtime_job_id = _start_completed_aliyun_eci_run(
        tmp_path,
        monkeypatch,
        fake_eci,
    )
    engine = build_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")

    with Session(engine) as session:
        result = cloud_runner.cleanup_aliyun_eci_terminal_runtime_job(
            session,
            cloud_run_id=cloud_run_id,
        )

    assert result.status == "succeeded"
    assert result.reason == "runtime_cleanup_deleted"
    assert result.cloud_run.status == "patch_ready"
    assert result.cloud_run.runtime_job_id == runtime_job_id
    assert result.cloud_run.external_status == "runtime_cleanup_deleted"
    assert result.cloud_run.external_error is None
    assert fake_eci.deleted_container_group_ids == [runtime_job_id]

    with Session(engine) as session:
        persisted = session.get(CloudRun, cloud_run_id)
        log_entries = session.exec(
            select(CloudRunLogEntry)
            .where(CloudRunLogEntry.cloud_run_id == cloud_run_id)
            .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
        ).all()

    assert persisted is not None
    assert persisted.status == "patch_ready"
    assert persisted.runtime_job_id == runtime_job_id
    assert persisted.external_status == "runtime_cleanup_deleted"
    serialized_logs = "\n".join(
        f"{entry.event}\n{entry.message}\n"
        f"{json.dumps(entry.payload, sort_keys=True, default=str)}"
        for entry in log_entries
    )
    assert "runtime_cleanup_attempted" in serialized_logs
    assert "runtime_cleanup_deleted" in serialized_logs
    assert "AI_SCDC_ALIYUN_ACCESS_KEY_SECRET" not in serialized_logs
    assert "provider-secret" not in serialized_logs


def test_aliyun_eci_terminal_cleanup_failure_keeps_runtime_job_and_redacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    fake_eci = DeleteFailingAliyunEciClient()
    cloud_run_id, runtime_job_id = _start_completed_aliyun_eci_run(
        tmp_path,
        monkeypatch,
        fake_eci,
    )
    engine = build_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")

    with Session(engine) as session:
        result = cloud_runner.cleanup_aliyun_eci_terminal_runtime_job(
            session,
            cloud_run_id=cloud_run_id,
        )

    assert result.status == "failed"
    assert result.reason == "runtime_cleanup_failed"
    assert result.cloud_run.status == "patch_ready"
    assert result.cloud_run.runtime_job_id == runtime_job_id
    assert result.cloud_run.external_status == "runtime_cleanup_failed"
    assert "provider-secret" not in (result.cloud_run.external_error or "")
    assert "abc123" not in (result.cloud_run.external_error or "")
    assert "signed-url" not in (result.cloud_run.external_error or "")
    assert fake_eci.deleted_container_group_ids == [runtime_job_id]

    with Session(engine) as session:
        persisted = session.get(CloudRun, cloud_run_id)
        log_entries = session.exec(
            select(CloudRunLogEntry)
            .where(CloudRunLogEntry.cloud_run_id == cloud_run_id)
            .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
        ).all()

    assert persisted is not None
    assert persisted.status == "patch_ready"
    assert persisted.runtime_job_id == runtime_job_id
    assert persisted.external_status == "runtime_cleanup_failed"
    assert "provider-secret" not in (persisted.external_error or "")
    assert "abc123" not in (persisted.external_error or "")
    assert "signed-url" not in (persisted.external_error or "")
    serialized_logs = "\n".join(
        f"{entry.event}\n{entry.message}\n"
        f"{json.dumps(entry.payload, sort_keys=True, default=str)}"
        for entry in log_entries
    )
    assert "runtime_cleanup_failed" in serialized_logs
    assert "provider-secret" not in serialized_logs
    assert "abc123" not in serialized_logs
    assert "signed-url" not in serialized_logs


def test_aliyun_eci_terminal_cleanup_skips_non_terminal_run_without_delete(
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
    fake_eci = CleanupRecordingAliyunEciClient()
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

    queued_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "queue_provider": "aliyun_mns",
            "storage_provider": "aliyun_oss",
            "runtime_provider": "aliyun_eci",
        },
    )
    assert queued_response.status_code == 201
    cloud_run_id = queued_response.json()["cloud_run"]["id"]

    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with Session(engine) as session:
        result = cloud_runner.cleanup_aliyun_eci_terminal_runtime_job(
            session,
            cloud_run_id=cloud_run_id,
        )

    assert result.status == "skipped"
    assert result.reason == "runtime_cleanup_requires_terminal_state"
    assert result.cloud_run.status == "queued"
    assert fake_eci.deleted_container_group_ids == []

    with Session(engine) as session:
        skip_log = session.exec(
            select(CloudRunLogEntry).where(
                CloudRunLogEntry.cloud_run_id == cloud_run_id,
                CloudRunLogEntry.event == "runtime_cleanup_skipped",
            )
        ).one()
    assert skip_log.payload == {
        "reason": "runtime_cleanup_requires_terminal_state",
        "runtime_provider": "aliyun_eci",
    }
```

- [ ] **Step 4: Run the new ECI cleanup tests and verify they fail for the missing helper**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "terminal_cleanup"
```

Expected:

```text
FAILED ... AttributeError: module 'ai_company_api.services.cloud_runner' has no attribute 'cleanup_aliyun_eci_terminal_runtime_job'
```

- [ ] **Step 5: Add Aliyun ECI cleanup imports**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, add this import near the other service imports:

```python
from ai_company_api.services.aliyun_clients import get_aliyun_client_bundle
```

Change the Aliyun config import to:

```python
from ai_company_api.services.aliyun_config import (
    AliyunConfigurationError,
    require_aliyun_settings,
)
```

- [ ] **Step 6: Add the ECI terminal cleanup helper**

Insert this code after `_mns_receipt_recovery_skip_reason` in `apps/api/app/ai_company_api/services/cloud_runner.py`:

```python
def cleanup_aliyun_eci_terminal_runtime_job(
    session: Session,
    *,
    cloud_run_id: str,
) -> CloudRunProviderOperationResult:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    skip_reason = _aliyun_eci_runtime_cleanup_skip_reason(cloud_run)
    if skip_reason is not None:
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="runtime_cleanup_skipped",
            message="Aliyun ECI runtime cleanup skipped.",
            payload={
                "runtime_provider": cloud_run.runtime_provider,
                "reason": skip_reason,
            },
        )
        cloud_run.updated_at = utc_now()
        session.add(cloud_run)
        session.commit()
        session.refresh(cloud_run)
        return CloudRunProviderOperationResult(
            status="skipped",
            reason=skip_reason,
            cloud_run=_cloud_run_read(cloud_run),
        )

    runtime_job_id = cloud_run.runtime_job_id or ""
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="runtime_cleanup_attempted",
        message="Aliyun ECI runtime cleanup attempted.",
        payload={
            "runtime_provider": cloud_run.runtime_provider,
            "runtime_job_id_suffix": runtime_job_id[-6:],
        },
    )
    try:
        settings = require_aliyun_settings(
            provider_name=ALIYUN_ECI_RUNTIME_PROVIDER,
            required_names=(
                "region_id",
                "access_key_id",
                "access_key_secret",
            ),
        )
        get_aliyun_client_bundle(settings).eci.delete_container_group(
            region_id=settings.region_id or "",
            container_group_id=runtime_job_id,
        )
    except Exception as exc:
        cloud_run.external_status = "runtime_cleanup_failed"
        cloud_run.external_error = (
            _redact_external_error(str(exc)) or "runtime_cleanup_failed"
        )
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="runtime_cleanup_failed",
            message="Aliyun ECI runtime cleanup failed.",
            level="warning",
            payload={
                "runtime_provider": cloud_run.runtime_provider,
                "reason": "runtime_cleanup_failed",
                "external_error": cloud_run.external_error,
            },
        )
        result_status: CloudRunProviderOperationStatus = "failed"
        result_reason = "runtime_cleanup_failed"
    else:
        cloud_run.external_status = "runtime_cleanup_deleted"
        cloud_run.external_error = None
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="runtime_cleanup_deleted",
            message="Aliyun ECI runtime cleanup deleted the container group.",
            payload={
                "runtime_provider": cloud_run.runtime_provider,
                "runtime_job_id_suffix": runtime_job_id[-6:],
            },
        )
        result_status = "succeeded"
        result_reason = "runtime_cleanup_deleted"

    cloud_run.updated_at = utc_now()
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return CloudRunProviderOperationResult(
        status=result_status,
        reason=result_reason,
        cloud_run=_cloud_run_read(cloud_run),
    )


def _aliyun_eci_runtime_cleanup_skip_reason(cloud_run: CloudRun) -> str | None:
    if cloud_run.runtime_provider != ALIYUN_ECI_RUNTIME_PROVIDER:
        return "runtime_cleanup_requires_aliyun_eci"
    if cloud_run.status not in CLOUD_RUN_TERMINAL_STATUSES:
        return "runtime_cleanup_requires_terminal_state"
    if not cloud_run.runtime_job_id:
        return "runtime_cleanup_missing_runtime_job_id"
    return None
```

- [ ] **Step 7: Run the focused ECI cleanup tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "terminal_cleanup"
```

Expected:

```text
3 passed
```

- [ ] **Step 8: Run adjacent Aliyun ECI regression tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_eci_runtime_submission_creates_safe_container_request or aliyun_eci_submission_cleans_up_when_oss_manifest_seed_fails or protected_aliyun_worker_claim_requires_callback_token or legacy_aliyun_eci_assigned_run_with_stored_mns_message_id_can_claim"
```

Expected:

```text
4 passed
```

- [ ] **Step 9: Verify no public cleanup route was added**

Run:

```bash
rg -n "retry_retained_mns_queue_receipt_delete|cleanup_aliyun_eci_terminal_runtime_job" apps/api/app/ai_company_api/api/routes.py
```

Expected: no output and exit code `1`.

- [ ] **Step 10: Commit the ECI cleanup seam**

Run:

```bash
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_cloud_run_api.py
git commit -m "Add Aliyun ECI terminal cleanup seam"
```

Expected:

```text
[codex/...] Add Aliyun ECI terminal cleanup seam
```

---

## Task 3: Add Aliyun Operations Runbook And RAM Policy Docs

**Files:**
- Create: `docs/operations/aliyun-operational-runbook.md`
- Create: `docs/operations/aliyun-ram-policies.md`

**Purpose:** Give operators concrete recovery steps and least-privilege policy examples without adding OSS deletion code or real KMS dependencies.

- [ ] **Step 1: Create the operations docs directory**

Run:

```bash
New-Item -ItemType Directory -Force -Path docs/operations
```

Expected:

```text
Directory: ...\docs
Mode                 LastWriteTime         Length Name
----                 -------------         ------ ----
d----                                      operations
```

- [ ] **Step 2: Create the Aliyun operational runbook**

Create `docs/operations/aliyun-operational-runbook.md` with this content:

```markdown
# Aliyun Operational Runbook

## Scope

This runbook covers the current Aliyun execution path:

- `aliyun_mns` queue-only pull runs.
- `aliyun_oss` manifest, log, diff, command-result, and test-result objects.
- `aliyun_eci` assigned-run containers.

It does not authorize direct database edits, public destructive HTTP calls,
worker-side receipt deletion by default, or OSS object deletion from code.

## Sensitive Values

Never paste these values into tickets, chat, logs, screenshots, or commit text:

- `AI_SCDC_CALLBACK_TOKEN`
- `callback_token`
- `callback_token_hash`
- `queue_receipt`
- Aliyun access key id or access key secret values
- Signed OSS query strings
- GitHub token values
- Raw provider exception text

Safe fields for troubleshooting:

- `cloud_run.id`
- `cloud_run.status`
- `queue_provider`
- `storage_provider`
- `runtime_provider`
- `queue_message_id`
- `runtime_job_id`
- `external_status`
- `failure_reason`
- redacted `external_error`
- control-plane log `event`, `level`, and redacted `payload`

## KMS Boundary

The codebase currently uses `DevSecretVault` for development secret sealing.
Commercial production must provide a KMS-backed implementation of the existing
`SecretVault` protocol before beta traffic. Operators must not compensate by
copying plaintext access keys, callback tokens, queue receipts, signed URLs, or
GitHub tokens into operational records.

## Cleanup Decision Rules

1. Retry MNS receipt deletion only for terminal Aliyun MNS runs that still have
   an internal `queue_receipt`.
2. Treat MNS delete failure as recoverable. The cloud run can remain
   `patch_ready`, `failed`, or `cancelled` while `external_status` records
   `mns_message_delete_failed`.
3. Delete ECI container groups only by persisted `runtime_job_id`.
4. Do not delete ECI containers immediately after terminal completion when
   `DescribeContainerLog` may still be needed for provider log sync.
5. Use Aliyun OSS lifecycle rules for development-prefix retention instead of
   adding API-side object deletion.

## Case: MNS Enqueue Fails During Queue-Only Pull Creation

Operator symptom: `POST /tasks/{task_id}/cloud-runs` returns 502 with
`Cloud queue provider aliyun_mns failed to enqueue message`.

Safe fields to inspect: `cloud_run.status`, `failure_reason`,
`last_queue_error`, `external_status`, redacted `external_error`, and
`queue_enqueue_failed` log entries.

Fields that must never be pasted: access keys, callback tokens, MNS message
body, raw SDK error text, and local shell environment dumps.

Expected API state: one failed cloud run, failed local run, `failure_reason` set
to `queue_enqueue_failed`, and no exposed receipt.

Recovery action: verify `AI_SCDC_ALIYUN_MNS_ENDPOINT`,
`AI_SCDC_ALIYUN_MNS_QUEUE_NAME`, region, and RAM permission for
`mns:SendMessage`; then create a new cloud run.

Escalation: if configuration and RAM are correct, capture the redacted
`queue_enqueue_failed` log and Aliyun request id from cloud provider tooling.

## Case: MNS Receive Returns Empty Queue

Operator symptom: pull worker receives no work and keeps polling.

Safe fields to inspect: worker poll interval, MNS queue depth, `cloud_run.status`
for queued runs, and `queue_message_id`.

Fields that must never be pasted: callback token from any MNS body and raw MNS
message body.

Expected API state: queued cloud runs remain queued and unclaimed.

Recovery action: confirm the worker is using `AI_SCDC_QUEUE_PROVIDER=aliyun_mns`
and the same queue name as the API process.

Escalation: compare redacted worker logs and API enqueue logs by
`cloud_run.id`.

## Case: MNS Receive Returns Malformed Token-Bearing Payload

Operator symptom: worker logs an invalid MNS message and does not claim a run.

Safe fields to inspect: MNS message id, API `queue_message_id`, and redacted
worker parse error reason.

Fields that must never be pasted: raw message body, callback token,
`queue_receipt`, and GitHub clone credential values.

Expected API state: the target cloud run is not claimed through the malformed
delivery.

Recovery action: leave the malformed message unclaimed until the queue
visibility timeout expires, then inspect the producer version and MNS queue
source.

Escalation: if malformed messages continue, stop the worker pool and preserve
only redacted message metadata for debugging.

## Case: MNS Terminal Delete Fails And Leaves Retained Receipt

Operator symptom: terminal cloud run has `external_status` set to
`mns_message_delete_failed`.

Safe fields to inspect: `cloud_run.id`, `status`, `queue_provider`,
`queue_message_id`, `external_status`, redacted `external_error`, and
`mns_message_delete_retry_failed` log entries.

Fields that must never be pasted: `queue_receipt`, raw delete exception,
callback token, and MNS message body.

Expected API state: `status` remains terminal and internal `queue_receipt`
remains present until recovery succeeds.

Recovery action: call the service helper
`retry_retained_mns_queue_receipt_delete(session, cloud_run_id=...)` from
authenticated maintenance tooling or an operator script running inside the API
trust boundary.

Escalation: if retry fails repeatedly, verify MNS delete permission and queue
visibility state, then keep the run terminal and document only redacted status.

## Case: OSS Manifest Or Log Seeding Fails After ECI Creation

Operator symptom: ECI submission returns a controlled runtime submission
failure after a container group was created.

Safe fields to inspect: `cloud_run.id`, `failure_reason`,
`runtime_submission_failed`, `runtime_job_id` when persisted, and fake-client or
Aliyun-side delete outcome.

Fields that must never be pasted: OSS signed query strings, access keys, raw OSS
exception text, and callback token environment values.

Expected API state: cloud run failed with `runtime_submission_failed`; existing
submission code attempts to delete the just-created ECI container group.

Recovery action: fix OSS endpoint, bucket, prefix, and RAM permissions for
`oss:PutObject`, then create a new cloud run.

Escalation: if container deletion also fails, delete the known container group
from Aliyun console using `runtime_job_id` and record only redacted details.

## Case: OSS Read Fails For URI, Bucket, Prefix, Or Metadata Mismatch

Operator symptom: log window or artifact read omits remote content or returns a
safe read failure.

Safe fields to inspect: redacted object URI path, object kind, stored SHA-256,
stored size, content type, and cloud-run log event.

Fields that must never be pasted: signed query strings, raw OSS errors, access
keys, and artifact content that may contain repository secrets.

Expected API state: cloud-run metadata remains unchanged; public reads avoid
returning unverified content.

Recovery action: verify bucket, configured prefix `ai-scdc/dev`, object
existence, size, and SHA-256 through trusted Aliyun tooling.

Escalation: if metadata differs, preserve the database row and object metadata
for investigation without pasting object bodies.

## Case: ECI Submission Fails Before Container Group Id Is Available

Operator symptom: cloud-run creation fails with controlled runtime submission
error and no persisted `runtime_job_id`.

Safe fields to inspect: `cloud_run.id`, `failure_reason`,
`external_status`, redacted `external_error`, and
`remote_runtime_submission_failed` log.

Fields that must never be pasted: access keys, raw ECI SDK exception, and worker
environment variables.

Expected API state: cloud run failed, local run failed, and no ECI cleanup by
persisted id is possible.

Recovery action: verify region, image, vSwitch, security group, and ECI create
permission; then create a new cloud run.

Escalation: use Aliyun console request diagnostics with redacted API logs.

## Case: ECI Submission Succeeds But Later OSS Seeding Fails

Operator symptom: ECI create request exists, but API returns controlled runtime
submission failure.

Safe fields to inspect: known container group id, failed cloud-run id,
redacted provider status, and whether cleanup was attempted.

Fields that must never be pasted: raw OSS exception, access keys, callback
token, and full worker environment.

Expected API state: failed cloud run; ECI deletion attempted immediately by the
submission path if the runtime job id is known.

Recovery action: check whether the container group still exists. If it exists,
delete it by known id through Aliyun console or the service cleanup helper once
the run is terminal and persisted.

Escalation: if deletion is denied, validate RAM `eci:DeleteContainerGroup`
scope and capture only redacted status.

## Case: ECI Log Sync Returns Empty Or Non-Text Content

Operator symptom: `GET /cloud-runs/{id}/logs/window?include_stream=true&sync_stream=true`
returns control-plane logs but no fresh provider log lines.

Safe fields to inspect: `runtime_job_id`, `runtime_provider`,
`log_stream_uri` path without query, log stream size, and redacted sync reason.

Fields that must never be pasted: raw provider log content if it may contain
secrets, access keys, and callback token environment values.

Expected API state: cloud-run terminal status remains unchanged; log sync
degrades to the previous safe window.

Recovery action: retry after ECI log propagation, then inspect Aliyun
`DescribeContainerLog` availability for the persisted `runtime_job_id`.

Escalation: if provider log content is malformed, preserve only content type,
size, and redacted reason.

## Case: ECI Delete Fails During Best-Effort Cleanup

Operator symptom: cleanup helper returns `runtime_cleanup_failed`.

Safe fields to inspect: `cloud_run.id`, terminal `status`, `runtime_job_id`,
`external_status`, redacted `external_error`, and
`runtime_cleanup_failed` control-plane log.

Fields that must never be pasted: access keys, raw ECI delete exception, worker
environment, callback token, and signed OSS links.

Expected API state: terminal cloud-run status is unchanged and `runtime_job_id`
is retained for another cleanup attempt.

Recovery action: verify RAM `eci:DeleteContainerGroup`, confirm the group still
exists, and retry cleanup by persisted `runtime_job_id`.

Escalation: if delete is denied or the group is stuck, use Aliyun console
support flow with redacted AI-SCDC metadata.
```

- [ ] **Step 3: Create the Aliyun RAM policy examples**

Create `docs/operations/aliyun-ram-policies.md` with this content:

```markdown
# Aliyun RAM Policy Examples

## Scope

These examples use concrete development values:

- Account id: `1234567890123456`
- Region: `cn-hangzhou`
- MNS queue: `ai-scdc-cloud-runs-dev`
- OSS bucket: `ai-scdc-dev-artifacts`
- OSS prefix: `ai-scdc/dev/`
- ECI container group prefix: `ai-scdc-run-`

Adjust values in the Aliyun RAM console for each deployment. Use the Aliyun RAM
policy simulator before attaching a policy to a production role.

## API Control Plane Role

The API process can enqueue queue-only MNS work, acknowledge MNS receipts,
write and read OSS run artifacts, create ECI containers, sync ECI logs, and
delete known ECI containers by persisted id.

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "mns:SendMessage",
        "mns:ReceiveMessage",
        "mns:DeleteMessage"
      ],
      "Resource": [
        "acs:mns:cn-hangzhou:1234567890123456:/queues/ai-scdc-cloud-runs-dev/messages"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:PutObject",
        "oss:GetObject"
      ],
      "Resource": [
        "acs:oss:*:1234567890123456:ai-scdc-dev-artifacts/ai-scdc/dev/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "eci:CreateContainerGroup",
        "eci:DescribeContainerLog",
        "eci:DeleteContainerGroup"
      ],
      "Resource": [
        "acs:eci:cn-hangzhou:1234567890123456:containergroup/ai-scdc-run-*"
      ]
    }
  ]
}
```

If an ECI action rejects resource-level scoping in the policy simulator, scope
that action to the smallest Aliyun-supported resource form and enforce the
`ai-scdc-run-` prefix through API-side naming, console review, and deployment
runbooks.

The API role must not be attached to a worker container.

## Pull Worker Role

The pull worker receives MNS messages and calls the AI-SCDC API over HTTPS with
the callback token embedded in the message. The Phase 13A default keeps receipt
acknowledgement API-owned, so worker-side `mns:DeleteMessage` is not required.

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "mns:ReceiveMessage"
      ],
      "Resource": [
        "acs:mns:cn-hangzhou:1234567890123456:/queues/ai-scdc-cloud-runs-dev/messages"
      ]
    }
  ]
}
```

The pull worker role must not include ECI create/delete, OSS read/write,
GitHub credentials, model credentials, or the API process's Aliyun access key
secret.

If a deployment chooses worker-side receipt deletion in a future authenticated
worker design, add only `mns:DeleteMessage` for the same queue resource and keep
the API callback-token completion boundary.

## Assigned ECI Worker

The current assigned-run ECI worker receives:

- `AI_SCDC_API_BASE_URL`
- `AI_SCDC_CLOUD_RUN_ID`
- `AI_SCDC_WORKER_ID`
- `AI_SCDC_CALLBACK_TOKEN`
- `AI_SCDC_QUEUE_PROVIDER`
- `AI_SCDC_STORAGE_PROVIDER`

It does not need Aliyun MNS credentials because it does not poll MNS. It does
not need OSS credentials because artifact upload goes through the callback-token
protected API endpoint. It must not receive `AI_SCDC_ALIYUN_ACCESS_KEY_SECRET`.

## OSS Retention

Use OSS bucket lifecycle rules for development cleanup under
`ai-scdc/dev/cloud-runs/`. Keep artifacts and logs long enough for review,
provider log sync, and audit handoff before ECI cleanup.

Do not add API-side OSS delete-prefix behavior until authenticated
organization-scoped operator controls exist.

## Production KMS Boundary

`DevSecretVault` is development-only. Production must provide a KMS-backed
implementation of the existing `SecretVault` protocol before commercial beta.
The RAM policies here do not grant KMS permissions because Phase 13A does not
integrate a real KMS SDK.
```

- [ ] **Step 4: Verify the required runbook and policy sections exist**

Run:

```bash
rg -n "MNS Terminal Delete Fails|ECI Delete Fails|DevSecretVault|API Control Plane Role|Pull Worker Role|Assigned ECI Worker|OSS Retention" docs/operations/aliyun-operational-runbook.md docs/operations/aliyun-ram-policies.md
```

Expected:

```text
docs/operations/aliyun-operational-runbook.md:...
docs/operations/aliyun-ram-policies.md:...
```

- [ ] **Step 5: Commit the operations docs**

Run:

```bash
git add docs/operations/aliyun-operational-runbook.md docs/operations/aliyun-ram-policies.md
git commit -m "Document Aliyun operational runbooks"
```

Expected:

```text
[codex/...] Document Aliyun operational runbooks
```

---

## Task 4: Update Project Status, README, And Architecture Boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/status.md`
- Modify: `STATUS.md`

**Purpose:** Mark Phase 13A as operational hardening, preserve non-goals, and link the new operator docs from user-facing project docs.

- [ ] **Step 1: Update README phase summary**

In `README.md`, extend the first paragraph so the phase list ends with Phase 13A:

```markdown
This repo includes the Phase 0 monorepo foundation, Phase 1 planner approval loop, Phase 2 backend-first model routing and BYOK foundation, Phase 3 real planner vertical slice, Phase 4 local runner vertical slice, Phase 5 deterministic test/review/debug workflow, Phase 6 human patch approval and diff viewer workflow, Phase 7 GitHub-only cloud-run and pull-request boundary, Phase 8 Docker local sandbox executor, Phase 9 local cloud-run queue worker boundary, Phase 10A remote worker control-plane contract, Phase 10B provider-neutral remote execution-plane contract, Phase 10C Aliyun provider MVP, Phase 10D run-scoped remote worker callback token hardening, Phase 11 real remote worker execution skeleton, Phase 12A bounded cloud-run log polling and safe remote log-stream reads, Phase 12B optional provider-native log sync, Phase 12C Aliyun MNS pull-worker receipt handling, and Phase 13A Aliyun operational hardening for a desktop multi-agent software engineering console.
```

- [ ] **Step 2: Add README operations links**

Add this section after `## Local Commands` in `README.md`:

```markdown
## Aliyun Operations

Phase 13A adds service-level operational seams for Aliyun MNS receipt recovery
and Aliyun ECI terminal cleanup. These helpers are intentionally not exposed as
public destructive HTTP routes before auth/RBAC.

Operator references:

- `docs/operations/aliyun-operational-runbook.md`
- `docs/operations/aliyun-ram-policies.md`

Use OSS lifecycle rules for development object retention. Do not add broad
API-side OSS deletion until authenticated organization-scoped operator controls
exist. `DevSecretVault` remains development-only; commercial production must
provide a KMS-backed `SecretVault` implementation before beta traffic.
```

- [ ] **Step 3: Add the Phase 13A architecture boundary**

In `docs/architecture.md`, add this section after the existing Phase 12C boundary:

```markdown
## Phase 13A Boundary

Phase 13A hardens the Aliyun MNS/OSS/ECI path for operator use without widening
the product boundary. The API now has service-level seams for retrying retained
Aliyun MNS receipt deletion and best-effort Aliyun ECI terminal cleanup by
persisted `runtime_job_id`.

Cleanup failures do not rewind terminal cloud-run status. MNS receipt recovery
clears only the internal `queue_receipt` after delete succeeds, and ECI cleanup
retains `runtime_job_id` for audit and repeat attempts. Cleanup logs and
responses use redacted provider status only and never expose callback tokens,
queue receipts, access keys, signed URLs, or raw provider exceptions.

Phase 13A also documents Aliyun RAM policy examples, provider failure runbooks,
OSS lifecycle boundaries, and the production KMS boundary. It does not add a
public destructive operations API, user auth, organization RBAC, billing, a real
KMS SDK, API-side OSS deletion, or a second cloud provider.
```

- [ ] **Step 4: Update architecture roadmap**

In `docs/architecture.md`, append this completed roadmap item:

```markdown
18. Aliyun operational hardening with retained MNS receipt recovery, best-effort ECI terminal cleanup by persisted runtime id, least-privilege RAM examples, provider failure runbooks, OSS lifecycle guidance, and production KMS boundary documentation.
```

Replace the current future item with:

```markdown
1. Authenticated organization-scoped operator controls for cleanup, audit, billing, and production KMS integration before commercial beta.
2. Broader provider coverage beyond the current Aliyun MNS/OSS/ECI production-provider path while preserving callback-token-protected payload access and completion boundaries.
```

- [ ] **Step 5: Update superpowers status**

In `docs/superpowers/status.md`, change `Last verified` to the current date and update Current Phase:

```markdown
Last verified: 2026-06-06

## Current Phase

The project is through Phase 13A: Aliyun operational hardening for MNS receipt
recovery, ECI terminal cleanup, RAM policy examples, provider runbooks, OSS
lifecycle guidance, and production KMS boundaries.
```

Append this completed item:

```markdown
17. Phase 12C Aliyun MNS pull-worker claims: protected MNS deliveries,
    callback-token hash storage, message-id binding, internal-only queue
    receipts, and post-terminal MNS acknowledgement or recoverable delete
    failure handling.
18. Phase 13A Aliyun operational hardening: service-level MNS receipt recovery,
    best-effort ECI terminal cleanup, redacted cleanup logs, least-privilege RAM
    examples, provider failure runbooks, OSS lifecycle guidance, and production
    KMS boundaries.
```

Replace the `Known Limits` Phase 12B wording with:

```markdown
- Phase 13A adds service-level Aliyun cleanup and recovery seams plus
  operations docs, but it does not expose public destructive cleanup endpoints,
  add user auth/RBAC, add billing, integrate real KMS, delete OSS objects from
  code, add WebSockets/SSE, or add a second cloud provider.
```

Replace the recommended next phase with:

```markdown
The next production phase should add authenticated organization-scoped
operator controls and production KMS integration before commercial beta.
```

- [ ] **Step 6: Update root STATUS**

Replace `STATUS.md` with:

```markdown
# Phase 13A Status

## Scope

Completed Aliyun operational hardening for the current MNS/OSS/ECI execution
path. The API now has service-level helpers for retrying retained MNS receipt
deletion and best-effort ECI terminal cleanup by persisted runtime id. Cleanup
success and failure paths append redacted control-plane logs and do not expose
callback tokens, queue receipts, access keys, signed URLs, or raw provider
secrets.

No public destructive cleanup endpoint was added. OSS object cleanup remains a
bucket lifecycle responsibility until authenticated organization-scoped
operator controls exist. `DevSecretVault` remains development-only and must be
replaced by a KMS-backed `SecretVault` implementation before commercial beta.

## Operator Docs

- `docs/operations/aliyun-operational-runbook.md`
- `docs/operations/aliyun-ram-policies.md`

## Verification

- `pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery or terminal_cleanup or aliyun_mns_completion_delete_failure or aliyun_eci_submission_cleans_up"` -> pending final run
- `pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_aliyun or protected_worker or aliyun_eci"` -> pending final run
- `pytest apps/api/tests/test_aliyun_clients.py -q` -> pending final run
- `pytest apps/api/tests/test_remote_worker.py -q` -> pending final run
- `pytest apps/api/tests -q` -> pending final run
- `pnpm --filter @ai-scdc/desktop test -- client.test.ts` -> pending final run
- `pnpm typecheck` -> pending final run
- `git diff --check` -> pending final run

## Warnings

- Existing `StarletteDeprecationWarning`: `starlette.testclient` warns that
  using `httpx` is deprecated and recommends `httpx2`.
```

- [ ] **Step 7: Verify status docs mention Phase 13A and operations links**

Run:

```bash
rg -n "Phase 13A|aliyun-operational-runbook|aliyun-ram-policies|public destructive|KMS-backed" README.md docs/architecture.md docs/superpowers/status.md STATUS.md
```

Expected: matches in all four files.

- [ ] **Step 8: Commit the status and architecture docs**

Run:

```bash
git add README.md docs/architecture.md docs/superpowers/status.md STATUS.md
git commit -m "Update Phase 13A project status"
```

Expected:

```text
[codex/...] Update Phase 13A project status
```

---

## Task 5: Final Verification And Handoff

**Files:**
- Verify: full repository
- Modify: `STATUS.md`

**Purpose:** Run the focused and full verification suite, update `STATUS.md` with real results, and leave a clean branch.

- [ ] **Step 1: Run focused cleanup tests**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "retained_receipt_recovery or terminal_cleanup or aliyun_mns_completion_delete_failure or aliyun_eci_submission_cleans_up"
```

Expected:

```text
7 passed
```

- [ ] **Step 2: Run the Aliyun worker/provider regression slice**

Run:

```bash
pytest apps/api/tests/test_cloud_run_api.py -q -k "aliyun_mns or protected_aliyun or protected_worker or aliyun_eci"
```

Expected:

```text
34 passed
```

The exact count can increase if tests are added in this phase. A valid pass must include the two retained receipt recovery tests and the three terminal cleanup tests.

- [ ] **Step 3: Run Aliyun client tests**

Run:

```bash
pytest apps/api/tests/test_aliyun_clients.py -q
```

Expected:

```text
15 passed
```

- [ ] **Step 4: Run remote worker tests**

Run:

```bash
pytest apps/api/tests/test_remote_worker.py -q
```

Expected:

```text
48 passed
```

- [ ] **Step 5: Run all API tests**

Run:

```bash
pytest apps/api/tests -q
```

Expected:

```text
all tests passed with the existing Starlette/httpx deprecation warning only
```

- [ ] **Step 6: Run desktop client tests**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- client.test.ts
```

Expected:

```text
1 file passed, 34 tests passed
```

- [ ] **Step 7: Run typecheck**

Run:

```bash
pnpm typecheck
```

Expected:

```text
typecheck passed for packages/agent-protocol and apps/desktop
```

- [ ] **Step 8: Run whitespace check**

Run:

```bash
git diff --check
```

Expected:

```text
no whitespace errors
```

Git may print Windows LF-to-CRLF conversion warnings for touched text files. Those warnings are acceptable when `git diff --check` exits with code `0`.

- [ ] **Step 9: Verify cleanup helpers are not routed**

Run:

```bash
rg -n "retry_retained_mns_queue_receipt_delete|cleanup_aliyun_eci_terminal_runtime_job" apps/api/app/ai_company_api/api/routes.py
```

Expected: no output and exit code `1`.

- [ ] **Step 10: Update `STATUS.md` with actual verification results**

Replace every `pending final run` line in `STATUS.md` with the exact command result just observed. Keep the existing warning section if the Starlette/httpx warning still appears.

- [ ] **Step 11: Commit final verification status**

Run:

```bash
git add STATUS.md
git commit -m "Record Phase 13A verification"
```

Expected:

```text
[codex/...] Record Phase 13A verification
```

- [ ] **Step 12: Confirm clean status**

Run:

```bash
git status -sb
```

Expected:

```text
## codex/phase-13a-aliyun-operational-hardening
```

or the active Phase 13A branch name with no modified, added, or deleted files listed.

---

## Plan Review

- Spec coverage: MNS receipt recovery is covered by Task 1; ECI cleanup is covered by Task 2; OSS cleanup is documented as lifecycle-only in Task 3; RAM policy examples, failure runbooks, and KMS boundary are covered by Task 3; architecture, status, and README updates are covered by Task 4; full verification is covered by Task 5.
- Type consistency: both service helpers return `CloudRunProviderOperationResult`, and every test imports the helpers from `ai_company_api.services.cloud_runner`.
- Public API boundary: Task 2 and Task 5 verify that no cleanup helper is referenced from `apps/api/app/ai_company_api/api/routes.py`.
