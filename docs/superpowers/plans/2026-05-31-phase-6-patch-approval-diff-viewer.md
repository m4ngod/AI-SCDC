# Phase 6 Patch Approval and Diff Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human patch approval boundary and compact unified diff viewer without running git merge, commit, push, apply, or PR creation.

**Architecture:** Keep Phase 6 as an API-and-desktop workflow layer on top of Phase 5 patch artifacts and reviews. The API owns durable `PatchApproval` records, state transitions to `MERGE_READY` and `HUMAN_APPROVAL`, and idempotent approval behavior. The desktop consumes compact result objects and displays diff, test, review, approval, worktree, and merge instruction context inside the existing task board.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, pytest, React 19, TypeScript, Vite, Vitest, Testing Library.

---

## File Structure

- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Add `PatchApproval` table with a unique `patch_artifact_id`.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Add `PatchApprovalRead` and `PatchApprovalResultRead`.
- Create: `apps/api/app/ai_company_api/services/patch_approval.py`
  - Own approval creation, idempotency, `APPROVED -> MERGE_READY`, and `MERGE_READY -> HUMAN_APPROVAL`.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Add patch approval endpoints.
- Create: `apps/api/tests/test_patch_approval_api.py`
  - Cover persistence, approval success, duplicate approval, invalid states, missing approved review, and human approval request.
- Modify: `apps/desktop/src/api/client.ts`
  - Add approval types and methods.
- Modify: `apps/desktop/src/test/client.test.ts`
  - Cover fake and HTTP client approval behavior.
- Modify: `apps/desktop/src/App.tsx`
  - Add handlers and pending state for patch approval and human approval request.
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
  - Render diff preview, approval controls, approval status, worktree, and merge instructions.
- Modify: `apps/desktop/src/styles/app.css`
  - Add compact diff viewer styling.
- Modify: `apps/desktop/src/fixtures/demoData.ts`
  - Add a demo approved patch ready for approval.
- Modify: `apps/desktop/src/test/App.test.tsx`
  - Cover diff preview, approve patch, request human approval, and merge instructions.
- Modify: `docs/architecture.md`
  - Add Phase 6 boundary and move it to Completed.
- Modify: `README.md`
  - Add Phase 6 local smoke notes.

---

## Task 1: Backend Patch Approval API

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/patch_approval.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/tests/test_patch_approval_api.py`

- [ ] **Step 1: Write failing backend tests**

Create `apps/api/tests/test_patch_approval_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    LocalTaskRun,
    LocalTestRun,
    PatchApproval,
    PatchArtifact,
    PatchReview,
    Project,
    Repository,
    Task,
)
from ai_company_api.services.task_state import TaskStatus


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def count_events(events: list[dict], event_type: str) -> int:
    return sum(1 for event in events if event["event_type"] == event_type)


def create_reviewed_patch(
    session: Session,
    *,
    task_status: TaskStatus = TaskStatus.APPROVED,
    review_verdict: str = "approved",
) -> tuple[Project, Task, LocalTaskRun, PatchArtifact, PatchReview]:
    project = Project(name="Demo")
    session.add(project)
    session.flush()
    repository = Repository(
        project_id=project.id,
        name="Demo repo",
        local_path=".",
        default_branch="main",
    )
    session.add(repository)
    session.flush()
    task = Task(
        project_id=project.id,
        title="Approve reviewed patch",
        role_required="backend",
        status=task_status,
        allowed_paths=["README.md"],
        required_tests=["python -V"],
        repo_id=repository.id,
        branch_name="main",
        worktree_ref=".worktrees/task-local_run",
    )
    session.add(task)
    session.flush()
    local_run = LocalTaskRun(
        project_id=project.id,
        task_id=task.id,
        repo_id=repository.id,
        status="patch_ready",
        base_branch="main",
        worktree_path=".worktrees/task-local_run",
    )
    session.add(local_run)
    session.flush()
    artifact = PatchArtifact(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        summary="Prepared patch.",
        files_changed=["README.md"],
        tests_run=["python -V"],
        test_result="passed",
        risks=[],
        diff_text="diff --git a/README.md b/README.md",
    )
    session.add(artifact)
    session.flush()
    test_run = LocalTestRun(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        status="passed",
        commands=["python -V"],
        command_results=[
            {
                "command": "python -V",
                "exit_code": 0,
                "stdout": "Python",
                "stderr": "",
                "duration_ms": 1,
            }
        ],
    )
    session.add(test_run)
    session.flush()
    review = PatchReview(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        test_run_id=test_run.id,
        reviewer_kind="deterministic",
        verdict=review_verdict,
        issues=[] if review_verdict == "approved" else [{"code": "needs_changes"}],
        required_changes=[] if review_verdict == "approved" else ["Fix review issue."],
    )
    session.add(review)
    session.commit()
    session.refresh(project)
    session.refresh(task)
    session.refresh(local_run)
    session.refresh(artifact)
    session.refresh(review)
    return project, task, local_run, artifact, review


def test_patch_approval_record_persists() -> None:
    with build_session() as session:
        _project, task, local_run, artifact, review = create_reviewed_patch(session)
        approval = PatchApproval(
            project_id=task.project_id,
            task_id=task.id,
            local_run_id=local_run.id,
            patch_artifact_id=artifact.id,
            review_id=review.id,
            status="approved",
            approved_by="dev_user",
            merge_instructions="Inspect the worktree before merging.",
        )
        session.add(approval)
        session.commit()

        persisted = session.get(PatchApproval, approval.id)

    assert persisted is not None
    assert persisted.patch_artifact_id == artifact.id
    assert persisted.review_id == review.id
    assert persisted.status == "approved"
    assert persisted.merge_instructions == "Inspect the worktree before merging."


def test_approved_reviewed_patch_can_be_approved(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, task, _local_run, artifact, review = create_reviewed_patch(session)

    response = client.post(f"/patch-artifacts/{artifact.id}/approvals")

    assert response.status_code == 201
    result = response.json()
    assert result["task"]["status"] == "MERGE_READY"
    assert result["approval"]["patch_artifact_id"] == artifact.id
    assert result["approval"]["review_id"] == review.id
    assert result["approval"]["status"] == "approved"
    assert "does not run git merge" in result["approval"]["merge_instructions"]
    events = client.get(f"/tasks/{task.id}/events").json()
    assert count_events(events, "patch_approval_created") == 1
    assert count_events(events, "task_transitioned") == 1


def test_patch_approval_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, task, _local_run, artifact, _review = create_reviewed_patch(session)

    first = client.post(f"/patch-artifacts/{artifact.id}/approvals")
    second = client.post(f"/patch-artifacts/{artifact.id}/approvals")
    list_response = client.get(f"/patch-artifacts/{artifact.id}/approvals")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["approval"]["id"] == first.json()["approval"]["id"]
    assert [item["id"] for item in list_response.json()] == [first.json()["approval"]["id"]]
    events = client.get(f"/tasks/{task.id}/events").json()
    assert count_events(events, "patch_approval_created") == 1


def test_patch_approval_requires_approved_task(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, _task, _local_run, artifact, _review = create_reviewed_patch(
            session,
            task_status=TaskStatus.REVIEWING,
        )

    response = client.post(f"/patch-artifacts/{artifact.id}/approvals")

    assert response.status_code == 400
    assert response.json()["detail"]["current_status"] == "REVIEWING"
    assert response.json()["detail"]["expected_status"] == "APPROVED"


def test_patch_approval_requires_approved_review(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, _task, _local_run, artifact, _review = create_reviewed_patch(
            session,
            review_verdict="changes_requested",
        )

    response = client.post(f"/patch-artifacts/{artifact.id}/approvals")

    assert response.status_code == 400
    assert "approved review" in response.json()["detail"]["message"]


def test_patch_approval_can_request_human_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, task, _local_run, artifact, _review = create_reviewed_patch(session)

    approval_response = client.post(f"/patch-artifacts/{artifact.id}/approvals")
    approval_id = approval_response.json()["approval"]["id"]
    response = client.post(f"/patch-approvals/{approval_id}/request-human-approval")

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "HUMAN_APPROVAL"
    events = client.get(f"/tasks/{task.id}/events").json()
    assert count_events(events, "human_approval_requested") == 1


def test_request_human_approval_requires_merge_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, _task, local_run, artifact, review = create_reviewed_patch(session)
        approval = PatchApproval(
            project_id=artifact.project_id,
            task_id=artifact.task_id,
            local_run_id=local_run.id,
            patch_artifact_id=artifact.id,
            review_id=review.id,
            status="approved",
            approved_by="dev_user",
            merge_instructions="Inspect the worktree before merging.",
        )
        session.add(approval)
        session.commit()
        session.refresh(approval)

    response = client.post(f"/patch-approvals/{approval.id}/request-human-approval")

    assert response.status_code == 400
    assert response.json()["detail"]["expected_status"] == "MERGE_READY"
```

- [ ] **Step 2: Run backend tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_patch_approval_api.py -v
```

Expected: FAIL with an import error for `PatchApproval` or route 404 errors, because the model and endpoints do not exist yet.

- [ ] **Step 3: Add PatchApproval entity**

In `apps/api/app/ai_company_api/models/entities.py`, add this class after `PatchReview`:

```python
class PatchApproval(SQLModel, table=True):
    __tablename__ = "patch_approval"
    __table_args__ = (
        UniqueConstraint(
            "patch_artifact_id",
            name="uq_patch_approval_patch_artifact_id",
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("patch_approval"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    review_id: str = Field(index=True, foreign_key="patch_review.id")
    status: str = Field(default="approved", index=True)
    approved_by: str = "dev_user"
    merge_instructions: str
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] **Step 4: Add API schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add after `PatchReviewResultRead`:

```python
class PatchApprovalRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    patch_artifact_id: str
    review_id: str
    status: str
    approved_by: str
    merge_instructions: str
    created_at: datetime


class PatchApprovalResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    review: PatchReviewRead
    approval: PatchApprovalRead
```

- [ ] **Step 5: Implement patch approval service**

Create `apps/api/app/ai_company_api/services/patch_approval.py`:

```python
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
            "status": approval.status,
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


def _get_patch_artifact_entity(
    session: Session,
    patch_artifact_id: str,
) -> PatchArtifact:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return artifact


def _get_review_entity(session: Session, review_id: str) -> PatchReview:
    review = session.get(PatchReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Patch review not found")
    return review


def _merge_instructions(task: Task, artifact: PatchArtifact) -> str:
    files = ", ".join(artifact.files_changed or [])
    worktree = task.worktree_ref or "the local runner worktree"
    return (
        f"Patch approved for review. Inspect {worktree} and files [{files}] before any "
        "manual merge. This workflow records approval only and does not run git merge, "
        "commit, push, apply, or PR creation."
    )


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
        "user",
        "dev_user",
        payload,
    )
    event.created_at = event_clock.next()


def _approval_result_read(
    session: Session,
    approval: PatchApproval,
) -> PatchApprovalResultRead:
    task = get_task(session, approval.task_id)
    artifact = _get_patch_artifact_entity(session, approval.patch_artifact_id)
    review = _get_review_entity(session, approval.review_id)
    return PatchApprovalResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        review=_review_read(review),
        approval=_approval_read(approval),
    )


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
```

- [ ] **Step 6: Add API routes**

In `apps/api/app/ai_company_api/api/routes.py`, import schemas and service functions:

```python
from fastapi import APIRouter, Depends, Response, status
```

Add to the schema imports:

```python
    PatchApprovalRead,
    PatchApprovalResultRead,
```

Add service imports:

```python
from ai_company_api.services.patch_approval import (
    approve_patch_artifact,
    get_patch_approval,
    list_patch_approvals,
    request_human_approval,
)
```

Add routes after patch review routes:

```python
@router.post(
    "/patch-artifacts/{patch_artifact_id}/approvals",
    response_model=PatchApprovalResultRead,
)
def post_patch_artifact_approval(
    patch_artifact_id: str,
    response: Response,
    session: SessionDep,
) -> PatchApprovalResultRead:
    result, status_code = approve_patch_artifact(session, patch_artifact_id)
    response.status_code = status_code
    return result


@router.get(
    "/patch-artifacts/{patch_artifact_id}/approvals",
    response_model=list[PatchApprovalRead],
)
def get_patch_artifact_approvals(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[PatchApprovalRead]:
    return list_patch_approvals(session, patch_artifact_id)


@router.get("/patch-approvals/{approval_id}", response_model=PatchApprovalRead)
def get_patch_approval_by_id(
    approval_id: str,
    session: SessionDep,
) -> PatchApprovalRead:
    return get_patch_approval(session, approval_id)


@router.post(
    "/patch-approvals/{approval_id}/request-human-approval",
    response_model=PatchApprovalResultRead,
)
def post_patch_approval_human_request(
    approval_id: str,
    session: SessionDep,
) -> PatchApprovalResultRead:
    return request_human_approval(session, approval_id)
```

- [ ] **Step 7: Run backend tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_patch_approval_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Run related backend suite**

Run:

```powershell
pytest apps/api/tests/test_patch_approval_api.py apps/api/tests/test_test_review_debug_api.py apps/api/tests/test_task_state.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit backend API work**

Run:

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/patch_approval.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_patch_approval_api.py
git commit -m "feat(api): add patch approval workflow"
```

---

## Task 2: Desktop Client Contract

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/test/client.test.ts`

- [ ] **Step 1: Write failing client tests**

In `apps/desktop/src/test/client.test.ts`, add tests before the closing `});`:

```typescript
  it("fake client approves a reviewed patch", async () => {
    await expect(fakeApiClient.approvePatch("patch_demo")).resolves.toMatchObject({
      task: {
        id: "task_board_ui",
        status: "MERGE_READY"
      },
      approval: {
        patch_artifact_id: "patch_demo",
        status: "approved",
        approved_by: "dev_user"
      }
    });
  });

  it("fake client requests human approval for an approved patch", async () => {
    const approval = await fakeApiClient.approvePatch("patch_demo");

    await expect(
      fakeApiClient.requestHumanApproval(approval.approval.id)
    ).resolves.toMatchObject({
      task: {
        id: "task_board_ui",
        status: "HUMAN_APPROVAL"
      },
      approval: {
        id: approval.approval.id,
        status: "approved"
      }
    });
  });

  it("HTTP client posts patch approvals and maps result cards", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse(
        {
          task: {
            id: "task_api",
            title: "Approve patch",
            status: "MERGE_READY",
            role_required: "backend",
            worktree_ref: "T:/repo/.worktrees/task_api-local_run_api",
            updated_at: "2026-05-29T03:00:00Z"
          },
          patch_artifact: {
            id: "patch_api",
            task_id: "task_api",
            local_run_id: "local_run_api",
            summary: "Prepared local runner patch.",
            files_changed: ["README.md"],
            tests_run: ["python -V"],
            test_result: "passed",
            risks: [],
            diff_text: "diff --git a/README.md b/README.md",
            created_at: "2026-05-29T02:00:00Z"
          },
          review: {
            id: "review_api",
            task_id: "task_api",
            local_run_id: "local_run_api",
            patch_artifact_id: "patch_api",
            test_run_id: "test_run_api",
            reviewer_kind: "deterministic",
            verdict: "approved",
            issues: [],
            required_changes: [],
            created_at: "2026-05-29T02:05:00Z"
          },
          approval: {
            id: "patch_approval_api",
            task_id: "task_api",
            local_run_id: "local_run_api",
            patch_artifact_id: "patch_api",
            review_id: "review_api",
            status: "approved",
            approved_by: "dev_user",
            merge_instructions: "Inspect the worktree before merging. This workflow does not run git merge.",
            created_at: "2026-05-29T03:00:00Z"
          }
        },
        { status: 201 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const result = await client.approvePatch("patch_api");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/patch-artifacts/patch_api/approvals",
      expect.objectContaining({ method: "POST" })
    );
    expect(result).toMatchObject({
      task: {
        id: "task_api",
        status: "MERGE_READY",
        assigned_agent: "Backend Engineer",
        worktree_ref: "T:/repo/.worktrees/task_api-local_run_api"
      },
      patch_artifact: {
        id: "patch_api",
        diff_text: "diff --git a/README.md b/README.md"
      },
      review: {
        verdict: "approved"
      },
      approval: {
        id: "patch_approval_api",
        merge_instructions: "Inspect the worktree before merging. This workflow does not run git merge."
      }
    });
  });

  it("HTTP client requests human approval for a patch approval", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
      jsonResponse({
        task: {
          id: "task_api",
          title: "Approve patch",
          status: "HUMAN_APPROVAL",
          role_required: "backend",
          updated_at: "2026-05-29T03:05:00Z"
        },
        patch_artifact: {
          id: "patch_api",
          task_id: "task_api",
          local_run_id: "local_run_api",
          summary: "Prepared local runner patch.",
          files_changed: ["README.md"],
          tests_run: ["python -V"],
          test_result: "passed",
          risks: [],
          diff_text: "diff --git a/README.md b/README.md",
          created_at: "2026-05-29T02:00:00Z"
        },
        review: {
          id: "review_api",
          task_id: "task_api",
          local_run_id: "local_run_api",
          patch_artifact_id: "patch_api",
          test_run_id: "test_run_api",
          reviewer_kind: "deterministic",
          verdict: "approved",
          issues: [],
          required_changes: [],
          created_at: "2026-05-29T02:05:00Z"
        },
        approval: {
          id: "patch_approval_api",
          task_id: "task_api",
          local_run_id: "local_run_api",
          patch_artifact_id: "patch_api",
          review_id: "review_api",
          status: "approved",
          approved_by: "dev_user",
          merge_instructions: "Inspect the worktree before merging.",
          created_at: "2026-05-29T03:00:00Z"
        }
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const client = createHttpApiClient({
      baseUrl: "http://127.0.0.1:8000/",
      projectId: "project_demo"
    });
    const result = await client.requestHumanApproval("patch_approval_api");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/patch-approvals/patch_approval_api/request-human-approval",
      expect.objectContaining({ method: "POST" })
    );
    expect(result.task.status).toBe("HUMAN_APPROVAL");
  });
```

- [ ] **Step 2: Run client tests to verify RED**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: FAIL because `approvePatch` and `requestHumanApproval` are not defined on `ConsoleApiClient`.

- [ ] **Step 3: Add client types**

In `apps/desktop/src/api/client.ts`, extend `TaskCard`:

```typescript
  patch_approval?: PatchApprovalCard;
```

Extend `PatchArtifactCard`:

```typescript
  diff_text?: string;
```

Add approval types after `PatchReviewCard`:

```typescript
export type PatchApprovalCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  local_run_id: string;
  patch_artifact_id: string;
  review_id: string;
  status: string;
  approved_by: string;
  merge_instructions: string;
  created_at: string;
};

export type PatchApprovalResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  review: PatchReviewCard;
  approval: PatchApprovalCard;
};
```

Extend `ConsoleApiClient`:

```typescript
  approvePatch: (patchArtifactId: string) => Promise<PatchApprovalResult>;
  requestHumanApproval: (approvalId: string) => Promise<PatchApprovalResult>;
```

Add API aliases:

```typescript
type ApiPatchApproval = PatchApprovalCard;

type ApiPatchApprovalResult = {
  task: ApiTask;
  patch_artifact: ApiPatchArtifact;
  review: ApiPatchReview;
  approval: ApiPatchApproval;
};
```

- [ ] **Step 4: Preserve diff text in patch artifact mapping**

Update `mapPatchArtifactCard()`:

```typescript
function mapPatchArtifactCard(artifact: ApiPatchArtifact): PatchArtifactCard {
  return {
    id: artifact.id,
    task_id: artifact.task_id,
    local_run_id: artifact.local_run_id,
    summary: artifact.summary,
    files_changed: artifact.files_changed,
    tests_run: artifact.tests_run,
    test_result: artifact.test_result,
    diff_text: artifact.diff_text
  };
}
```

Add mapping helpers:

```typescript
function mapPatchApprovalCard(approval: ApiPatchApproval): PatchApprovalCard {
  return {
    id: approval.id,
    workspace_id: approval.workspace_id,
    project_id: approval.project_id,
    task_id: approval.task_id,
    local_run_id: approval.local_run_id,
    patch_artifact_id: approval.patch_artifact_id,
    review_id: approval.review_id,
    status: approval.status,
    approved_by: approval.approved_by,
    merge_instructions: approval.merge_instructions,
    created_at: approval.created_at
  };
}

function mapPatchApprovalResult(result: ApiPatchApprovalResult): PatchApprovalResult {
  return {
    task: mapTaskCard(result.task),
    patch_artifact: mapPatchArtifactCard(result.patch_artifact),
    review: mapPatchReviewCard(result.review),
    approval: mapPatchApprovalCard(result.approval)
  };
}
```

- [ ] **Step 5: Implement fake client approval methods**

In `fakeApiClient`, add methods after `reviewPatch`:

```typescript
  async approvePatch(patchArtifactId: string) {
    const { task, patchArtifact: basePatchArtifact } = fakeTaskFromPatchArtifact(patchArtifactId);
    const patchArtifact = {
      ...basePatchArtifact,
      test_result: "passed",
      diff_text: basePatchArtifact.diff_text ?? "diff --git a/README.md b/README.md"
    };
    const review: PatchReviewCard = {
      id: "review_demo",
      workspace_id: "workspace_demo",
      project_id: "project_demo",
      task_id: task.id,
      local_run_id: patchArtifact.local_run_id,
      patch_artifact_id: patchArtifact.id,
      test_run_id: task.test_run?.id ?? "test_run_demo",
      reviewer_kind: "deterministic",
      verdict: "approved",
      issues: [],
      required_changes: [],
      created_at: "2026-05-29T00:03:00Z"
    };
    const approval: PatchApprovalCard = {
      id: `patch_approval_${patchArtifact.id}`,
      workspace_id: "workspace_demo",
      project_id: "project_demo",
      task_id: task.id,
      local_run_id: patchArtifact.local_run_id,
      patch_artifact_id: patchArtifact.id,
      review_id: review.id,
      status: "approved",
      approved_by: "dev_user",
      merge_instructions: "Inspect the worktree before merging. This workflow does not run git merge.",
      created_at: "2026-05-29T00:04:00Z"
    };
    return {
      task: {
        ...task,
        status: "MERGE_READY",
        patch_artifact: patchArtifact,
        patch_review: review,
        patch_approval: approval
      },
      patch_artifact: patchArtifact,
      review,
      approval
    };
  },
  async requestHumanApproval(approvalId: string) {
    const patchArtifactId = approvalId.replace(/^patch_approval_/, "");
    const result = await this.approvePatch(patchArtifactId);
    return {
      ...result,
      task: {
        ...result.task,
        status: "HUMAN_APPROVAL"
      }
    };
  }
```

- [ ] **Step 6: Implement HTTP client methods**

In `createHttpApiClient()` return object, add:

```typescript
    async approvePatch(patchArtifactId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-artifacts/${patchArtifactId}/approvals`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPatchApprovalResult>(
        response,
        `POST /patch-artifacts/${patchArtifactId}/approvals`
      );
      return mapPatchApprovalResult(result);
    },
    async requestHumanApproval(approvalId: string) {
      const response = await fetch(
        apiUrl(options.baseUrl, `/patch-approvals/${approvalId}/request-human-approval`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" }
        }
      );
      const result = await readJsonResponse<ApiPatchApprovalResult>(
        response,
        `POST /patch-approvals/${approvalId}/request-human-approval`
      );
      return mapPatchApprovalResult(result);
    }
```

- [ ] **Step 7: Run client tests to verify GREEN**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: PASS.

- [ ] **Step 8: Commit client contract work**

Run:

```powershell
git add apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "feat(desktop): add patch approval client contract"
```

---

## Task 3: Desktop Diff Viewer and Approval Controls

**Files:**
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
- Modify: `apps/desktop/src/styles/app.css`
- Modify: `apps/desktop/src/fixtures/demoData.ts`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Write failing App tests**

In `apps/desktop/src/test/App.test.tsx`, update `patchReadyTaskFixture()` patch artifact to include:

```typescript
      diff_text: "diff --git a/apps/desktop/src/components/TaskBoard.tsx b/apps/desktop/src/components/TaskBoard.tsx\n+Approve patch"
```

Add helper after `reviewingTaskFixture()`:

```typescript
function approvedTaskFixture(): TaskCard {
  return {
    ...reviewingTaskFixture(),
    status: "APPROVED",
    patch_review: {
      id: "review_test",
      workspace_id: "workspace_test",
      project_id: "project_demo",
      task_id: "task_patch_ready",
      local_run_id: "local_run_test",
      patch_artifact_id: "patch_test",
      test_run_id: "test_run_test",
      reviewer_kind: "deterministic",
      verdict: "approved",
      issues: [],
      required_changes: [],
      created_at: "2026-05-29T00:03:00Z"
    }
  };
}


function mergeReadyTaskFixture(): TaskCard {
  return {
    ...approvedTaskFixture(),
    status: "MERGE_READY",
    patch_approval: {
      id: "patch_approval_test",
      workspace_id: "workspace_test",
      project_id: "project_demo",
      task_id: "task_patch_ready",
      local_run_id: "local_run_test",
      patch_artifact_id: "patch_test",
      review_id: "review_test",
      status: "approved",
      approved_by: "dev_user",
      merge_instructions: "Inspect .worktrees/task_patch_ready before merging. This workflow does not run git merge.",
      created_at: "2026-05-29T00:04:00Z"
    }
  };
}
```

Extend `createMockApiClient()` defaults:

```typescript
    approvePatch: vi.fn().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        patch_approval: undefined
      },
      patch_artifact: mergeReadyTaskFixture().patch_artifact!,
      review: mergeReadyTaskFixture().patch_review!,
      approval: mergeReadyTaskFixture().patch_approval!
    }),
    requestHumanApproval: vi.fn().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        status: "HUMAN_APPROVAL",
        patch_approval: undefined
      },
      patch_artifact: mergeReadyTaskFixture().patch_artifact!,
      review: mergeReadyTaskFixture().patch_review!,
      approval: mergeReadyTaskFixture().patch_approval!
    }),
```

Add tests before the closing `});`:

```typescript
  it("renders unified diff preview for patch artifacts", async () => {
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([approvedTaskFixture()])
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    expect(await within(board).findByText("Diff preview")).toBeInTheDocument();
    expect(
      within(board).getByText(/diff --git a\/apps\/desktop\/src\/components\/TaskBoard.tsx/)
    ).toBeInTheDocument();
  });

  it("approves an approved patch and renders merge instructions", async () => {
    const user = userEvent.setup();
    const approvePatch = vi.fn<ConsoleApiClient["approvePatch"]>().mockResolvedValue({
      task: {
        ...mergeReadyTaskFixture(),
        patch_approval: undefined
      },
      patch_artifact: mergeReadyTaskFixture().patch_artifact!,
      review: mergeReadyTaskFixture().patch_review!,
      approval: mergeReadyTaskFixture().patch_approval!
    });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([approvedTaskFixture()]),
      approvePatch
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(await within(board).findByRole("button", { name: "Approve patch" }));

    expect(approvePatch).toHaveBeenCalledWith("patch_test");
    expect(await within(board).findByText("MERGE_READY")).toBeInTheDocument();
    expect(within(board).getByText("Patch approval")).toBeInTheDocument();
    expect(within(board).getByText("approved by dev_user")).toBeInTheDocument();
    expect(
      within(board).getByText(/This workflow does not run git merge/)
    ).toBeInTheDocument();
  });

  it("requests human approval from a merge-ready task", async () => {
    const user = userEvent.setup();
    const requestHumanApproval =
      vi.fn<ConsoleApiClient["requestHumanApproval"]>().mockResolvedValue({
        task: {
          ...mergeReadyTaskFixture(),
          status: "HUMAN_APPROVAL",
          patch_approval: undefined
        },
        patch_artifact: mergeReadyTaskFixture().patch_artifact!,
        review: mergeReadyTaskFixture().patch_review!,
        approval: mergeReadyTaskFixture().patch_approval!
      });
    const apiClient = createMockApiClient({
      listTasks: vi.fn().mockResolvedValue([mergeReadyTaskFixture()]),
      requestHumanApproval
    });

    render(<App apiClient={apiClient} />);

    const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
    const board = within(contextPanel).getByLabelText("Task board");
    await user.click(
      await within(board).findByRole("button", { name: "Request human approval" })
    );

    expect(requestHumanApproval).toHaveBeenCalledWith("patch_approval_test");
    expect(await within(board).findByText("HUMAN_APPROVAL")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run App tests to verify RED**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/App.test.tsx
```

Expected: FAIL because approval handlers, buttons, and diff preview are not implemented.

- [ ] **Step 3: Add App state and handlers**

In `apps/desktop/src/App.tsx`, add state:

```typescript
  const [approvingPatchTaskId, setApprovingPatchTaskId] = useState<string | null>(null);
  const [requestingHumanApprovalTaskId, setRequestingHumanApprovalTaskId] =
    useState<string | null>(null);
```

Add handlers after `handleReviewPatch()`:

```typescript
  async function handleApprovePatch(task: TaskCard) {
    if (approvingPatchTaskId || !task.patch_artifact) {
      return;
    }

    setApprovingPatchTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.approvePatch(task.patch_artifact.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...mergeWorkflowTask(currentTask, result.task),
                patch_artifact: result.patch_artifact,
                patch_review: result.review,
                patch_approval: result.approval
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to approve patch")
      }));
    } finally {
      setApprovingPatchTaskId(null);
    }
  }

  async function handleRequestHumanApproval(task: TaskCard) {
    if (requestingHumanApprovalTaskId || !task.patch_approval) {
      return;
    }

    setRequestingHumanApprovalTaskId(task.id);
    setWorkflowErrors((currentErrors) => {
      const nextErrors = { ...currentErrors };
      delete nextErrors[task.id];
      return nextErrors;
    });
    try {
      const result = await apiClient.requestHumanApproval(task.patch_approval.id);
      setTasks((currentTasks) =>
        currentTasks.map((currentTask) =>
          currentTask.id === task.id
            ? {
                ...mergeWorkflowTask(currentTask, result.task),
                patch_artifact: result.patch_artifact,
                patch_review: result.review,
                patch_approval: result.approval
              }
            : currentTask
        )
      );
    } catch (error) {
      setWorkflowErrors((currentErrors) => ({
        ...currentErrors,
        [task.id]: errorMessage(error, "Failed to request human approval")
      }));
    } finally {
      setRequestingHumanApprovalTaskId(null);
    }
  }
```

Pass props to `TaskBoard`:

```tsx
        approvingPatchTaskId={approvingPatchTaskId}
        requestingHumanApprovalTaskId={requestingHumanApprovalTaskId}
        onApprovePatch={handleApprovePatch}
        onRequestHumanApproval={handleRequestHumanApproval}
```

- [ ] **Step 4: Add TaskBoard controls and diff preview**

In `apps/desktop/src/components/TaskBoard.tsx`, extend props:

```typescript
  approvingPatchTaskId?: string | null;
  requestingHumanApprovalTaskId?: string | null;
  onApprovePatch?: (task: TaskCard) => void;
  onRequestHumanApproval?: (task: TaskCard) => void;
```

Add defaults:

```typescript
  approvingPatchTaskId = null,
  requestingHumanApprovalTaskId = null,
  onApprovePatch,
  onRequestHumanApproval
```

Add controls after `Review patch`:

```tsx
              {onApprovePatch && task.status === "APPROVED" && task.patch_artifact ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={approvingPatchTaskId !== null}
                  onClick={() => onApprovePatch(task)}
                >
                  {approvingPatchTaskId === task.id ? "Approving" : "Approve patch"}
                </button>
              ) : null}
              {onRequestHumanApproval && task.status === "MERGE_READY" && task.patch_approval ? (
                <button
                  type="button"
                  className="task-run-button"
                  disabled={requestingHumanApprovalTaskId !== null}
                  onClick={() => onRequestHumanApproval(task)}
                >
                  {requestingHumanApprovalTaskId === task.id
                    ? "Requesting"
                    : "Request human approval"}
                </button>
              ) : null}
```

Add metadata blocks inside `task-patch-meta`:

```tsx
                {task.patch_approval ? (
                  <div>
                    <dt>Patch approval</dt>
                    <dd>{`${task.patch_approval.status} by ${task.patch_approval.approved_by}`}</dd>
                  </div>
                ) : null}
                {task.worktree_ref ? (
                  <div>
                    <dt>Worktree</dt>
                    <dd>{task.worktree_ref}</dd>
                  </div>
                ) : null}
                {task.patch_approval?.merge_instructions ? (
                  <div>
                    <dt>Merge instructions</dt>
                    <dd>{task.patch_approval.merge_instructions}</dd>
                  </div>
                ) : null}
```

After the `dl`, render diff preview:

```tsx
            {task.patch_artifact?.diff_text ? (
              <div className="task-diff-preview">
                <h4>Diff preview</h4>
                <pre>{task.patch_artifact.diff_text}</pre>
              </div>
            ) : null}
```

- [ ] **Step 5: Add CSS for diff preview**

In `apps/desktop/src/styles/app.css`, add after `.task-patch-meta dd`:

```css
.task-diff-preview {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.task-diff-preview h4 {
  margin: 0;
  color: #344047;
  font-size: 11px;
  line-height: 1.3;
}

.task-diff-preview pre {
  max-height: 180px;
  margin: 0;
  overflow: auto;
  border: 1px solid #d7dee2;
  border-radius: 6px;
  padding: 8px;
  background: #f8f9fa;
  color: #172026;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 11px;
  line-height: 1.45;
  white-space: pre;
}
```

- [ ] **Step 6: Update demo fixture**

In `apps/desktop/src/fixtures/demoData.ts`, add `diff_text` to any `patch_artifact` objects:

```typescript
      diff_text: "diff --git a/README.md b/README.md\n+Local runner prepared patch"
```

Add one demo task with `APPROVED` status, review, and approval-ready context:

```typescript
  {
    id: "task_patch_approved",
    title: "Approve reviewed README patch",
    status: "APPROVED",
    role_required: "documentation",
    assigned_agent: "Documentation Agent",
    updated_at: "2026-05-29T00:04:00Z",
    worktree_ref: ".worktrees/task_patch_approved-local_run_demo",
    patch_artifact: {
      id: "patch_approved_demo",
      task_id: "task_patch_approved",
      local_run_id: "local_run_demo",
      summary: "Prepared README patch.",
      files_changed: ["README.md"],
      tests_run: ["python -V"],
      test_result: "passed",
      diff_text: "diff --git a/README.md b/README.md\n+Approved demo patch"
    },
    patch_review: {
      id: "review_approved_demo",
      task_id: "task_patch_approved",
      local_run_id: "local_run_demo",
      patch_artifact_id: "patch_approved_demo",
      test_run_id: "test_run_demo",
      reviewer_kind: "deterministic",
      verdict: "approved",
      issues: [],
      required_changes: [],
      created_at: "2026-05-29T00:03:00Z"
    }
  }
```

- [ ] **Step 7: Run App tests to verify GREEN**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/App.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Run desktop tests**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 9: Commit desktop UI work**

Run:

```powershell
git add apps/desktop/src/App.tsx apps/desktop/src/components/TaskBoard.tsx apps/desktop/src/styles/app.css apps/desktop/src/fixtures/demoData.ts apps/desktop/src/test/App.test.tsx
git commit -m "feat(desktop): add patch approval controls"
```

---

## Task 4: Docs, Roadmap, and Full Verification

**Files:**
- Modify: `docs/architecture.md`
- Modify: `README.md`

- [ ] **Step 1: Update architecture docs**

In `docs/architecture.md`, add after the Phase 5 boundary:

```markdown
## Phase 6 Boundary

Phase 6 adds the human patch approval boundary and a compact unified diff viewer. A task that has passed deterministic review can be patch-approved, which records a durable `PatchApproval`, moves the task to `MERGE_READY`, and exposes merge instructions without modifying git state.

The desktop shows changed files, unified diff text, test result, review verdict, patch approval state, worktree path, and merge instructions. A separate human-approval request moves `MERGE_READY -> HUMAN_APPROVAL`; Phase 6 still does not commit, merge, push, apply patches, create branches, or open pull requests.
```

In the Completed list, add:

```markdown
7. Human patch approval boundary with compact diff preview, durable approval records, `MERGE_READY` and `HUMAN_APPROVAL` transitions, and no automatic git merge.
```

In Future, keep cloud sandbox and PR creation as future work.

- [ ] **Step 2: Update README smoke notes**

In `README.md`, add a Phase 6 section near the existing smoke tests:

```markdown
### Phase 6 patch approval smoke

After a patch reaches `APPROVED`, approve it without merging:

```powershell
$approval = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/patch-artifacts/$($patch.id)/approvals"

$approval.task.status
$approval.approval.merge_instructions
```

Expected:

```text
MERGE_READY
```

Then request human approval:

```powershell
$humanApproval = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/patch-approvals/$($approval.approval.id)/request-human-approval"

$humanApproval.task.status
```

Expected:

```text
HUMAN_APPROVAL
```

This workflow records approval intent only. It does not run `git commit`, `git merge`, `git push`, `git apply`, or create a PR.
```

- [ ] **Step 3: Run full verification**

Run:

```powershell
pnpm test
pnpm typecheck
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
git diff --check
```

Expected:

- `pnpm test`: PASS.
- `pnpm typecheck`: PASS.
- `pytest ... -v`: PASS.
- `git diff --check`: no whitespace errors. CRLF warnings on Windows are acceptable if there are no whitespace error lines.

- [ ] **Step 4: Commit docs**

Run:

```powershell
git add docs/architecture.md README.md
git commit -m "docs: document phase 6 patch approval workflow"
```

- [ ] **Step 5: Final sanity check**

Run:

```powershell
git status --short
git log --oneline -5
```

Expected:

- `git status --short` is empty.
- Recent commits include the three Phase 6 implementation commits and the docs commit.

---

## Self-Review Notes

- Spec coverage: backend approval records, idempotency, `MERGE_READY`, `HUMAN_APPROVAL`, desktop diff preview, approval controls, and no-git-merge boundary are covered.
- Scope wording scan: no task uses unresolved wording or unspecified error handling.
- Type consistency: `PatchApprovalRead`, `PatchApprovalResultRead`, `PatchApprovalCard`, and `PatchApprovalResult` use the same field names across API and desktop.
