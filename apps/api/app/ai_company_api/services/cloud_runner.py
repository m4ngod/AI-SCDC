from datetime import datetime, timedelta, timezone
import os
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, update
from sqlmodel import Session, select

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
from ai_company_api.schemas.api import (
    CloudRunCreate,
    CloudRunExecutionResultCreate,
    CloudRunLeaseRead,
    CloudRunLogEntryRead,
    CloudRunRead,
    CloudRunResultRead,
    PatchArtifactRead,
)
from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxCommandSelection,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    repo_url_redaction_secrets,
    select_cloud_sandbox_executor,
)
from ai_company_api.services.github_repository import (
    get_active_github_credential,
    validate_github_repository_url,
)
from ai_company_api.services.repository import create_task_event, get_repository, get_task
from ai_company_api.services.sandbox_profiles import validate_sandbox_profile_for_repo
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)

SENSITIVE_PAYLOAD_KEYS = {
    "token",
    "github_token",
    "access_token",
    "authorization",
    "password",
    "secret",
}
SENSITIVE_PAYLOAD_KEY_PARTS = ("token", "secret", "password", "authorization")
CLOUD_RUN_TERMINAL_STATUSES = {"patch_ready", "failed", "cancelled"}
DEFAULT_QUEUE_PROVIDER = "local_db"
DEFAULT_LEASE_SECONDS = 60
DEFAULT_LEASE_CLAIM_CANDIDATE_LIMIT = 25


def _is_sensitive_payload_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    if key.lower() in SENSITIVE_PAYLOAD_KEYS:
        return True
    return any(part in normalized for part in SENSITIVE_PAYLOAD_KEY_PARTS)


def redact_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***"
            if _is_sensitive_payload_key(key)
            else redact_sensitive_values(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_values(item) for item in value]
    return value


def _append_cloud_run_log(
    session: Session,
    *,
    cloud_run: CloudRun,
    event: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
) -> CloudRunLogEntry:
    entry = CloudRunLogEntry(
        cloud_run_id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        event=event,
        message=message,
        level=level,
        payload=redact_sensitive_values(payload) if payload else None,
        created_at=created_at or utc_now(),
    )
    session.add(entry)
    return entry


def start_cloud_run(
    session: Session,
    task_id: str,
    data: CloudRunCreate,
) -> CloudRunResultRead:
    return enqueue_cloud_run(session, task_id, data)


def enqueue_cloud_run(
    session: Session,
    task_id: str,
    data: CloudRunCreate,
) -> CloudRunResultRead:
    task = get_task(session, task_id)
    repository = get_repository(session, data.repo_id)
    if repository.project_id != task.project_id:
        raise HTTPException(
            status_code=400,
            detail="Repository does not belong to task project",
        )
    if repository.provider != "github":
        raise HTTPException(status_code=400, detail="Cloud runs require a GitHub repository")
    if repository.connection_status != "active":
        raise HTTPException(status_code=400, detail="GitHub repository is not active")

    executor = select_cloud_sandbox_executor()
    sandbox_profile_id: str | None = None
    patch_command_key: str | None = None
    test_command_keys: list[str] = []
    if executor.sandbox_kind == "docker_local":
        if data.sandbox_profile_id is None:
            raise HTTPException(
                status_code=400,
                detail="Docker cloud runs require a sandbox profile",
            )
        if repository.github_credential_id is None:
            raise HTTPException(status_code=404, detail="GitHub credential not found")
        validate_github_repository_url(
            repository.repo_url,
            owner=repository.github_owner or "",
            repo=repository.github_repo or "",
        )
        profile = validate_sandbox_profile_for_repo(
            session,
            data.sandbox_profile_id,
            project_id=task.project_id,
            repo_id=repository.id,
        )
        patch_command, test_commands = _select_profile_commands(profile, data)
        sandbox_profile_id = profile.id
        patch_command_key = patch_command.key
        test_command_keys = [command.key for command in test_commands]

    cloud_run = CloudRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        base_branch=repository.default_branch,
        head_branch="",
        status="queued",
        sandbox_kind=executor.sandbox_kind,
        sandbox_profile_id=sandbox_profile_id,
        patch_command_key=patch_command_key,
        test_command_keys=test_command_keys,
    )
    session.add(cloud_run)
    session.flush()

    head_branch = f"ai-scdc/task-{task.id}-{cloud_run.id}"
    cloud_run.head_branch = head_branch

    local_run = LocalTaskRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        status="queued",
        runner_kind=executor.sandbox_kind,
        base_branch=repository.default_branch,
    )
    session.add(local_run)
    session.flush()

    cloud_run.local_run_id = local_run.id
    cloud_run.updated_at = utc_now()
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="queued",
        message="Cloud run queued.",
        payload={
            "repo_id": repository.id,
            "sandbox_kind": executor.sandbox_kind,
            "sandbox_profile_id": sandbox_profile_id,
            "patch_command_key": patch_command_key,
            "test_command_keys": test_command_keys,
        },
    )
    session.add(local_run)
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return CloudRunResultRead(
        cloud_run=_cloud_run_read(cloud_run),
        patch_artifact=None,
    )


def process_next_cloud_run(
    session: Session,
    *,
    worker_id: str = "local-worker",
) -> CloudRunResultRead | None:
    cloud_run = session.exec(
        select(CloudRun)
        .where(CloudRun.status == "queued")
        .order_by(CloudRun.created_at, CloudRun.id)
    ).first()
    if cloud_run is None:
        return None
    return process_cloud_run(session, cloud_run_id=cloud_run.id, worker_id=worker_id)


def claim_next_cloud_run_lease(
    session: Session,
    *,
    worker_id: str,
    worker_kind: str = "remote_stub",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> CloudRunLeaseRead | None:
    candidate_ids = session.exec(
        select(CloudRun.id)
        .where(
            CloudRun.status == "queued",
            CloudRun.cancel_requested.is_(False),
            CloudRun.attempt_count < CloudRun.max_attempts,
        )
        .order_by(CloudRun.created_at, CloudRun.id)
        .limit(DEFAULT_LEASE_CLAIM_CANDIDATE_LIMIT)
    ).all()

    for cloud_run_id in candidate_ids:
        cloud_run = session.get(CloudRun, cloud_run_id)
        if cloud_run is None:
            continue

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
            continue

        session.refresh(cloud_run)
        local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
        local_run.status = "running"
        local_run.updated_at = now
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="lease_claimed",
            message="Cloud run lease claimed by remote worker.",
            payload={
                "worker_id": worker_id,
                "worker_kind": worker_kind,
                "lease_id_suffix": lease_id.rsplit("_", 1)[-1],
                "lease_seconds": lease_seconds,
                "attempt_count": cloud_run.attempt_count,
            },
        )
        session.add(local_run)
        session.add(cloud_run)
        session.commit()
        session.refresh(cloud_run)
        return _cloud_run_lease_read(cloud_run)

    return None


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
                "lease_id_suffix": cloud_run.lease_id[-6:]
                if cloud_run.lease_id
                else None,
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


def _command_result_from_create(data) -> CommandResult:
    return CommandResult(
        command=data.command,
        exit_code=data.exit_code,
        stdout=data.stdout,
        stderr=data.stderr,
        duration_ms=data.duration_ms,
        timed_out=data.timed_out,
    )


def _sandbox_execution_result_from_create(
    data: CloudRunExecutionResultCreate,
) -> SandboxExecutionResult:
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


def process_cloud_run(
    session: Session,
    *,
    cloud_run_id: str,
    worker_id: str = "local-worker",
) -> CloudRunResultRead:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    if cloud_run.status != "queued":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run is not queued",
        )

    local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
    now = utc_now()
    if not _claim_cloud_run(session, cloud_run_id=cloud_run.id, worker_id=worker_id, now=now):
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run is not queued",
        )
    session.refresh(cloud_run)
    local_run.status = "running"
    local_run.updated_at = now
    log_clock = _EventClock()
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="claimed",
        message="Cloud run claimed by local worker.",
        payload={"worker_id": worker_id},
        created_at=log_clock.next(),
    )
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="started",
        message="Cloud run execution started.",
        payload={"worker_id": worker_id},
        created_at=log_clock.next(),
    )
    event_clock = _EventClock()
    _create_cloud_run_event(
        session,
        event_clock,
        cloud_run.task_id,
        "cloud_run_started",
        {"cloud_run_id": cloud_run.id, "repo_id": cloud_run.repo_id},
    )
    session.add(local_run)
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    session.refresh(local_run)
    try:
        return _execute_claimed_cloud_run(session, cloud_run=cloud_run)
    except HTTPException as exc:
        return _mark_claimed_cloud_run_failed(
            session,
            cloud_run=cloud_run,
            local_run=local_run,
            failure_reason="cloud_run_preflight_failed",
            message=str(exc.detail),
        )


def _claim_cloud_run(
    session: Session,
    *,
    cloud_run_id: str,
    worker_id: str,
    now: datetime,
) -> bool:
    result = session.exec(
        update(CloudRun)
        .where(CloudRun.id == cloud_run_id, CloudRun.status == "queued")
        .values(
            status="running",
            worker_id=worker_id,
            claimed_at=now,
            updated_at=now,
        )
    )
    return result.rowcount == 1


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


def _mark_claimed_cloud_run_failed(
    session: Session,
    *,
    cloud_run: CloudRun,
    local_run: LocalTaskRun,
    failure_reason: str,
    message: str,
) -> CloudRunResultRead:
    completed_at = utc_now()
    local_run.status = "failed"
    local_run.failure_reason = failure_reason
    local_run.updated_at = completed_at
    cloud_run.status = "failed"
    cloud_run.failure_reason = failure_reason
    cloud_run.command_results = [
        {
            "command": "cloud run preflight",
            "exit_code": 1,
            "stdout": "",
            "stderr": message,
            "duration_ms": 0,
            "timed_out": False,
        }
    ]
    cloud_run.completed_at = completed_at
    cloud_run.updated_at = completed_at
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="failed",
        message="Cloud run preflight failed.",
        level="error",
        payload={"failure_reason": failure_reason, "message": message},
    )
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="completed",
        message="Cloud run processing completed.",
        payload={"status": "failed"},
    )
    session.add(local_run)
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return CloudRunResultRead(cloud_run=_cloud_run_read(cloud_run), patch_artifact=None)


def _mark_claimed_cloud_run_cancelled(
    session: Session,
    *,
    cloud_run: CloudRun,
    local_run: LocalTaskRun,
    execution_result: SandboxExecutionResult,
    secrets: list[str],
    log_clock: "_EventClock",
) -> CloudRunResultRead:
    completed_at = utc_now()
    local_run.status = "cancelled"
    local_run.patch_artifact_id = None
    local_run.failure_reason = None
    local_run.updated_at = completed_at
    cloud_run.status = "cancelled"
    cloud_run.patch_artifact_id = None
    cloud_run.command_results = _command_result_payloads(
        [
            *execution_result.command_results,
            *execution_result.test_command_results,
        ],
        secrets=secrets,
    )
    cloud_run.failure_reason = None
    cloud_run.cancel_requested = True
    cloud_run.cancel_requested_at = cloud_run.cancel_requested_at or completed_at
    cloud_run.cancelled_at = cloud_run.cancelled_at or completed_at
    cloud_run.completed_at = completed_at
    cloud_run.updated_at = completed_at
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="cancelled",
        message="Running cloud run cancelled after execution finished.",
        payload={"status": "cancelled"},
        created_at=log_clock.next(),
    )
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="completed",
        message="Cloud run processing completed.",
        payload={"status": "cancelled"},
        created_at=log_clock.next(),
    )
    session.add(local_run)
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return CloudRunResultRead(cloud_run=_cloud_run_read(cloud_run), patch_artifact=None)


def cancel_cloud_run(session: Session, *, cloud_run_id: str) -> CloudRunRead:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    if cloud_run.status in CLOUD_RUN_TERMINAL_STATUSES:
        return _cloud_run_read(cloud_run)

    now = utc_now()

    if cloud_run.status == "queued":
        if not _cancel_queued_cloud_run(session, cloud_run_id=cloud_run.id, now=now):
            session.rollback()
            cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
            if cloud_run.status in CLOUD_RUN_TERMINAL_STATUSES:
                return _cloud_run_read(cloud_run)
            now = utc_now()
            return _request_running_cloud_run_cancel_and_log(
                session,
                cloud_run_id=cloud_run.id,
                now=now,
            )
        session.refresh(cloud_run)
        local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
        local_run.status = "cancelled"
        local_run.updated_at = now
        session.add(local_run)
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="cancelled",
            message="Queued cloud run cancelled.",
        )
    else:
        return _request_running_cloud_run_cancel_and_log(
            session,
            cloud_run_id=cloud_run.id,
            now=now,
        )

    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return _cloud_run_read(cloud_run)


def _cancel_queued_cloud_run(
    session: Session,
    *,
    cloud_run_id: str,
    now: datetime,
) -> bool:
    result = session.exec(
        update(CloudRun)
        .where(CloudRun.id == cloud_run_id, CloudRun.status == "queued")
        .values(
            status="cancelled",
            cancel_requested=True,
            cancel_requested_at=now,
            cancelled_at=now,
            completed_at=now,
            updated_at=now,
        )
    )
    return result.rowcount == 1


def _request_running_cloud_run_cancel_and_log(
    session: Session,
    *,
    cloud_run_id: str,
    now: datetime,
) -> CloudRunRead:
    if not _request_running_cloud_run_cancel(session, cloud_run_id=cloud_run_id, now=now):
        session.rollback()
        cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
        return _cloud_run_read(cloud_run)
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    session.refresh(cloud_run)
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="cancel_requested",
        message="Cancellation requested.",
    )
    session.add(cloud_run)
    session.commit()
    session.refresh(cloud_run)
    return _cloud_run_read(cloud_run)


def _request_running_cloud_run_cancel(
    session: Session,
    *,
    cloud_run_id: str,
    now: datetime,
) -> bool:
    result = session.exec(
        update(CloudRun)
        .where(
            CloudRun.id == cloud_run_id,
            CloudRun.status == "running",
            CloudRun.completed_at.is_(None),
        )
        .values(
            cancel_requested=True,
            cancel_requested_at=func.coalesce(CloudRun.cancel_requested_at, now),
            updated_at=now,
        )
    )
    return result.rowcount == 1


def list_cloud_run_logs(
    session: Session,
    *,
    cloud_run_id: str,
) -> list[CloudRunLogEntryRead]:
    _get_cloud_run_or_404(session, cloud_run_id)
    entries = session.exec(
        select(CloudRunLogEntry)
        .where(CloudRunLogEntry.cloud_run_id == cloud_run_id)
        .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
    ).all()
    return [_cloud_run_log_entry_read(entry) for entry in entries]


def _execute_claimed_cloud_run(
    session: Session,
    *,
    cloud_run: CloudRun,
) -> CloudRunResultRead:
    task = get_task(session, cloud_run.task_id)
    repository = get_repository(session, cloud_run.repo_id)
    local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
    executor = select_cloud_sandbox_executor()
    docker_image: str | None = None
    patch_command: SandboxCommandSelection | None = None
    test_commands: list[SandboxCommandSelection] = []
    sandbox_env: dict[str, str] = {}
    network_enabled = True
    github_token: str | None = None
    if cloud_run.sandbox_kind == "docker_local":
        if cloud_run.sandbox_profile_id is None:
            raise HTTPException(
                status_code=400,
                detail="Docker cloud runs require a sandbox profile",
            )
        if repository.github_credential_id is None:
            raise HTTPException(status_code=404, detail="GitHub credential not found")
        validate_github_repository_url(
            repository.repo_url,
            owner=repository.github_owner or "",
            repo=repository.github_repo or "",
        )
        profile = validate_sandbox_profile_for_repo(
            session,
            cloud_run.sandbox_profile_id,
            project_id=task.project_id,
            repo_id=repository.id,
        )
        patch_command, test_commands = _select_profile_commands(
            profile,
            CloudRunCreate(
                repo_id=repository.id,
                sandbox_profile_id=cloud_run.sandbox_profile_id,
                patch_command_key=cloud_run.patch_command_key,
                test_command_keys=cloud_run.test_command_keys or [],
            ),
        )
        docker_image = profile.docker_image
        sandbox_env = _sandbox_profile_env(profile.allowed_env_vars or [])
        network_enabled = profile.network_enabled
        credential = get_active_github_credential(session, repository.github_credential_id)
        github_token = DevSecretVault().open(credential.encrypted_token)

    try:
        execution_result = executor.run(
            SandboxExecutionRequest(
                task_id=task.id,
                cloud_run_id=cloud_run.id,
                title=task.title,
                description=task.description,
                repo_url=repository.repo_url,
                github_owner=repository.github_owner,
                github_repo=repository.github_repo,
                base_branch=cloud_run.base_branch or repository.default_branch,
                head_branch=cloud_run.head_branch,
                allowed_paths=task.allowed_paths or [],
                required_tests=task.required_tests or [],
                docker_image=docker_image,
                patch_command=patch_command,
                test_commands=test_commands,
                env=sandbox_env,
                network_enabled=network_enabled,
                github_token=github_token,
            )
        )
    except Exception:
        execution_result = _executor_exception_result(cloud_run.sandbox_kind)

    secrets = _redaction_secrets(sandbox_env, repository.repo_url, github_token)
    return _finalize_claimed_cloud_run_result(
        session,
        cloud_run=cloud_run,
        execution_result=execution_result,
        secrets=secrets,
    )


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
    event_clock = _EventClock()
    log_clock = _EventClock()

    if cloud_run.cancel_requested:
        return _mark_claimed_cloud_run_cancelled(
            session,
            cloud_run=cloud_run,
            local_run=local_run,
            execution_result=execution_result,
            secrets=secrets,
            log_clock=log_clock,
        )

    should_create_patch_artifact = _should_create_patch_artifact(execution_result)
    cloud_run, task, repository, local_run = _reload_claimed_cloud_run(
        session,
        cloud_run_id=cloud_run_id,
        task_id=task_id,
        execution_result=execution_result,
    )
    event_clock = _EventClock()
    log_clock = _EventClock()
    if cloud_run.cancel_requested:
        return _mark_claimed_cloud_run_cancelled(
            session,
            cloud_run=cloud_run,
            local_run=local_run,
            execution_result=execution_result,
            secrets=secrets,
            log_clock=log_clock,
        )
    if not _claim_cloud_run_finalization(
        session,
        cloud_run_id=cloud_run_id,
        now=utc_now(),
    ):
        session.rollback()
        cloud_run, task, repository, local_run = _reload_claimed_cloud_run(
            session,
            cloud_run_id=cloud_run_id,
            task_id=task_id,
            execution_result=execution_result,
        )
        log_clock = _EventClock()
        if cloud_run.cancel_requested:
            return _mark_claimed_cloud_run_cancelled(
                session,
                cloud_run=cloud_run,
                local_run=local_run,
                execution_result=execution_result,
                secrets=secrets,
                log_clock=log_clock,
            )
        return _existing_cloud_run_result(session, cloud_run=cloud_run)
    session.refresh(cloud_run)

    if not should_create_patch_artifact:
        failure_command_results = [
            *execution_result.command_results,
            *execution_result.test_command_results,
        ]
        if execution_result.test_command_results:
            test_run = LocalTestRun(
                project_id=task.project_id,
                task_id=task.id,
                local_run_id=local_run.id,
                patch_artifact_id=None,
                status=execution_result.test_result,
                commands=execution_result.tests_run,
                command_results=_command_result_payloads(
                    execution_result.test_command_results,
                    secrets=secrets,
                ),
                failure_reason=execution_result.failure_reason,
                completed_at=utc_now(),
            )
            session.add(test_run)

        local_run.status = execution_result.status
        local_run.failure_reason = execution_result.failure_reason
        local_run.updated_at = utc_now()
        cloud_run.status = execution_result.status
        cloud_run.command_results = _command_result_payloads(
            failure_command_results,
            secrets=secrets,
        )
        cloud_run.failure_reason = execution_result.failure_reason
        completed_at = utc_now()
        cloud_run.completed_at = completed_at
        cloud_run.updated_at = completed_at
        _create_cloud_run_event(
            session,
            event_clock,
            task.id,
            "cloud_run_failed",
            {
                "cloud_run_id": cloud_run.id,
                "local_run_id": local_run.id,
                "failure_reason": execution_result.failure_reason,
            },
        )
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="failed",
            message="Cloud run failed.",
            level="error",
            payload={"failure_reason": execution_result.failure_reason},
            created_at=log_clock.next(),
        )
        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="completed",
            message="Cloud run processing completed.",
            payload={"status": execution_result.status},
            created_at=log_clock.next(),
        )
        session.add(local_run)
        session.add(cloud_run)
        session.add(task)
        session.commit()
        session.refresh(cloud_run)
        return CloudRunResultRead(
            cloud_run=_cloud_run_read(cloud_run),
            patch_artifact=None,
        )

    artifact = PatchArtifact(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        summary=execution_result.summary,
        files_changed=execution_result.files_changed,
        tests_run=execution_result.tests_run,
        test_result=execution_result.test_result,
        risks=execution_result.risks,
        diff_text=execution_result.diff_text,
    )
    session.add(artifact)
    session.flush()

    if execution_result.test_command_results:
        test_run = LocalTestRun(
            project_id=task.project_id,
            task_id=task.id,
            local_run_id=local_run.id,
            patch_artifact_id=artifact.id,
            status=execution_result.test_result,
            commands=execution_result.tests_run,
            command_results=_command_result_payloads(
                execution_result.test_command_results,
                secrets=secrets,
            ),
            failure_reason=execution_result.failure_reason,
            completed_at=utc_now(),
        )
        session.add(test_run)

    local_run.status = execution_result.status
    local_run.patch_artifact_id = artifact.id
    local_run.failure_reason = execution_result.failure_reason
    local_run.updated_at = utc_now()
    cloud_run.status = execution_result.status
    cloud_run.patch_artifact_id = artifact.id
    cloud_run.command_results = _command_result_payloads(
        execution_result.command_results,
        secrets=secrets,
    )
    cloud_run.failure_reason = execution_result.failure_reason
    completed_at = utc_now()
    cloud_run.completed_at = completed_at
    cloud_run.updated_at = completed_at
    _create_cloud_run_event(
        session,
        event_clock,
        task.id,
        "patch_artifact_created",
        {
            "cloud_run_id": cloud_run.id,
            "local_run_id": local_run.id,
            "patch_artifact_id": artifact.id,
            "files_changed": artifact.files_changed,
        },
    )
    if execution_result.status == "failed":
        _create_cloud_run_event(
            session,
            event_clock,
            task.id,
            "cloud_run_failed",
            {
                "cloud_run_id": cloud_run.id,
                "local_run_id": local_run.id,
                "failure_reason": execution_result.failure_reason,
                "patch_artifact_id": artifact.id,
            },
        )
    terminal_event = "patch_ready" if execution_result.status == "patch_ready" else "failed"
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event=terminal_event,
        message="Cloud run produced a patch artifact."
        if terminal_event == "patch_ready"
        else "Cloud run failed after producing a patch artifact.",
        level="info" if terminal_event == "patch_ready" else "error",
        payload={
            "patch_artifact_id": artifact.id,
            "failure_reason": execution_result.failure_reason,
        },
        created_at=log_clock.next(),
    )
    _append_cloud_run_log(
        session,
        cloud_run=cloud_run,
        event="completed",
        message="Cloud run processing completed.",
        payload={"status": execution_result.status},
        created_at=log_clock.next(),
    )
    task.repo_id = repository.id
    task.branch_name = cloud_run.head_branch
    task.worktree_ref = execution_result.worktree_ref
    _transition_task_to_patch_ready(session, event_clock, task)

    session.add(local_run)
    session.add(cloud_run)
    session.add(task)
    session.commit()
    session.refresh(cloud_run)
    session.refresh(artifact)
    return CloudRunResultRead(
        cloud_run=_cloud_run_read(cloud_run),
        patch_artifact=_patch_artifact_read(artifact),
    )


def _claim_cloud_run_finalization(
    session: Session,
    *,
    cloud_run_id: str,
    now: datetime,
) -> bool:
    result = session.exec(
        update(CloudRun)
        .where(
            CloudRun.id == cloud_run_id,
            CloudRun.status == "running",
            CloudRun.completed_at.is_(None),
            CloudRun.cancel_requested.is_(False),
        )
        .values(updated_at=now)
    )
    return result.rowcount == 1


def _reload_claimed_cloud_run(
    session: Session,
    *,
    cloud_run_id: str,
    task_id: str,
    execution_result: SandboxExecutionResult,
) -> tuple[CloudRun, Task, Repository, LocalTaskRun]:
    session.rollback()
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    task = get_task(session, task_id)
    repository = get_repository(session, cloud_run.repo_id)
    local_run = _get_cloud_run_local_run_or_404(session, cloud_run)
    local_run.runner_kind = execution_result.runner_kind
    local_run.base_sha = execution_result.base_sha
    local_run.head_sha = execution_result.head_sha
    local_run.worktree_path = execution_result.worktree_ref
    return cloud_run, task, repository, local_run


def _existing_cloud_run_result(
    session: Session,
    *,
    cloud_run: CloudRun,
) -> CloudRunResultRead:
    artifact = (
        session.get(PatchArtifact, cloud_run.patch_artifact_id)
        if cloud_run.patch_artifact_id is not None
        else None
    )
    return CloudRunResultRead(
        cloud_run=_cloud_run_read(cloud_run),
        patch_artifact=_patch_artifact_read(artifact) if artifact is not None else None,
    )


def list_cloud_runs(session: Session, task_id: str) -> list[CloudRunRead]:
    get_task(session, task_id)
    statement = (
        select(CloudRun)
        .where(CloudRun.task_id == task_id)
        .order_by(CloudRun.created_at, CloudRun.id)
    )
    return [_cloud_run_read(cloud_run) for cloud_run in session.exec(statement).all()]


def _get_cloud_run_or_404(session: Session, cloud_run_id: str) -> CloudRun:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")
    return cloud_run


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
    lease_expires_at = cloud_run.lease_expires_at
    if lease_expires_at is not None and lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    if lease_expires_at is None or lease_expires_at < utc_now():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is not current",
        )
    return cloud_run


def _get_cloud_run_local_run_or_404(
    session: Session,
    cloud_run: CloudRun,
) -> LocalTaskRun:
    if cloud_run.local_run_id is None:
        raise HTTPException(status_code=404, detail="Cloud run local run not found")
    local_run = session.get(LocalTaskRun, cloud_run.local_run_id)
    if local_run is None:
        raise HTTPException(status_code=404, detail="Cloud run local run not found")
    return local_run


def get_cloud_run_read(session: Session, cloud_run_id: str) -> CloudRunRead:
    cloud_run = _get_cloud_run_or_404(session, cloud_run_id)
    return _cloud_run_read(cloud_run)


def _select_profile_commands(
    profile,
    data: CloudRunCreate,
) -> tuple[SandboxCommandSelection, list[SandboxCommandSelection]]:
    patch_command = _select_command(
        profile.patch_commands or [],
        data.patch_command_key,
        kind="patch",
    )
    if data.test_command_keys:
        test_commands = [
            _select_command(profile.test_commands or [], key, kind="test")
            for key in data.test_command_keys
        ]
    else:
        test_commands = [
            _command_selection(command)
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
) -> SandboxCommandSelection:
    if requested_key is not None:
        for command in commands:
            if command.get("key") == requested_key:
                return _command_selection(command)
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sandbox {kind} command key",
        )

    defaults = [command for command in commands if command.get("is_default") is True]
    if kind == "patch" and len(defaults) != 1:
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile requires exactly one default patch command",
        )
    if kind == "test" and len(defaults) != 1:
        raise HTTPException(
            status_code=400,
            detail="Sandbox profile requires exactly one default test command",
        )
    return _command_selection(defaults[0])


def _command_selection(command: dict) -> SandboxCommandSelection:
    return SandboxCommandSelection(
        key=command["key"],
        label=command["label"],
        command=command["command"],
        timeout_seconds=command.get("timeout_seconds", 300),
    )


def _sandbox_profile_env(allowed_env_vars: list[str]) -> dict[str, str]:
    return {name: os.environ[name] for name in allowed_env_vars if name in os.environ}


def _should_create_patch_artifact(result: SandboxExecutionResult) -> bool:
    if result.status == "patch_ready":
        return True
    return (
        result.failure_reason == "test_failed"
        and bool(result.files_changed)
        and result.diff_text.strip() != ""
    )


def _executor_exception_result(runner_kind: str) -> SandboxExecutionResult:
    return SandboxExecutionResult(
        status="failed",
        runner_kind=runner_kind,
        base_sha=None,
        head_sha=None,
        worktree_ref=None,
        summary="",
        files_changed=[],
        tests_run=[],
        test_result="not_run",
        risks=[],
        diff_text="",
        command_results=[
            CommandResult(
                command=f"{runner_kind} executor",
                exit_code=1,
                stdout="",
                stderr="Executor failed before returning a result.",
                duration_ms=0,
            )
        ],
        test_command_results=[],
        failure_reason="executor_failed",
    )


def _transition_task_to_patch_ready(
    session: Session,
    event_clock: "_EventClock",
    task: Task,
) -> None:
    current_status = TaskStatus(task.status)
    if current_status == TaskStatus.CREATED:
        _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.ASSIGNED)
        _transition_task_for_cloud_runner(
            session,
            event_clock,
            task,
            TaskStatus.IN_PROGRESS,
        )
    elif current_status in {TaskStatus.ASSIGNED, TaskStatus.FIX_REQUESTED}:
        _transition_task_for_cloud_runner(
            session,
            event_clock,
            task,
            TaskStatus.IN_PROGRESS,
        )

    if TaskStatus(task.status) != TaskStatus.PATCH_READY:
        _transition_task_for_cloud_runner(
            session,
            event_clock,
            task,
            TaskStatus.PATCH_READY,
        )


def _transition_task_for_cloud_runner(
    session: Session,
    event_clock: "_EventClock",
    task: Task,
    requested_status: TaskStatus,
) -> None:
    current_status = TaskStatus(task.status)
    try:
        next_status = validate_transition(
            current_status,
            requested_status,
            actor_type="system",
        )
    except InvalidTaskTransition as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": str(exc),
                "current_status": current_status.value,
                "requested_status": requested_status.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        ) from exc

    task.status = next_status
    task.updated_at = utc_now()
    session.add(task)
    _create_cloud_run_event(
        session,
        event_clock,
        task.id,
        "task_transitioned",
        {"from_status": current_status.value, "to_status": next_status.value},
    )


class _EventClock:
    def __init__(self) -> None:
        self._base = utc_now()
        self._offset = 0

    def next(self) -> datetime:
        self._offset += 1
        return self._base + timedelta(microseconds=self._offset)


def _create_cloud_run_event(
    session: Session,
    event_clock: _EventClock,
    task_id: str,
    event_type: str,
    payload: dict,
) -> None:
    event = create_task_event(
        session,
        task_id,
        event_type,
        "system",
        "cloud_runner",
        payload,
    )
    event.created_at = event_clock.next()


def _redaction_secrets(
    env: dict[str, str],
    repo_url: str = "",
    github_token: str | None = None,
) -> list[str]:
    return [
        value
        for value in [*env.values(), github_token, *repo_url_redaction_secrets(repo_url)]
        if value
    ]


def _command_result_payloads(
    command_results: list[CommandResult],
    *,
    secrets: list[str],
) -> list[dict]:
    return [result.as_payload(secrets=secrets) for result in command_results]


def _cloud_run_read(cloud_run: CloudRun) -> CloudRunRead:
    return CloudRunRead(
        id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        repo_id=cloud_run.repo_id,
        local_run_id=cloud_run.local_run_id,
        sandbox_profile_id=cloud_run.sandbox_profile_id,
        patch_command_key=cloud_run.patch_command_key,
        test_command_keys=cloud_run.test_command_keys or [],
        command_results=cloud_run.command_results or [],
        base_branch=cloud_run.base_branch,
        head_branch=cloud_run.head_branch,
        status=cloud_run.status,
        sandbox_kind=cloud_run.sandbox_kind,
        patch_artifact_id=cloud_run.patch_artifact_id,
        failure_reason=cloud_run.failure_reason,
        cancel_requested=cloud_run.cancel_requested,
        cancel_requested_at=cloud_run.cancel_requested_at,
        cancelled_at=cloud_run.cancelled_at,
        worker_id=cloud_run.worker_id,
        claimed_at=cloud_run.claimed_at,
        completed_at=cloud_run.completed_at,
        queue_provider=cloud_run.queue_provider,
        remote_worker_kind=cloud_run.remote_worker_kind,
        lease_id=cloud_run.lease_id,
        lease_expires_at=cloud_run.lease_expires_at,
        heartbeat_at=cloud_run.heartbeat_at,
        attempt_count=cloud_run.attempt_count,
        max_attempts=cloud_run.max_attempts,
        last_queue_error=cloud_run.last_queue_error,
        created_at=cloud_run.created_at,
        updated_at=cloud_run.updated_at,
    )


def _cloud_run_lease_read(cloud_run: CloudRun) -> CloudRunLeaseRead:
    if (
        cloud_run.lease_id is None
        or cloud_run.lease_expires_at is None
        or cloud_run.heartbeat_at is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cloud run lease is incomplete",
        )
    return CloudRunLeaseRead(
        cloud_run=_cloud_run_read(cloud_run),
        lease_id=cloud_run.lease_id,
        lease_expires_at=cloud_run.lease_expires_at,
        heartbeat_at=cloud_run.heartbeat_at,
        attempt_count=cloud_run.attempt_count,
        cancel_requested=cloud_run.cancel_requested,
    )


def _cloud_run_log_entry_read(entry: CloudRunLogEntry) -> CloudRunLogEntryRead:
    return CloudRunLogEntryRead(
        id=entry.id,
        cloud_run_id=entry.cloud_run_id,
        level=entry.level,
        event=entry.event,
        message=entry.message,
        payload=entry.payload,
        created_at=entry.created_at,
    )


def _patch_artifact_read(artifact: PatchArtifact) -> PatchArtifactRead:
    return PatchArtifactRead(
        id=artifact.id,
        workspace_id=artifact.workspace_id,
        project_id=artifact.project_id,
        task_id=artifact.task_id,
        local_run_id=artifact.local_run_id,
        summary=artifact.summary,
        files_changed=artifact.files_changed,
        tests_run=artifact.tests_run,
        test_result=artifact.test_result,
        risks=artifact.risks,
        diff_text=artifact.diff_text,
        created_at=artifact.created_at,
    )
