from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    CloudRun,
    LocalTaskRun,
    PatchArtifact,
    Task,
    utc_now,
)
from ai_company_api.schemas.api import (
    CloudRunCreate,
    CloudRunRead,
    CloudRunResultRead,
    PatchArtifactRead,
)
from ai_company_api.services.cloud_sandbox_executor import (
    SandboxExecutionRequest,
    select_cloud_sandbox_executor,
)
from ai_company_api.services.repository import create_task_event, get_repository, get_task
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)


def start_cloud_run(
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

    event_clock = _EventClock()
    executor = select_cloud_sandbox_executor()
    cloud_run = CloudRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        base_branch=repository.default_branch,
        head_branch="",
        status="queued",
        sandbox_kind=executor.sandbox_kind,
    )
    session.add(cloud_run)
    session.flush()

    head_branch = f"ai-scdc/task-{task.id}-{cloud_run.id}"
    cloud_run.head_branch = head_branch
    cloud_run.sandbox_kind = executor.sandbox_kind
    execution_result = executor.run(
        SandboxExecutionRequest(
            task_id=task.id,
            cloud_run_id=cloud_run.id,
            title=task.title,
            description=task.description,
            repo_url=repository.repo_url,
            base_branch=repository.default_branch,
            head_branch=head_branch,
            allowed_paths=task.allowed_paths or [],
            required_tests=task.required_tests or [],
            docker_image=None,
            patch_command=None,
            test_commands=[],
            env={},
            network_enabled=True,
        )
    )

    _create_cloud_run_event(
        session,
        event_clock,
        task.id,
        "cloud_run_started",
        {"cloud_run_id": cloud_run.id, "repo_id": repository.id},
    )

    local_run = LocalTaskRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        status="running",
        runner_kind=execution_result.runner_kind,
        base_branch=repository.default_branch,
        base_sha=execution_result.base_sha,
        head_sha=execution_result.head_sha,
        worktree_path=execution_result.worktree_ref,
    )
    session.add(local_run)
    session.flush()

    cloud_run.status = "running"
    cloud_run.local_run_id = local_run.id
    cloud_run.updated_at = utc_now()
    task.repo_id = repository.id
    task.branch_name = head_branch
    task.worktree_ref = execution_result.worktree_ref
    _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.ASSIGNED)
    _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.IN_PROGRESS)

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

    local_run.status = execution_result.status
    local_run.patch_artifact_id = artifact.id
    local_run.updated_at = utc_now()
    cloud_run.status = execution_result.status
    cloud_run.patch_artifact_id = artifact.id
    cloud_run.command_results = [
        result.as_payload() for result in execution_result.command_results
    ]
    cloud_run.updated_at = utc_now()
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
    _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.PATCH_READY)

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


def list_cloud_runs(session: Session, task_id: str) -> list[CloudRunRead]:
    get_task(session, task_id)
    statement = (
        select(CloudRun)
        .where(CloudRun.task_id == task_id)
        .order_by(CloudRun.created_at, CloudRun.id)
    )
    return [_cloud_run_read(cloud_run) for cloud_run in session.exec(statement).all()]


def get_cloud_run_read(session: Session, cloud_run_id: str) -> CloudRunRead:
    cloud_run = session.get(CloudRun, cloud_run_id)
    if cloud_run is None:
        raise HTTPException(status_code=404, detail="Cloud run not found")
    return _cloud_run_read(cloud_run)


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
        created_at=cloud_run.created_at,
        updated_at=cloud_run.updated_at,
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
