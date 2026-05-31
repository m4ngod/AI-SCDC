from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    PatchApproval,
    PatchArtifact,
    PatchReview,
    Task,
    utc_now,
)
from ai_company_api.schemas.api import (
    PatchApprovalRead,
    PatchApprovalResultRead,
    PatchArtifactRead,
    PatchReviewRead,
    TaskRead,
)
from ai_company_api.services.repository import create_task_event, get_task
from ai_company_api.services.task_state import (
    InvalidTaskTransition,
    TaskStatus,
    allowed_next_statuses,
    validate_transition,
)


def approve_patch_artifact(
    session: Session,
    patch_artifact_id: str,
) -> tuple[PatchApprovalResultRead, int]:
    artifact = _get_patch_artifact_entity(session, patch_artifact_id)
    existing_approval = _existing_patch_approval(session, artifact.id)
    if existing_approval is not None:
        return _approval_result_read(session, existing_approval), 200

    task = get_task(session, artifact.task_id)
    if TaskStatus(task.status) != TaskStatus.APPROVED:
        current_status = TaskStatus(task.status)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be APPROVED before patch approval",
                "current_status": current_status.value,
                "expected_status": TaskStatus.APPROVED.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        )

    review = _latest_review(session, artifact.id)
    if review is None or review.verdict != "approved":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Patch approval requires an approved review",
                "patch_artifact_id": artifact.id,
            },
        )

    event_clock = _EventClock()
    try:
        approval = PatchApproval(
            project_id=task.project_id,
            task_id=task.id,
            local_run_id=artifact.local_run_id,
            patch_artifact_id=artifact.id,
            review_id=review.id,
            status="approved",
            approved_by="dev_user",
            merge_instructions=_merge_instructions(task, artifact),
        )
        session.add(approval)
        session.flush()
        _create_approval_event(
            session,
            event_clock,
            task.id,
            "patch_approval_created",
            {
                "patch_approval_id": approval.id,
                "patch_artifact_id": artifact.id,
                "review_id": review.id,
                "status": approval.status,
            },
        )
        _transition_task_for_patch_approval(
            session,
            event_clock,
            task,
            TaskStatus.MERGE_READY,
        )
        session.add(task)
        session.add(approval)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing_approval = _existing_patch_approval(session, patch_artifact_id)
        if existing_approval is not None:
            return _approval_result_read(session, existing_approval), 200
        raise HTTPException(
            status_code=409,
            detail="Patch approval could not be created because of a uniqueness conflict",
        ) from exc

    session.refresh(approval)
    return _approval_result_read(session, approval), 201


def list_patch_approvals(
    session: Session,
    patch_artifact_id: str,
) -> list[PatchApprovalRead]:
    _get_patch_artifact_entity(session, patch_artifact_id)
    statement = (
        select(PatchApproval)
        .where(PatchApproval.patch_artifact_id == patch_artifact_id)
        .order_by(PatchApproval.created_at, PatchApproval.id)
    )
    return [_approval_read(approval) for approval in session.exec(statement).all()]


def get_patch_approval(session: Session, approval_id: str) -> PatchApprovalRead:
    approval = session.get(PatchApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Patch approval not found")
    return _approval_read(approval)


def request_human_approval(
    session: Session,
    approval_id: str,
) -> PatchApprovalResultRead:
    approval = session.get(PatchApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Patch approval not found")

    task = get_task(session, approval.task_id)
    if TaskStatus(task.status) != TaskStatus.MERGE_READY:
        current_status = TaskStatus(task.status)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be MERGE_READY before human approval can be requested",
                "current_status": current_status.value,
                "expected_status": TaskStatus.MERGE_READY.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        )

    event_clock = _EventClock()
    _create_approval_event(
        session,
        event_clock,
        task.id,
        "human_approval_requested",
        {
            "patch_approval_id": approval.id,
            "patch_artifact_id": approval.patch_artifact_id,
            "review_id": approval.review_id,
        },
    )
    _transition_task_for_patch_approval(
        session,
        event_clock,
        task,
        TaskStatus.HUMAN_APPROVAL,
    )
    session.add(task)
    session.commit()
    session.refresh(approval)
    return _approval_result_read(session, approval)


def _existing_patch_approval(
    session: Session,
    patch_artifact_id: str,
) -> PatchApproval | None:
    statement = (
        select(PatchApproval)
        .where(PatchApproval.patch_artifact_id == patch_artifact_id)
        .order_by(PatchApproval.created_at, PatchApproval.id)
        .limit(1)
    )
    return session.exec(statement).first()


def _latest_review(session: Session, patch_artifact_id: str) -> PatchReview | None:
    statement = (
        select(PatchReview)
        .where(PatchReview.patch_artifact_id == patch_artifact_id)
        .order_by(PatchReview.created_at.desc(), PatchReview.id.desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _approval_result_read(
    session: Session,
    approval: PatchApproval,
) -> PatchApprovalResultRead:
    task = get_task(session, approval.task_id)
    artifact = _get_patch_artifact_entity(session, approval.patch_artifact_id)
    review = _get_patch_review_entity(session, approval.review_id)
    return PatchApprovalResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        review=_review_read(review),
        approval=_approval_read(approval),
    )


def _get_patch_artifact_entity(
    session: Session,
    patch_artifact_id: str,
) -> PatchArtifact:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return artifact


def _get_patch_review_entity(session: Session, review_id: str) -> PatchReview:
    review = session.get(PatchReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Patch review not found")
    return review


def _transition_task_for_patch_approval(
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
    _create_approval_event(
        session,
        event_clock,
        task.id,
        "task_transitioned",
        {"from_status": current_status.value, "to_status": next_status.value},
    )


def _merge_instructions(task: Task, artifact: PatchArtifact) -> str:
    worktree = task.worktree_ref or "the task worktree"
    files = ", ".join(artifact.files_changed or [])
    changed_files = f" Changed files: {files}." if files else ""
    return (
        f"Inspect {worktree} before merging.{changed_files} "
        "This workflow records approval only and does not run git merge, "
        "git commit, git push, git apply, or create a pull request."
    )


class _EventClock:
    def __init__(self) -> None:
        self._base = utc_now()
        self._offset = 0

    def next(self) -> datetime:
        self._offset += 1
        return self._base + timedelta(microseconds=self._offset)


def _create_approval_event(
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
        "patch_approval",
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


def _review_read(review: PatchReview) -> PatchReviewRead:
    return PatchReviewRead(
        id=review.id,
        workspace_id=review.workspace_id,
        project_id=review.project_id,
        task_id=review.task_id,
        local_run_id=review.local_run_id,
        patch_artifact_id=review.patch_artifact_id,
        test_run_id=review.test_run_id,
        reviewer_kind=review.reviewer_kind,
        verdict=review.verdict,
        issues=review.issues,
        required_changes=review.required_changes,
        created_at=review.created_at,
    )


def _approval_read(approval: PatchApproval) -> PatchApprovalRead:
    return PatchApprovalRead(
        id=approval.id,
        workspace_id=approval.workspace_id,
        project_id=approval.project_id,
        task_id=approval.task_id,
        local_run_id=approval.local_run_id,
        patch_artifact_id=approval.patch_artifact_id,
        review_id=approval.review_id,
        status=approval.status,
        approved_by=approval.approved_by,
        merge_instructions=approval.merge_instructions,
        created_at=approval.created_at,
    )
