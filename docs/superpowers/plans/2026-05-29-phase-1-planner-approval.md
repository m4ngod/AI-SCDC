# Phase 1 Planner Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Planner-only approval loop where a user goal creates persisted `TaskSpec` drafts, a user approves or rejects the batch, and approved drafts become normal tasks in the desktop task board.

**Architecture:** Add first-class planner persistence to the FastAPI control plane: `PlannerRun`, `PlannerTaskDraft`, and `Approval`. Keep planning deterministic through a `FakePlanner` behind a narrow planner interface, then update the desktop shell to call planner-run APIs instead of directly creating tasks. Real model routing, BYOK, agent dispatch, worker execution, and per-draft editing remain outside this plan.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel, Pydantic, pytest, pnpm workspaces, React 19, TypeScript, Vite, Vitest, Testing Library.

---

## File Structure

Create or modify these files:

- Create: `apps/api/app/ai_company_api/services/planner.py`
  - Owns the `PlannerService` protocol, `TaskSpecDraft` value model, and deterministic `FakePlanner`.
- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Adds `PlannerRunStatus`, `ApprovalStatus`, `PlannerRun`, `PlannerTaskDraft`, and `Approval`.
- Modify: `apps/api/app/ai_company_api/models/__init__.py`
  - Exports the new ORM models so SQLModel metadata includes them.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Adds planner request/response schemas.
- Modify: `apps/api/app/ai_company_api/services/repository.py`
  - Adds planner run creation, retrieval, approval, rejection, and draft-to-task conversion.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Adds planner run endpoints.
- Create: `apps/api/tests/test_fake_planner.py`
  - Unit tests deterministic planner behavior and protocol-compatible fields.
- Create: `apps/api/tests/test_planner_endpoints.py`
  - API tests planner run creation, retrieval, approve/reject, and invalid transitions.
- Modify: `apps/desktop/src/api/client.ts`
  - Replaces direct `createTask(goal)` behavior with planner-run client methods.
- Create: `apps/desktop/src/components/PlannerDraftPanel.tsx`
  - Renders generated drafts and batch approve/reject actions.
- Modify: `apps/desktop/src/components/GoalInput.tsx`
  - Makes submit wording and callback generic for planning.
- Modify: `apps/desktop/src/App.tsx`
  - Owns planner run state and updates the task board after approval.
- Modify: `apps/desktop/src/styles/app.css`
  - Adds dense planner draft preview styles.
- Modify: `apps/desktop/src/test/client.test.ts`
  - Tests fake and HTTP planner client behavior.
- Modify: `apps/desktop/src/test/App.test.tsx`
  - Tests planner preview, approve, reject, and error states.
- Modify: `README.md`
  - Updates the local flow description from direct task creation to planner approval.
- Modify: `docs/architecture.md`
  - Updates the first runtime flow for Phase 1.

---

## Task 1: Fake Planner Interface and Unit Tests

**Files:**
- Create: `apps/api/app/ai_company_api/services/planner.py`
- Create: `apps/api/tests/test_fake_planner.py`

- [ ] **Step 1: Write failing tests for deterministic planner output**

Create `apps/api/tests/test_fake_planner.py`:

```python
import json
from pathlib import Path

from ai_company_api.services.planner import FakePlanner


def test_fake_planner_returns_deterministic_task_spec_drafts() -> None:
    planner = FakePlanner()
    first = planner.plan(
        project_id="project_123",
        goal="Build model route settings",
    )
    second = planner.plan(
        project_id="project_123",
        goal="Build model route settings",
    )

    assert first == second
    assert [draft.role_required for draft in first] == ["frontend", "backend"]
    assert all("Build model route settings" in draft.objective for draft in first)


def test_fake_planner_output_matches_agent_protocol_enums() -> None:
    role_schema = json.loads(
        Path("packages/agent-protocol/schemas/agent-role.schema.json").read_text()
    )
    task_spec_schema = json.loads(
        Path("packages/agent-protocol/schemas/task-spec.schema.json").read_text()
    )

    drafts = FakePlanner().plan(
        project_id="project_123",
        goal="Build model route settings",
    )

    assert drafts
    for draft in drafts:
        assert draft.role_required in role_schema["enum"]
        assert draft.risk_level in task_spec_schema["properties"]["risk_level"]["enum"]
        assert draft.title
        assert draft.acceptance_criteria
        assert draft.allowed_paths
        assert isinstance(draft.required_tests, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest apps/api/tests/test_fake_planner.py -v
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'ai_company_api.services.planner'`.

- [ ] **Step 3: Add the fake planner implementation**

Create `apps/api/app/ai_company_api/services/planner.py`:

```python
from typing import Protocol

from pydantic import BaseModel, Field

from ai_company_api.schemas.api import AgentRole, RiskLevel


class TaskSpecDraft(BaseModel):
    title: str
    role_required: AgentRole
    objective: str
    acceptance_criteria: list[str] = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)
    required_tests: list[str] = Field(default_factory=list)
    risk_level: RiskLevel


class PlannerService(Protocol):
    def plan(self, project_id: str, goal: str) -> list[TaskSpecDraft]:
        ...


class FakePlanner:
    def plan(self, project_id: str, goal: str) -> list[TaskSpecDraft]:
        normalized_goal = " ".join(goal.split())
        return [
            TaskSpecDraft(
                title=f"Design desktop flow for {normalized_goal}",
                role_required=AgentRole.FRONTEND,
                objective=(
                    f"Create the user-facing planner approval flow for {normalized_goal}."
                ),
                acceptance_criteria=[
                    "Generated task drafts are visible in the main thread.",
                    "The user can approve or reject the generated draft batch.",
                ],
                allowed_paths=["apps/desktop/**"],
                required_tests=[
                    "App renders planner draft preview",
                    "App approval adds created tasks to the task board",
                ],
                risk_level=RiskLevel.MEDIUM,
            ),
            TaskSpecDraft(
                title=f"Implement planner API for {normalized_goal}",
                role_required=AgentRole.BACKEND,
                objective=(
                    f"Persist planner runs, task drafts, and approval decisions for {normalized_goal}."
                ),
                acceptance_criteria=[
                    "Planner run creation stores ordered drafts.",
                    "Approving a run creates normal tasks and task_created events.",
                ],
                allowed_paths=["apps/api/**"],
                required_tests=[
                    "Planner run endpoint creates drafts",
                    "Planner approval endpoint creates tasks",
                ],
                risk_level=RiskLevel.MEDIUM,
            ),
        ]
```

- [ ] **Step 4: Run planner tests to verify they pass**

Run:

```bash
pytest apps/api/tests/test_fake_planner.py -v
```

Expected: PASS, 2 tests.

- [ ] **Step 5: Commit planner service**

```bash
git add apps/api/app/ai_company_api/services/planner.py apps/api/tests/test_fake_planner.py
git commit -m "feat: add fake planner service"
```

---

## Task 2: Planner Persistence and Read Endpoints

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/models/__init__.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/tests/test_planner_endpoints.py`

- [ ] **Step 1: Write failing API tests for planner run creation and retrieval**

Create `apps/api/tests/test_planner_endpoints.py`:

```python
from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def test_create_planner_run_creates_ordered_drafts_and_no_tasks() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()

        response = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        )

        assert response.status_code == 201
        planner_run = response.json()
        assert planner_run["project_id"] == project["id"]
        assert planner_run["goal"] == "Build model route settings"
        assert planner_run["status"] == "DRAFTED"
        assert planner_run["planner_kind"] == "fake"
        assert planner_run["draft_count"] == 2
        assert [draft["sequence"] for draft in planner_run["drafts"]] == [1, 2]
        assert [draft["role_required"] for draft in planner_run["drafts"]] == [
            "frontend",
            "backend",
        ]

        tasks_response = client.get(f"/projects/{project['id']}/tasks")
        assert tasks_response.status_code == 200
        assert tasks_response.json() == []


def test_create_planner_run_rejects_cross_project_conversation() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        conversation = client.post(
            f"/projects/{other_project['id']}/conversations",
            json={"title": "Other planning"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={
                "goal": "Build model route settings",
                "conversation_id": conversation["id"],
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Conversation does not belong to project"


def test_get_planner_run_returns_ordered_drafts() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.get(f"/planner-runs/{planner_run['id']}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == planner_run["id"]
        assert [draft["sequence"] for draft in data["drafts"]] == [1, 2]


def test_create_planner_run_with_missing_project_returns_404() -> None:
    with build_client() as client:
        response = client.post(
            "/projects/project_missing/planner-runs",
            json={"goal": "Build model route settings"},
        )

        assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest apps/api/tests/test_planner_endpoints.py -v
```

Expected: FAIL with 404 responses for planner endpoints.

- [ ] **Step 3: Add planner persistence models**

In `apps/api/app/ai_company_api/models/entities.py`, add imports:

```python
from enum import Enum
```

Add these classes after `Message` and before `Task`:

```python
class PlannerRunStatus(str, Enum):
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class PlannerRun(SQLModel, table=True):
    __tablename__ = "planner_run"

    id: str = Field(default_factory=lambda: prefixed_id("planner_run"), primary_key=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    conversation_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="conversation.id",
    )
    goal: str
    status: PlannerRunStatus = Field(default=PlannerRunStatus.DRAFTED, index=True)
    planner_kind: str = "fake"
    draft_count: int = 0
    created_by: str = "dev_user"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PlannerTaskDraft(SQLModel, table=True):
    __tablename__ = "planner_task_draft"

    id: str = Field(
        default_factory=lambda: prefixed_id("planner_draft"),
        primary_key=True,
    )
    planner_run_id: str = Field(index=True, foreign_key="planner_run.id")
    sequence: int
    title: str
    role_required: str
    objective: str
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    allowed_paths: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    required_tests: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    risk_level: str = "medium"
    created_at: datetime = Field(default_factory=utc_now)


class Approval(SQLModel, table=True):
    __tablename__ = "approval"

    id: str = Field(default_factory=lambda: prefixed_id("approval"), primary_key=True)
    workspace_id: str = "dev_workspace"
    project_id: str = Field(index=True, foreign_key="project.id")
    planner_run_id: str = Field(index=True, foreign_key="planner_run.id")
    action_type: str = "approve_planner_run"
    risk_level: str = "medium"
    reason: str = ""
    status: ApprovalStatus
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
```

- [ ] **Step 4: Export the new models**

In `apps/api/app/ai_company_api/models/__init__.py`, add the new names to the import/export list:

```python
from ai_company_api.models.entities import (
    Approval,
    ApprovalStatus,
    Conversation,
    Message,
    PlannerRun,
    PlannerRunStatus,
    PlannerTaskDraft,
    Project,
    Task,
    TaskEvent,
)
```

- [ ] **Step 5: Add planner API schemas**

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class PlannerRunCreate(BaseModel):
    goal: str = Field(min_length=1)
    conversation_id: str | None = None


class PlannerTaskDraftRead(BaseModel):
    id: str
    sequence: int
    title: str
    role_required: str
    objective: str
    acceptance_criteria: list[str]
    allowed_paths: list[str]
    required_tests: list[str]
    risk_level: str


class PlannerRunRead(BaseModel):
    id: str
    project_id: str
    conversation_id: str | None
    goal: str
    status: str
    planner_kind: str
    draft_count: int
    drafts: list[PlannerTaskDraftRead]


class PlannerRunReject(BaseModel):
    reason: str = ""
```

- [ ] **Step 6: Add repository helpers for creating and reading planner runs**

In `apps/api/app/ai_company_api/services/repository.py`, import:

```python
from ai_company_api.models.entities import (
    Approval,
    ApprovalStatus,
    Conversation,
    Message,
    PlannerRun,
    PlannerRunStatus,
    PlannerTaskDraft,
    Project,
    Task,
    TaskEvent,
    utc_now,
)
from ai_company_api.schemas.api import (
    ConversationCreate,
    MessageCreate,
    PlannerRunCreate,
    PlannerRunRead,
    PlannerTaskDraftRead,
    ProjectCreate,
    TaskCreate,
)
from ai_company_api.services.planner import FakePlanner, PlannerService
```

Add these functions before `create_task`:

```python
def _planner_draft_read(draft: PlannerTaskDraft) -> PlannerTaskDraftRead:
    return PlannerTaskDraftRead(
        id=draft.id,
        sequence=draft.sequence,
        title=draft.title,
        role_required=draft.role_required,
        objective=draft.objective,
        acceptance_criteria=draft.acceptance_criteria,
        allowed_paths=draft.allowed_paths,
        required_tests=draft.required_tests,
        risk_level=draft.risk_level,
    )


def list_planner_task_drafts(
    session: Session,
    planner_run_id: str,
) -> list[PlannerTaskDraft]:
    statement = (
        select(PlannerTaskDraft)
        .where(PlannerTaskDraft.planner_run_id == planner_run_id)
        .order_by(PlannerTaskDraft.sequence)
    )
    return list(session.exec(statement).all())


def _planner_run_read(session: Session, planner_run: PlannerRun) -> PlannerRunRead:
    drafts = list_planner_task_drafts(session, planner_run.id)
    return PlannerRunRead(
        id=planner_run.id,
        project_id=planner_run.project_id,
        conversation_id=planner_run.conversation_id,
        goal=planner_run.goal,
        status=planner_run.status.value,
        planner_kind=planner_run.planner_kind,
        draft_count=planner_run.draft_count,
        drafts=[_planner_draft_read(draft) for draft in drafts],
    )


def get_planner_run(session: Session, planner_run_id: str) -> PlannerRun:
    planner_run = session.get(PlannerRun, planner_run_id)
    if planner_run is None:
        raise HTTPException(status_code=404, detail="Planner run not found")
    return planner_run


def get_planner_run_read(session: Session, planner_run_id: str) -> PlannerRunRead:
    return _planner_run_read(session, get_planner_run(session, planner_run_id))


def create_planner_run(
    session: Session,
    project_id: str,
    data: PlannerRunCreate,
    planner: PlannerService | None = None,
) -> PlannerRunRead:
    get_project(session, project_id)
    if data.conversation_id is not None:
        conversation = get_conversation(session, data.conversation_id)
        if conversation.project_id != project_id:
            raise HTTPException(
                status_code=400,
                detail="Conversation does not belong to project",
            )

    planner_service = planner or FakePlanner()
    task_specs = planner_service.plan(project_id=project_id, goal=data.goal)
    planner_run = PlannerRun(
        project_id=project_id,
        conversation_id=data.conversation_id,
        goal=data.goal,
        draft_count=len(task_specs),
    )
    session.add(planner_run)
    session.flush()

    for index, task_spec in enumerate(task_specs, start=1):
        session.add(
            PlannerTaskDraft(
                planner_run_id=planner_run.id,
                sequence=index,
                title=task_spec.title,
                role_required=task_spec.role_required.value,
                objective=task_spec.objective,
                acceptance_criteria=task_spec.acceptance_criteria,
                allowed_paths=task_spec.allowed_paths,
                required_tests=task_spec.required_tests,
                risk_level=task_spec.risk_level.value,
            )
        )

    session.commit()
    session.refresh(planner_run)
    return _planner_run_read(session, planner_run)
```

- [ ] **Step 7: Add planner routes**

In `apps/api/app/ai_company_api/api/routes.py`, import the new schemas and repository functions:

```python
from ai_company_api.schemas.api import (
    ConversationCreate,
    MessageCreate,
    PlannerRunCreate,
    PlannerRunReject,
    ProjectCreate,
    TaskCreate,
    TaskUpdate,
)
from ai_company_api.services.repository import (
    create_conversation,
    create_message,
    create_planner_run,
    create_project,
    create_task,
    get_planner_run_read,
    get_task,
    list_conversations,
    list_messages,
    list_projects,
    list_task_events,
    list_tasks,
    transition_task,
)
```

Add these endpoints after project routes:

```python
@router.post("/projects/{project_id}/planner-runs", status_code=status.HTTP_201_CREATED)
def post_project_planner_run(
    project_id: str,
    data: PlannerRunCreate,
    session: SessionDep,
):
    return create_planner_run(session, project_id, data)


@router.get("/planner-runs/{planner_run_id}")
def get_planner_run_by_id(planner_run_id: str, session: SessionDep):
    return get_planner_run_read(session, planner_run_id)
```

`PlannerRunReject` is imported now because Task 3 adds reject handling.

- [ ] **Step 8: Run planner creation tests**

Run:

```bash
pytest apps/api/tests/test_fake_planner.py apps/api/tests/test_planner_endpoints.py -v
```

Expected: Fake planner tests pass. Planner endpoint tests pass except tests for approve/reject do not exist yet.

- [ ] **Step 9: Commit planner persistence and read endpoints**

```bash
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/models/__init__.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/repository.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_planner_endpoints.py
git commit -m "feat: persist planner runs and drafts"
```

---

## Task 3: Planner Approval and Rejection

**Files:**
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/tests/test_planner_endpoints.py`

- [ ] **Step 1: Add failing tests for approve, reject, and repeated decisions**

Append these tests to `apps/api/tests/test_planner_endpoints.py`:

```python
def test_approve_planner_run_creates_tasks_and_task_events() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.post(f"/planner-runs/{planner_run['id']}/approve")

        assert response.status_code == 200
        approval = response.json()
        assert approval["planner_run_id"] == planner_run["id"]
        assert approval["status"] == "APPROVED"
        assert len(approval["created_tasks"]) == 2
        assert [task["role_required"] for task in approval["created_tasks"]] == [
            "frontend",
            "backend",
        ]

        tasks = client.get(f"/projects/{project['id']}/tasks").json()
        assert len(tasks) == 2
        assert all(task["status"] == "CREATED" for task in tasks)

        for task in tasks:
            events = client.get(f"/tasks/{task['id']}/events").json()
            assert [event["event_type"] for event in events] == ["task_created"]

        updated_run = client.get(f"/planner-runs/{planner_run['id']}").json()
        assert updated_run["status"] == "APPROVED"


def test_reject_planner_run_creates_no_tasks() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.post(
            f"/planner-runs/{planner_run['id']}/reject",
            json={"reason": "Too broad for this project."},
        )

        assert response.status_code == 200
        rejection = response.json()
        assert rejection["planner_run_id"] == planner_run["id"]
        assert rejection["status"] == "REJECTED"
        assert rejection["created_tasks"] == []
        assert client.get(f"/projects/{project['id']}/tasks").json() == []
        assert client.get(f"/planner-runs/{planner_run['id']}").json()["status"] == "REJECTED"


def test_planner_run_can_only_be_decided_once() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        first = client.post(f"/planner-runs/{planner_run['id']}/approve")
        second = client.post(f"/planner-runs/{planner_run['id']}/approve")
        third = client.post(
            f"/planner-runs/{planner_run['id']}/reject",
            json={"reason": "Changed my mind."},
        )

        assert first.status_code == 200
        assert second.status_code == 400
        assert second.json()["detail"] == "Planner run has already been decided"
        assert third.status_code == 400
        assert third.json()["detail"] == "Planner run has already been decided"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest apps/api/tests/test_planner_endpoints.py::test_approve_planner_run_creates_tasks_and_task_events apps/api/tests/test_planner_endpoints.py::test_reject_planner_run_creates_no_tasks apps/api/tests/test_planner_endpoints.py::test_planner_run_can_only_be_decided_once -v
```

Expected: FAIL with 404 responses for approve/reject endpoints.

- [ ] **Step 3: Add approval response schema**

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class PlannerRunDecisionRead(BaseModel):
    planner_run_id: str
    approval_id: str
    status: str
    created_tasks: list[dict[str, Any]]
```

- [ ] **Step 4: Add repository approval and rejection functions**

In `apps/api/app/ai_company_api/services/repository.py`, add these helpers after `create_planner_run`:

```python
def _ensure_planner_run_is_drafted(planner_run: PlannerRun) -> None:
    if planner_run.status != PlannerRunStatus.DRAFTED:
        raise HTTPException(
            status_code=400,
            detail="Planner run has already been decided",
        )


def approve_planner_run(session: Session, planner_run_id: str):
    planner_run = get_planner_run(session, planner_run_id)
    _ensure_planner_run_is_drafted(planner_run)
    project = get_project(session, planner_run.project_id)
    drafts = list_planner_task_drafts(session, planner_run.id)

    approval = Approval(
        workspace_id=project.workspace_id,
        project_id=project.id,
        planner_run_id=planner_run.id,
        status=ApprovalStatus.APPROVED,
        decided_by="dev_user",
        decided_at=utc_now(),
    )
    session.add(approval)
    session.flush()

    created_tasks: list[Task] = []
    for draft in drafts:
        task = Task(
            project_id=planner_run.project_id,
            conversation_id=planner_run.conversation_id,
            title=draft.title,
            description=draft.objective,
            role_required=draft.role_required,
            risk_level=draft.risk_level,
            acceptance_criteria=draft.acceptance_criteria,
        )
        session.add(task)
        session.flush()
        create_task_event(
            session,
            task.id,
            "task_created",
            "user",
            "dev_user",
            {
                "status": task.status.value,
                "planner_run_id": planner_run.id,
                "planner_task_draft_id": draft.id,
            },
        )
        created_tasks.append(task)

    planner_run.status = PlannerRunStatus.APPROVED
    planner_run.updated_at = utc_now()
    session.add(planner_run)
    session.commit()
    for task in created_tasks:
        session.refresh(task)

    return {
        "planner_run_id": planner_run.id,
        "approval_id": approval.id,
        "status": planner_run.status.value,
        "created_tasks": created_tasks,
    }


def reject_planner_run(
    session: Session,
    planner_run_id: str,
    reason: str = "",
):
    planner_run = get_planner_run(session, planner_run_id)
    _ensure_planner_run_is_drafted(planner_run)
    project = get_project(session, planner_run.project_id)

    approval = Approval(
        workspace_id=project.workspace_id,
        project_id=project.id,
        planner_run_id=planner_run.id,
        reason=reason,
        status=ApprovalStatus.REJECTED,
        decided_by="dev_user",
        decided_at=utc_now(),
    )
    session.add(approval)

    planner_run.status = PlannerRunStatus.REJECTED
    planner_run.updated_at = utc_now()
    session.add(planner_run)
    session.commit()

    return {
        "planner_run_id": planner_run.id,
        "approval_id": approval.id,
        "status": planner_run.status.value,
        "created_tasks": [],
    }
```

- [ ] **Step 5: Add approve and reject routes**

In `apps/api/app/ai_company_api/api/routes.py`, import:

```python
from ai_company_api.services.repository import (
    approve_planner_run,
    create_conversation,
    create_message,
    create_planner_run,
    create_project,
    create_task,
    get_planner_run_read,
    get_task,
    list_conversations,
    list_messages,
    list_projects,
    list_task_events,
    list_tasks,
    reject_planner_run,
    transition_task,
)
```

Add routes after `get_planner_run_by_id`:

```python
@router.post("/planner-runs/{planner_run_id}/approve")
def approve_planner_run_by_id(planner_run_id: str, session: SessionDep):
    return approve_planner_run(session, planner_run_id)


@router.post("/planner-runs/{planner_run_id}/reject")
def reject_planner_run_by_id(
    planner_run_id: str,
    data: PlannerRunReject,
    session: SessionDep,
):
    return reject_planner_run(session, planner_run_id, data.reason)
```

- [ ] **Step 6: Run planner endpoint tests**

Run:

```bash
pytest apps/api/tests/test_planner_endpoints.py -v
```

Expected: PASS.

- [ ] **Step 7: Run all API tests**

Run:

```bash
pytest apps/api/tests -v
```

Expected: PASS.

- [ ] **Step 8: Commit planner approval behavior**

```bash
git add apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/repository.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_planner_endpoints.py
git commit -m "feat: approve planner task drafts"
```

---

## Task 4: Desktop Planner Client Contract

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/test/client.test.ts`

- [ ] **Step 1: Add client tests for planner-run behavior**

Update `apps/desktop/src/test/client.test.ts` to keep the existing response helper and add planner tests:

```typescript
it("fake client creates deterministic planner drafts", async () => {
  const plannerRun = await fakeApiClient.createPlannerRun("Build model route settings");

  expect(plannerRun).toMatchObject({
    id: "planner_run_demo",
    status: "DRAFTED",
    planner_kind: "fake",
    draft_count: 2
  });
  expect(plannerRun.drafts.map((draft) => draft.role_required)).toEqual([
    "frontend",
    "backend"
  ]);
  expect(plannerRun.drafts[0].objective).toContain("Build model route settings");
});

it("fake client approves planner drafts into task cards", async () => {
  const decision = await fakeApiClient.approvePlannerRun("planner_run_demo");

  expect(decision.status).toBe("APPROVED");
  expect(decision.created_tasks).toEqual([
    expect.objectContaining({
      id: "task_planner_frontend",
      title: "Design desktop flow for planner approval",
      role_required: "frontend"
    }),
    expect.objectContaining({
      id: "task_planner_backend",
      title: "Implement planner approval API",
      role_required: "backend"
    })
  ]);
});

it("HTTP client creates a planner run for the resolved project", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse([{ id: "project_demo" }]))
    .mockResolvedValueOnce(
      jsonResponse(
        {
          id: "planner_run_api",
          project_id: "project_demo",
          goal: "Build model route settings",
          status: "DRAFTED",
          planner_kind: "fake",
          draft_count: 1,
          drafts: [
            {
              id: "planner_draft_api",
              sequence: 1,
              title: "Design model route settings UI",
              role_required: "frontend",
              objective: "Create the desktop UI.",
              acceptance_criteria: ["Draft is visible"],
              allowed_paths: ["apps/desktop/**"],
              required_tests: ["App renders planner draft preview"],
              risk_level: "medium"
            }
          ]
        },
        { status: 201 }
      )
    );
  vi.stubGlobal("fetch", fetchMock);

  const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });
  const plannerRun = await client.createPlannerRun("Build model route settings");

  expect(fetchMock).toHaveBeenNthCalledWith(1, "http://127.0.0.1:8000/projects");
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8000/projects/project_demo/planner-runs",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ goal: "Build model route settings" })
    })
  );
  expect(plannerRun.drafts[0].title).toBe("Design model route settings UI");
});

it("HTTP client approves planner runs and maps created tasks", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
    jsonResponse({
      planner_run_id: "planner_run_api",
      approval_id: "approval_api",
      status: "APPROVED",
      created_tasks: [
        {
          id: "task_api",
          title: "Design model route settings UI",
          status: "CREATED",
          role_required: "frontend",
          created_at: "2026-05-29T01:00:00Z"
        }
      ]
    })
  );
  vi.stubGlobal("fetch", fetchMock);

  const client = createHttpApiClient({
    baseUrl: "http://127.0.0.1:8000/",
    projectId: "project_demo"
  });
  const decision = await client.approvePlannerRun("planner_run_api");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/planner-runs/planner_run_api/approve",
    expect.objectContaining({ method: "POST" })
  );
  expect(decision.created_tasks[0]).toMatchObject({
    id: "task_api",
    assigned_agent: "Frontend Engineer"
  });
});
```

Keep the existing tests for `listTasks()` and HTTP error formatting.

- [ ] **Step 2: Run client tests to verify they fail**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: FAIL because `createPlannerRun`, `approvePlannerRun`, and `rejectPlannerRun` are not part of `ConsoleApiClient`.

- [ ] **Step 3: Extend desktop client types and fake client**

In `apps/desktop/src/api/client.ts`, add types:

```typescript
export type PlannerTaskDraftCard = {
  id: string;
  sequence: number;
  title: string;
  role_required: string;
  objective: string;
  acceptance_criteria: string[];
  allowed_paths: string[];
  required_tests: string[];
  risk_level: string;
};

export type PlannerRunDraft = {
  id: string;
  project_id: string;
  conversation_id?: string | null;
  goal: string;
  status: string;
  planner_kind: string;
  draft_count: number;
  drafts: PlannerTaskDraftCard[];
};

export type PlannerRunDecision = {
  planner_run_id: string;
  approval_id: string;
  status: string;
  created_tasks: TaskCard[];
};
```

Change `ConsoleApiClient`:

```typescript
export type ConsoleApiClient = {
  listTasks: () => Promise<TaskCard[]>;
  createTask: (goal: string) => Promise<TaskCard>;
  createPlannerRun: (goal: string) => Promise<PlannerRunDraft>;
  approvePlannerRun: (plannerRunId: string) => Promise<PlannerRunDecision>;
  rejectPlannerRun: (plannerRunId: string, reason?: string) => Promise<PlannerRunDecision>;
};
```

Keep the fake client's existing `createTask` method for the low-level task API path, and add planner methods:

```typescript
const demoPlannerDrafts = (goal: string): PlannerTaskDraftCard[] => [
  {
    id: "planner_draft_frontend",
    sequence: 1,
    title: "Design desktop flow for planner approval",
    role_required: "frontend",
    objective: `Create the user-facing planner approval flow for ${goal}.`,
    acceptance_criteria: [
      "Generated task drafts are visible in the main thread.",
      "The user can approve or reject the generated draft batch."
    ],
    allowed_paths: ["apps/desktop/**"],
    required_tests: [
      "App renders planner draft preview",
      "App approval adds created tasks to the task board"
    ],
    risk_level: "medium"
  },
  {
    id: "planner_draft_backend",
    sequence: 2,
    title: "Implement planner approval API",
    role_required: "backend",
    objective: `Persist planner runs, task drafts, and approval decisions for ${goal}.`,
    acceptance_criteria: [
      "Planner run creation stores ordered drafts.",
      "Approving a run creates normal tasks and task_created events."
    ],
    allowed_paths: ["apps/api/**"],
    required_tests: [
      "Planner run endpoint creates drafts",
      "Planner approval endpoint creates tasks"
    ],
    risk_level: "medium"
  }
];
```

```typescript
export const fakeApiClient: ConsoleApiClient = {
  async listTasks() {
    return [...demoTasks];
  },
  async createTask() {
    return {
      id: "task_demo_created",
      title: "Build task board",
      status: "CREATED",
      role_required: "frontend",
      assigned_agent: "Frontend Engineer",
      updated_at: "2026-05-29T00:00:00Z"
    };
  },
  async createPlannerRun(goal: string) {
    return {
      id: "planner_run_demo",
      project_id: "project_demo",
      conversation_id: null,
      goal,
      status: "DRAFTED",
      planner_kind: "fake",
      draft_count: 2,
      drafts: demoPlannerDrafts(goal)
    };
  },
  async approvePlannerRun() {
    return {
      planner_run_id: "planner_run_demo",
      approval_id: "approval_demo",
      status: "APPROVED",
      created_tasks: [
        {
          id: "task_planner_frontend",
          title: "Design desktop flow for planner approval",
          status: "CREATED",
          role_required: "frontend",
          assigned_agent: "Frontend Engineer",
          updated_at: "2026-05-29T00:00:00Z"
        },
        {
          id: "task_planner_backend",
          title: "Implement planner approval API",
          status: "CREATED",
          role_required: "backend",
          assigned_agent: "Backend Engineer",
          updated_at: "2026-05-29T00:00:00Z"
        }
      ]
    };
  },
  async rejectPlannerRun(plannerRunId: string) {
    return {
      planner_run_id: plannerRunId,
      approval_id: "approval_demo_rejected",
      status: "REJECTED",
      created_tasks: []
    };
  }
};
```

- [ ] **Step 4: Add HTTP planner methods**

Inside `createHttpApiClient`, return these methods:

```typescript
async createPlannerRun(goal: string) {
  const projectId = await getProjectId();
  const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/planner-runs`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal })
  });
  return readJsonResponse<PlannerRunDraft>(
    response,
    `POST /projects/${projectId}/planner-runs`
  );
},
async approvePlannerRun(plannerRunId: string) {
  const response = await fetch(apiUrl(options.baseUrl, `/planner-runs/${plannerRunId}/approve`), {
    method: "POST",
    headers: { "Content-Type": "application/json" }
  });
  const decision = await readJsonResponse<{
    planner_run_id: string;
    approval_id: string;
    status: string;
    created_tasks: ApiTask[];
  }>(response, `POST /planner-runs/${plannerRunId}/approve`);
  return {
    ...decision,
    created_tasks: decision.created_tasks.map(mapTaskCard)
  };
},
async rejectPlannerRun(plannerRunId: string, reason = "") {
  const response = await fetch(apiUrl(options.baseUrl, `/planner-runs/${plannerRunId}/reject`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason })
  });
  const decision = await readJsonResponse<{
    planner_run_id: string;
    approval_id: string;
    status: string;
    created_tasks: ApiTask[];
  }>(response, `POST /planner-runs/${plannerRunId}/reject`);
  return {
    ...decision,
    created_tasks: decision.created_tasks.map(mapTaskCard)
  };
}
```

- [ ] **Step 5: Run desktop client tests**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: PASS.

- [ ] **Step 6: Run desktop typecheck**

Run:

```bash
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS. Task 4 extends the client contract without breaking the existing direct task creation path; Task 5 changes the UI to use planner methods.

- [ ] **Step 7: Commit client contract**

If tests and typecheck pass, commit the client and test changes:

```bash
git add apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "feat: add desktop planner client contract"
```

---

## Task 5: Desktop Planner Approval UI

**Files:**
- Create: `apps/desktop/src/components/PlannerDraftPanel.tsx`
- Modify: `apps/desktop/src/components/GoalInput.tsx`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/styles/app.css`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Update failing App tests for planner draft preview and approval**

In `apps/desktop/src/test/App.test.tsx`, update `ConsoleApiClient` test objects to implement `createPlannerRun`, `approvePlannerRun`, and `rejectPlannerRun`. Replace the direct task creation tests with:

```typescript
function plannerRunFixture(goal = "Build model route settings") {
  return {
    id: "planner_run_test",
    project_id: "project_demo",
    conversation_id: null,
    goal,
    status: "DRAFTED",
    planner_kind: "fake",
    draft_count: 2,
    drafts: [
      {
        id: "planner_draft_frontend",
        sequence: 1,
        title: "Design desktop flow",
        role_required: "frontend",
        objective: `Create UI for ${goal}.`,
        acceptance_criteria: ["Draft is visible"],
        allowed_paths: ["apps/desktop/**"],
        required_tests: ["App renders planner draft preview"],
        risk_level: "medium"
      },
      {
        id: "planner_draft_backend",
        sequence: 2,
        title: "Implement planner API",
        role_required: "backend",
        objective: `Persist data for ${goal}.`,
        acceptance_criteria: ["Approval creates tasks"],
        allowed_paths: ["apps/api/**"],
        required_tests: ["Planner approval endpoint creates tasks"],
        risk_level: "medium"
      }
    ]
  };
}
```

```typescript
it("submitting a goal renders planner draft preview", async () => {
  const user = userEvent.setup();
  const createPlannerRun = vi.fn<ConsoleApiClient["createPlannerRun"]>().mockResolvedValue(
    plannerRunFixture()
  );
  const apiClient: ConsoleApiClient = {
    listTasks: vi.fn().mockResolvedValue([]),
    createTask: vi.fn(),
    createPlannerRun,
    approvePlannerRun: vi.fn(),
    rejectPlannerRun: vi.fn()
  };

  render(<App apiClient={apiClient} />);

  await user.type(screen.getByLabelText("Goal"), "Build model route settings");
  await user.click(screen.getByRole("button", { name: "Plan tasks" }));

  expect(createPlannerRun).toHaveBeenCalledWith("Build model route settings");
  expect(await screen.findByText("Planner draft")).toBeInTheDocument();
  expect(screen.getByText("Design desktop flow")).toBeInTheDocument();
  expect(screen.getByText("Implement planner API")).toBeInTheDocument();
});

it("approving planner drafts adds created tasks to the task board", async () => {
  const user = userEvent.setup();
  const approvePlannerRun = vi.fn<ConsoleApiClient["approvePlannerRun"]>().mockResolvedValue({
    planner_run_id: "planner_run_test",
    approval_id: "approval_test",
    status: "APPROVED",
    created_tasks: [
      {
        id: "task_frontend",
        title: "Design desktop flow",
        status: "CREATED",
        role_required: "frontend",
        assigned_agent: "Frontend Engineer",
        updated_at: "2026-05-29T00:00:00Z"
      }
    ]
  });
  const apiClient: ConsoleApiClient = {
    listTasks: vi.fn().mockResolvedValue([]),
    createTask: vi.fn(),
    createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
    approvePlannerRun,
    rejectPlannerRun: vi.fn()
  };

  render(<App apiClient={apiClient} />);

  await user.type(screen.getByLabelText("Goal"), "Build model route settings");
  await user.click(screen.getByRole("button", { name: "Plan tasks" }));
  await user.click(await screen.findByRole("button", { name: "Approve drafts" }));

  expect(approvePlannerRun).toHaveBeenCalledWith("planner_run_test");
  const contextPanel = screen.getByRole("complementary", { name: "Task context panel" });
  expect(await within(contextPanel).findByText("Design desktop flow")).toBeInTheDocument();
  expect(screen.getByText("Approved")).toBeInTheDocument();
});

it("rejecting planner drafts does not add tasks", async () => {
  const user = userEvent.setup();
  const rejectPlannerRun = vi.fn<ConsoleApiClient["rejectPlannerRun"]>().mockResolvedValue({
    planner_run_id: "planner_run_test",
    approval_id: "approval_rejected",
    status: "REJECTED",
    created_tasks: []
  });
  const apiClient: ConsoleApiClient = {
    listTasks: vi.fn().mockResolvedValue([]),
    createTask: vi.fn(),
    createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
    approvePlannerRun: vi.fn(),
    rejectPlannerRun
  };

  render(<App apiClient={apiClient} />);

  await user.type(screen.getByLabelText("Goal"), "Build model route settings");
  await user.click(screen.getByRole("button", { name: "Plan tasks" }));
  await user.click(await screen.findByRole("button", { name: "Reject drafts" }));

  expect(rejectPlannerRun).toHaveBeenCalledWith("planner_run_test", "Rejected from desktop shell.");
  expect(screen.getByText("Rejected")).toBeInTheDocument();
  expect(screen.queryByText("task_frontend")).not.toBeInTheDocument();
});

it("shows planner approval errors inline", async () => {
  const user = userEvent.setup();
  const apiClient: ConsoleApiClient = {
    listTasks: vi.fn().mockResolvedValue([]),
    createTask: vi.fn(),
    createPlannerRun: vi.fn().mockResolvedValue(plannerRunFixture()),
    approvePlannerRun: vi.fn().mockRejectedValue(new Error("Planner run has already been decided")),
    rejectPlannerRun: vi.fn()
  };

  render(<App apiClient={apiClient} />);

  await user.type(screen.getByLabelText("Goal"), "Build model route settings");
  await user.click(screen.getByRole("button", { name: "Plan tasks" }));
  await user.click(await screen.findByRole("button", { name: "Approve drafts" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Planner run has already been decided");
});
```

- [ ] **Step 2: Run App tests to verify they fail**

Run:

```bash
pnpm --filter @ai-scdc/desktop test -- src/test/App.test.tsx
```

Expected: FAIL because the app still calls `createTask` and no `PlannerDraftPanel` exists.

- [ ] **Step 3: Add PlannerDraftPanel component**

Create `apps/desktop/src/components/PlannerDraftPanel.tsx`:

```tsx
import type { PlannerRunDraft } from "../api/client";

type PlannerDraftPanelProps = {
  plannerRun: PlannerRunDraft | null;
  decisionStatus: string | null;
  decisionError: string | null;
  isDeciding: boolean;
  onApprove: () => Promise<void> | void;
  onReject: () => Promise<void> | void;
};

export function PlannerDraftPanel({
  plannerRun,
  decisionStatus,
  decisionError,
  isDeciding,
  onApprove,
  onReject
}: PlannerDraftPanelProps) {
  if (!plannerRun) {
    return null;
  }

  return (
    <section className="planner-draft-panel" aria-labelledby="planner-draft-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Planner draft</p>
          <h2 id="planner-draft-title">Planner draft</h2>
        </div>
        <span className="run-state">{decisionStatus ?? plannerRun.status}</span>
      </div>
      <p className="planner-goal">{plannerRun.goal}</p>
      <div className="planner-draft-list">
        {plannerRun.drafts.map((draft) => (
          <article className="planner-draft-card" key={draft.id}>
            <div className="section-heading">
              <h3>{draft.title}</h3>
              <span className="status-pill">{draft.role_required}</span>
            </div>
            <p>{draft.objective}</p>
            <dl>
              <div>
                <dt>Risk</dt>
                <dd>{draft.risk_level}</dd>
              </div>
              <div>
                <dt>Allowed paths</dt>
                <dd>{draft.allowed_paths.join(", ")}</dd>
              </div>
              <div>
                <dt>Acceptance</dt>
                <dd>{draft.acceptance_criteria.join("; ")}</dd>
              </div>
              <div>
                <dt>Tests</dt>
                <dd>{draft.required_tests.join("; ") || "None specified"}</dd>
              </div>
            </dl>
          </article>
        ))}
      </div>
      {decisionError ? (
        <p className="goal-input-error" role="alert">
          {decisionError}
        </p>
      ) : null}
      <div className="planner-actions">
        <button type="button" onClick={onApprove} disabled={isDeciding || decisionStatus !== null}>
          Approve drafts
        </button>
        <button type="button" onClick={onReject} disabled={isDeciding || decisionStatus !== null}>
          Reject drafts
        </button>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Generalize GoalInput copy**

Modify `apps/desktop/src/components/GoalInput.tsx`:

```tsx
type GoalInputProps = {
  onSubmitGoal: (goal: string) => Promise<void> | void;
};

export function GoalInput({ onSubmitGoal }: GoalInputProps) {
```

Replace `await onCreateTask(trimmedGoal);` with:

```tsx
await onSubmitGoal(trimmedGoal);
```

Replace the fallback error and button label:

```tsx
setTaskError(error instanceof Error ? error.message : "Failed to plan tasks");
```

```tsx
<button type="submit" disabled={isSubmitting}>
  Plan tasks
</button>
```

- [ ] **Step 5: Update App state and handlers**

Modify `apps/desktop/src/App.tsx` imports:

```tsx
import type { ConsoleApiClient, PlannerRunDraft, TaskCard } from "./api/client";
import { PlannerDraftPanel } from "./components/PlannerDraftPanel";
```

Add state:

```tsx
const [plannerRun, setPlannerRun] = useState<PlannerRunDraft | null>(null);
const [plannerDecisionStatus, setPlannerDecisionStatus] = useState<string | null>(null);
const [plannerDecisionError, setPlannerDecisionError] = useState<string | null>(null);
const [isDecidingPlannerRun, setIsDecidingPlannerRun] = useState(false);
```

Replace `handleCreateTask` with:

```tsx
async function handleSubmitGoal(goal: string) {
  const run = await apiClient.createPlannerRun(goal);
  setPlannerRun(run);
  setPlannerDecisionStatus(null);
  setPlannerDecisionError(null);
}

async function handleApprovePlannerRun() {
  if (!plannerRun || isDecidingPlannerRun) {
    return;
  }

  setIsDecidingPlannerRun(true);
  try {
    const decision = await apiClient.approvePlannerRun(plannerRun.id);
    setTasks((currentTasks) => [...decision.created_tasks, ...currentTasks]);
    setPlannerDecisionStatus("Approved");
    setPlannerDecisionError(null);
  } catch (error) {
    setPlannerDecisionError(errorMessage(error, "Failed to approve planner run"));
  } finally {
    setIsDecidingPlannerRun(false);
  }
}

async function handleRejectPlannerRun() {
  if (!plannerRun || isDecidingPlannerRun) {
    return;
  }

  setIsDecidingPlannerRun(true);
  try {
    await apiClient.rejectPlannerRun(plannerRun.id, "Rejected from desktop shell.");
    setPlannerDecisionStatus("Rejected");
    setPlannerDecisionError(null);
  } catch (error) {
    setPlannerDecisionError(errorMessage(error, "Failed to reject planner run"));
  } finally {
    setIsDecidingPlannerRun(false);
  }
}
```

Render the panel after `GoalInput`:

```tsx
<GoalInput onSubmitGoal={handleSubmitGoal} />
<PlannerDraftPanel
  plannerRun={plannerRun}
  decisionStatus={plannerDecisionStatus}
  decisionError={plannerDecisionError}
  isDeciding={isDecidingPlannerRun}
  onApprove={handleApprovePlannerRun}
  onReject={handleRejectPlannerRun}
/>
```

- [ ] **Step 6: Add planner styles**

Append to `apps/desktop/src/styles/app.css` before the media query:

```css
.planner-draft-panel {
  display: grid;
  gap: 12px;
  padding: 12px;
  border: 1px solid #c7d0d5;
  border-radius: 8px;
  background: #ffffff;
}

.planner-goal {
  margin: 0;
  color: #344047;
  font-size: 13px;
  line-height: 1.5;
}

.planner-draft-list {
  display: grid;
  gap: 10px;
}

.planner-draft-card {
  display: grid;
  gap: 10px;
  padding: 12px;
  border: 1px solid #d7dee2;
  border-radius: 8px;
  background: #f8f9fa;
}

.planner-draft-card h3 {
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 14px;
  line-height: 1.35;
}

.planner-draft-card p {
  margin: 0;
  color: #344047;
  font-size: 12px;
  line-height: 1.5;
}

.planner-draft-card dl {
  display: grid;
  gap: 8px;
  margin: 0;
}

.planner-draft-card dt {
  color: #344047;
  font-size: 11px;
  font-weight: 700;
}

.planner-draft-card dd {
  margin: 2px 0 0;
  color: #5f6b73;
  font-size: 12px;
  line-height: 1.45;
}

.planner-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.planner-actions button {
  min-height: 32px;
  border: 1px solid #24586a;
  border-radius: 6px;
  padding: 0 10px;
  color: #ffffff;
  background: #2f6f86;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.planner-actions button:last-child {
  border-color: #b8c4cb;
  color: #344047;
  background: #ffffff;
}

.planner-actions button:disabled {
  cursor: not-allowed;
  opacity: 0.65;
}
```

- [ ] **Step 7: Run desktop tests and typecheck**

Run:

```bash
pnpm --filter @ai-scdc/desktop test
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit desktop planner UI**

```bash
git add apps/desktop/src/components/PlannerDraftPanel.tsx apps/desktop/src/components/GoalInput.tsx apps/desktop/src/App.tsx apps/desktop/src/styles/app.css apps/desktop/src/test/App.test.tsx
git commit -m "feat: add planner approval UI"
```

---

## Task 6: Documentation, Browser Smoke, and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update README runtime notes**

In `README.md`, replace the direct task-creation sentence with:

```markdown
The desktop runs in deterministic mock mode by default. Set
`VITE_API_BASE_URL=http://127.0.0.1:8000` before `pnpm dev:desktop` to enable
the minimal FastAPI planner approval path; `VITE_DEMO_PROJECT_ID` can pin the
demo project, otherwise the client creates or reuses one.
```

- [ ] **Step 2: Update architecture runtime flow**

In `docs/architecture.md`, replace the "First Runtime Flow" block with:

````markdown
## First Runtime Flow

```text
User enters goal
  -> desktop shell requests a planner run
  -> FakePlanner creates structured TaskSpec drafts
  -> user approves or rejects the batch
  -> approved drafts become normal tasks
  -> task events capture audit trail
  -> desktop right panel shows created tasks
```

The desktop task client defaults to mock mode when `VITE_API_BASE_URL` is unset
so demos and tests stay deterministic. Setting
`VITE_API_BASE_URL=http://127.0.0.1:8000` enables the minimal HTTP integration:
the desktop resolves or creates a demo project, creates planner runs, approves
or rejects generated drafts, and maps approved tasks into the right-panel task
board.
````

When editing this fenced block, keep the outer markdown valid by ensuring the `text` fence is closed before the paragraph.

- [ ] **Step 3: Run full verification**

Run:

```bash
pnpm test
pnpm typecheck
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
git diff --check
```

Expected:

- `packages/agent-protocol` tests pass.
- `apps/desktop` tests pass.
- API, worker, and LLM gateway Python tests pass.
- Typecheck passes.
- `git diff --check` exits 0.

- [ ] **Step 4: Browser smoke test mock mode**

Start Vite from the repo root:

```powershell
Start-Process -WindowStyle Hidden -FilePath pnpm -ArgumentList @("--filter", "@ai-scdc/desktop", "dev", "--host", "127.0.0.1") -RedirectStandardOutput ".dev-server/desktop.out.log" -RedirectStandardError ".dev-server/desktop.err.log"
```

Open the dev URL from the Vite log, usually `http://127.0.0.1:5173`. Verify:

- The button says `Plan tasks`.
- Submitting a goal renders `Planner draft`.
- The preview shows frontend and backend drafts.
- Clicking `Approve drafts` adds created tasks to the right-panel task board.
- The browser console has no errors.

Stop the Vite process after the smoke test. If removing `.dev-server`, first verify the resolved path is inside the repo root.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/architecture.md
git commit -m "docs: update phase 1 planner flow"
```

- [ ] **Step 6: Request code review before integration**

Use `superpowers:requesting-code-review` with:

- Base SHA: commit before Task 1.
- Head SHA: current HEAD.
- Requirements: `docs/superpowers/specs/2026-05-29-phase-1-planner-approval-design.md`.
- Verification: commands from Step 3 and browser smoke from Step 4.

Fix any Critical or Important review findings before finishing.

---

## Plan Self-Review

Spec coverage:

- Planner-only scope: Tasks 1 through 5.
- Fake planner interface: Task 1.
- PlannerRun, PlannerTaskDraft, Approval persistence: Tasks 2 and 3.
- Create/get/approve/reject API: Tasks 2 and 3.
- Batch approval creates tasks and events: Task 3.
- Desktop draft preview and approve/reject flow: Tasks 4 and 5.
- Inline desktop error handling: Task 5.
- Full verification and browser smoke: Task 6.

Type consistency:

- Backend statuses use `DRAFTED`, `APPROVED`, `REJECTED`.
- Approval status values persisted as `approved` and `rejected`; API decision status returns planner run status.
- Desktop UI uses `createPlannerRun`, `approvePlannerRun`, and `rejectPlannerRun`; `createTask` remains available in the client contract for the lower-level task API path but is no longer used by the goal input flow.
- Created tasks remain `TaskCard[]` in desktop state.

Scope check:

- No real LLM provider calls.
- No model credentials.
- No dispatcher.
- No worker execution.
- No per-draft editing.
- No billing or usage ledger writes.
