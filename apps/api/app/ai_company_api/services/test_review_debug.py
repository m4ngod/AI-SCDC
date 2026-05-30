from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    DebugAttempt,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    Task,
    utc_now,
)
from ai_company_api.schemas.api import (
    DebugAttemptRead,
    LocalTestRunRead,
    PatchArtifactRead,
    PatchTestRunResultRead,
    TaskRead,
)
from ai_company_api.services.repository import create_task_event, get_task
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)
from ai_company_worker.test_runner import (
    TestRunnerError,
    TestRunnerRequest,
    run_tests,
)


RUN_TESTS = run_tests


def start_patch_test_run(
    session: Session,
    patch_artifact_id: str,
) -> PatchTestRunResultRead:
    artifact = _get_patch_artifact_entity(session, patch_artifact_id)
    task = get_task(session, artifact.task_id)
    if TaskStatus(task.status) != TaskStatus.PATCH_READY:
        current_status = TaskStatus(task.status)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be PATCH_READY before tests can run",
                "current_status": current_status.value,
                "expected_status": TaskStatus.PATCH_READY.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        )

    local_run = _get_local_run_entity(session, artifact.local_run_id)
    if not local_run.worktree_path:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Local run has no worktree_path",
                "local_run_id": local_run.id,
            },
        )

    event_clock = _EventClock()
    commands = list(task.required_tests or [])
    worktree_path = local_run.worktree_path
    test_run = LocalTestRun(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        status="running",
        commands=commands,
    )
    session.add(test_run)
    session.flush()
    _create_workflow_event(
        session,
        event_clock,
        task.id,
        "test_run_started",
        {"patch_artifact_id": artifact.id, "test_run_id": test_run.id},
    )
    _transition_task_for_workflow(
        session,
        event_clock,
        task,
        TaskStatus.SELF_TESTING,
    )
    task_id = task.id
    test_run_id = test_run.id
    session.add(test_run)
    session.add(task)
    session.commit()

    debug_attempt: DebugAttempt | None = None
    try:
        result = RUN_TESTS(
            TestRunnerRequest(
                worktree_path=Path(worktree_path),
                commands=commands,
            )
        )
    except TestRunnerError as exc:
        task = get_task(session, task_id)
        artifact = _get_patch_artifact_entity(session, patch_artifact_id)
        test_run = _get_test_run_entity(session, test_run_id)
        _complete_test_run(
            test_run,
            artifact,
            status="failed",
            command_results=[],
            failure_reason=str(exc),
        )
        _create_workflow_event(
            session,
            event_clock,
            task.id,
            "test_run_completed",
            {
                "patch_artifact_id": artifact.id,
                "test_run_id": test_run.id,
                "status": test_run.status,
                "failure_reason": test_run.failure_reason,
            },
        )
        debug_attempt = _create_debug_attempt(
            session,
            event_clock,
            task,
            artifact,
            test_run,
            root_cause=f"Test command failed: {exc}",
        )
        _transition_task_for_workflow(
            session,
            event_clock,
            task,
            TaskStatus.FIX_REQUESTED,
        )
    else:
        task = get_task(session, task_id)
        artifact = _get_patch_artifact_entity(session, patch_artifact_id)
        test_run = _get_test_run_entity(session, test_run_id)
        command_results = [
            command_result.model_dump() for command_result in result.command_results
        ]
        failure_reason = _failure_reason(command_results) if result.status == "failed" else None
        _complete_test_run(
            test_run,
            artifact,
            status=result.status,
            command_results=command_results,
            failure_reason=failure_reason,
        )
        _create_workflow_event(
            session,
            event_clock,
            task.id,
            "test_run_completed",
            {
                "patch_artifact_id": artifact.id,
                "test_run_id": test_run.id,
                "status": test_run.status,
            },
        )
        if result.status == "passed":
            _transition_task_for_workflow(
                session,
                event_clock,
                task,
                TaskStatus.REVIEWING,
            )
        else:
            debug_attempt = _create_debug_attempt(
                session,
                event_clock,
                task,
                artifact,
                test_run,
                root_cause=failure_reason or "Test command failed.",
            )
            _transition_task_for_workflow(
                session,
                event_clock,
                task,
                TaskStatus.FIX_REQUESTED,
            )

    session.add(test_run)
    session.add(artifact)
    session.add(task)
    session.commit()
    session.refresh(task)
    session.refresh(artifact)
    session.refresh(test_run)
    if debug_attempt is not None:
        session.refresh(debug_attempt)

    return PatchTestRunResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        test_run=_test_run_read(test_run),
        debug_attempt=_debug_attempt_read(debug_attempt) if debug_attempt else None,
    )


def list_patch_test_runs(
    session: Session,
    patch_artifact_id: str,
) -> list[LocalTestRunRead]:
    _get_patch_artifact_entity(session, patch_artifact_id)
    statement = (
        select(LocalTestRun)
        .where(LocalTestRun.patch_artifact_id == patch_artifact_id)
        .order_by(LocalTestRun.created_at, LocalTestRun.id)
    )
    return [_test_run_read(test_run) for test_run in session.exec(statement).all()]


def get_test_run(session: Session, test_run_id: str) -> LocalTestRunRead:
    test_run = session.get(LocalTestRun, test_run_id)
    if test_run is None:
        raise HTTPException(status_code=404, detail="Local test run not found")
    return _test_run_read(test_run)


def list_debug_attempts(session: Session, task_id: str) -> list[DebugAttemptRead]:
    get_task(session, task_id)
    statement = (
        select(DebugAttempt)
        .where(DebugAttempt.task_id == task_id)
        .order_by(DebugAttempt.created_at, DebugAttempt.id)
    )
    return [_debug_attempt_read(debug_attempt) for debug_attempt in session.exec(statement).all()]


def _get_patch_artifact_entity(
    session: Session,
    patch_artifact_id: str,
) -> PatchArtifact:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return artifact


def _get_local_run_entity(session: Session, local_run_id: str) -> LocalTaskRun:
    local_run = session.get(LocalTaskRun, local_run_id)
    if local_run is None:
        raise HTTPException(status_code=404, detail="Local task run not found")
    return local_run


def _get_test_run_entity(session: Session, test_run_id: str) -> LocalTestRun:
    test_run = session.get(LocalTestRun, test_run_id)
    if test_run is None:
        raise HTTPException(status_code=404, detail="Local test run not found")
    return test_run


def _complete_test_run(
    test_run: LocalTestRun,
    artifact: PatchArtifact,
    *,
    status: str,
    command_results: list[dict],
    failure_reason: str | None,
) -> None:
    now = utc_now()
    test_run.status = status
    test_run.command_results = command_results
    test_run.failure_reason = failure_reason
    test_run.completed_at = now
    artifact.tests_run = list(test_run.commands)
    artifact.test_result = "passed" if status == "passed" else "failed"


def _failure_reason(command_results: list[dict]) -> str:
    for command_result in command_results:
        if command_result.get("exit_code") != 0:
            command = command_result.get("command", "")
            stderr = str(command_result.get("stderr") or "").strip()
            stdout = str(command_result.get("stdout") or "").strip()
            detail = stderr or stdout
            if detail:
                return f"Test command failed: {command}: {detail}"
            return f"Test command failed: {command}"
    return "Test command failed."


def _create_debug_attempt(
    session: Session,
    event_clock: "_EventClock",
    task: Task,
    artifact: PatchArtifact,
    test_run: LocalTestRun,
    *,
    root_cause: str,
) -> DebugAttempt:
    debug_attempt = DebugAttempt(
        project_id=task.project_id,
        task_id=task.id,
        patch_artifact_id=artifact.id,
        test_run_id=test_run.id,
        root_cause=root_cause,
        fix_summary="Fix the failing test command output, then rerun local tests.",
    )
    session.add(debug_attempt)
    session.flush()
    _create_workflow_event(
        session,
        event_clock,
        task.id,
        "debug_attempt_created",
        {
            "patch_artifact_id": artifact.id,
            "test_run_id": test_run.id,
            "debug_attempt_id": debug_attempt.id,
        },
    )
    return debug_attempt


def _transition_task_for_workflow(
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
    _create_workflow_event(
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


def _create_workflow_event(
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
        "test_runner",
        payload,
    )
    event.created_at = event_clock.next()


def _task_read(task: Task) -> TaskRead:
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        conversation_id=task.conversation_id,
        parent_task_id=task.parent_task_id,
        title=task.title,
        description=task.description,
        role_required=task.role_required,
        status=TaskStatus(task.status),
        priority=task.priority,
        risk_level=task.risk_level,
        acceptance_criteria=task.acceptance_criteria,
        allowed_paths=task.allowed_paths,
        required_tests=task.required_tests,
        assigned_agent_profile_id=task.assigned_agent_profile_id,
        repo_id=task.repo_id,
        branch_name=task.branch_name,
        worktree_ref=task.worktree_ref,
        budget_limit=task.budget_limit,
        created_at=task.created_at,
        updated_at=task.updated_at,
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


def _test_run_read(test_run: LocalTestRun) -> LocalTestRunRead:
    return LocalTestRunRead(
        id=test_run.id,
        workspace_id=test_run.workspace_id,
        project_id=test_run.project_id,
        task_id=test_run.task_id,
        local_run_id=test_run.local_run_id,
        patch_artifact_id=test_run.patch_artifact_id,
        status=test_run.status,
        commands=test_run.commands,
        command_results=test_run.command_results,
        failure_reason=test_run.failure_reason,
        started_at=test_run.started_at,
        completed_at=test_run.completed_at,
        created_at=test_run.created_at,
    )


def _debug_attempt_read(debug_attempt: DebugAttempt) -> DebugAttemptRead:
    return DebugAttemptRead(
        id=debug_attempt.id,
        workspace_id=debug_attempt.workspace_id,
        project_id=debug_attempt.project_id,
        task_id=debug_attempt.task_id,
        patch_artifact_id=debug_attempt.patch_artifact_id,
        review_id=debug_attempt.review_id,
        test_run_id=debug_attempt.test_run_id,
        status=debug_attempt.status,
        root_cause=debug_attempt.root_cause,
        fix_summary=debug_attempt.fix_summary,
        created_at=debug_attempt.created_at,
    )
