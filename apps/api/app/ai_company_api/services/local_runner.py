from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    LocalTaskRun,
    PatchArtifact,
    Task,
    utc_now,
)
from ai_company_api.schemas.api import (
    LocalRunCreate,
    LocalTaskRunRead,
    PatchArtifactRead,
)
from ai_company_api.services.repository import (
    create_task_event,
    get_repository,
    get_task,
)
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)
from ai_company_worker.local_runner import (
    LocalRunnerError,
    LocalRunnerRequest,
    run_local_task,
)


RUN_LOCAL_TASK = run_local_task


def start_local_task_run(
    session: Session,
    task_id: str,
    data: LocalRunCreate,
) -> LocalTaskRunRead:
    task = get_task(session, task_id)
    repository = get_repository(session, data.repo_id)
    if repository.project_id != task.project_id:
        raise HTTPException(
            status_code=400,
            detail="Repository does not belong to task project",
        )

    event_clock = _EventClock()
    local_run = LocalTaskRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        status="queued",
        base_branch=repository.default_branch,
    )
    session.add(local_run)
    session.flush()
    _create_local_run_event(
        session,
        event_clock,
        task.id,
        "local_run_started",
        "system",
        "local_runner",
        {"local_run_id": local_run.id, "repo_id": repository.id},
    )

    local_run.status = "running"
    _transition_task_for_local_runner(
        session,
        event_clock,
        task,
        TaskStatus.ASSIGNED,
    )
    _transition_task_for_local_runner(
        session,
        event_clock,
        task,
        TaskStatus.IN_PROGRESS,
    )

    try:
        result = RUN_LOCAL_TASK(
            LocalRunnerRequest(
                task_id=task.id,
                run_id=local_run.id,
                repo_path=repository.local_path,
                title=task.title,
                description=task.description,
                allowed_paths=task.allowed_paths or [],
                required_tests=task.required_tests or [],
            )
        )
    except LocalRunnerError as exc:
        local_run.status = "failed"
        local_run.failure_reason = str(exc)
        local_run.updated_at = utc_now()
        session.add(local_run)
        _create_local_run_event(
            session,
            event_clock,
            task.id,
            "local_run_failed",
            "system",
            "local_runner",
            {"local_run_id": local_run.id, "failure_reason": local_run.failure_reason},
        )
        _transition_task_for_local_runner(
            session,
            event_clock,
            task,
            TaskStatus.FIX_REQUESTED,
        )
        session.commit()
        session.refresh(local_run)
        return _local_task_run_read(local_run)

    artifact = PatchArtifact(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        summary=result.summary,
        files_changed=result.files_changed,
        tests_run=result.tests_run,
        test_result=result.test_result,
        risks=result.risks,
        diff_text=result.diff_text,
    )
    session.add(artifact)
    session.flush()

    local_run.status = "patch_ready"
    local_run.base_sha = result.base_sha
    local_run.head_sha = result.head_sha
    local_run.worktree_path = result.worktree_path
    local_run.patch_artifact_id = artifact.id
    local_run.updated_at = utc_now()
    session.add(local_run)
    _create_local_run_event(
        session,
        event_clock,
        task.id,
        "patch_artifact_created",
        "system",
        "local_runner",
        {
            "local_run_id": local_run.id,
            "patch_artifact_id": artifact.id,
            "files_changed": result.files_changed,
        },
    )

    task.repo_id = repository.id
    task.branch_name = repository.default_branch
    task.worktree_ref = result.worktree_path
    _transition_task_for_local_runner(
        session,
        event_clock,
        task,
        TaskStatus.PATCH_READY,
    )
    session.commit()
    session.refresh(local_run)
    return _local_task_run_read(local_run)


def list_local_task_runs(session: Session, task_id: str) -> list[LocalTaskRunRead]:
    get_task(session, task_id)
    statement = (
        select(LocalTaskRun)
        .where(LocalTaskRun.task_id == task_id)
        .order_by(LocalTaskRun.created_at, LocalTaskRun.id)
    )
    return [_local_task_run_read(local_run) for local_run in session.exec(statement).all()]


def get_local_task_run(session: Session, local_run_id: str) -> LocalTaskRunRead:
    local_run = session.get(LocalTaskRun, local_run_id)
    if local_run is None:
        raise HTTPException(status_code=404, detail="Local task run not found")
    return _local_task_run_read(local_run)


def get_patch_artifact(session: Session, patch_artifact_id: str) -> PatchArtifactRead:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return _patch_artifact_read(artifact)


def _transition_task_for_local_runner(
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
    _create_local_run_event(
        session,
        event_clock,
        task.id,
        "task_transitioned",
        "system",
        "local_runner",
        {"from_status": current_status.value, "to_status": next_status.value},
    )
    session.add(task)


class _EventClock:
    def __init__(self) -> None:
        self._base = utc_now()
        self._offset = 0

    def next(self) -> datetime:
        self._offset += 1
        return self._base + timedelta(microseconds=self._offset)


def _create_local_run_event(
    session: Session,
    event_clock: _EventClock,
    task_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str,
    payload: dict,
) -> None:
    event = create_task_event(
        session,
        task_id,
        event_type,
        actor_type,
        actor_id,
        payload,
    )
    event.created_at = event_clock.next()


def _local_task_run_read(local_run: LocalTaskRun) -> LocalTaskRunRead:
    return LocalTaskRunRead(
        id=local_run.id,
        workspace_id=local_run.workspace_id,
        project_id=local_run.project_id,
        task_id=local_run.task_id,
        repo_id=local_run.repo_id,
        status=local_run.status,
        runner_kind=local_run.runner_kind,
        base_branch=local_run.base_branch,
        base_sha=local_run.base_sha,
        head_sha=local_run.head_sha,
        worktree_path=local_run.worktree_path,
        patch_artifact_id=local_run.patch_artifact_id,
        failure_reason=local_run.failure_reason,
        created_at=local_run.created_at,
        updated_at=local_run.updated_at,
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
