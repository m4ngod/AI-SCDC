# Phase 5 Test Review Debug Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the deterministic self-test, review, and debug-attempt loop that moves patch-ready tasks to `REVIEWING`, `APPROVED`, or `FIX_REQUESTED`.

**Architecture:** Keep execution local and synchronous, matching the Phase 4 local runner pattern. The worker package owns command execution inside an existing worktree, the API owns workflow state transitions and persistence, and the desktop consumes compact workflow result objects. No model reviewer/debugger, cloud worker, commit, push, merge, or PR behavior is introduced.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, pytest, subprocess command execution, React 19, TypeScript, Vite, Vitest.

---

## File Structure

- Create: `apps/worker/app/ai_company_worker/test_runner.py`
  - Owns `TestRunnerRequest`, `TestRunnerResult`, `CommandResult`, `TestRunnerError`, and `run_tests()`.
- Create: `apps/worker/tests/test_test_runner.py`
  - Covers passing, failing, timeout, missing worktree, and empty command behavior.
- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Adds `LocalTestRun`, `PatchReview`, and `DebugAttempt`.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Adds read models for test runs, reviews, debug attempts, and combined workflow result responses.
- Create: `apps/api/app/ai_company_api/services/test_review_debug.py`
  - Orchestrates test runs, deterministic reviews, debug attempt creation, and workflow state transitions.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Adds test-run, review, and debug-attempt endpoints.
- Create: `apps/api/tests/test_test_review_debug_api.py`
  - Covers passing tests, failing tests, deterministic review approval/change request, invalid states, and event trail.
- Modify: `apps/desktop/src/api/client.ts`
  - Adds workflow result types and API client methods for testing and review.
- Modify: `apps/desktop/src/App.tsx`
  - Adds handlers and state for running tests and reviewing patches.
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
  - Adds compact `Run tests` and `Review patch` controls and displays results.
- Modify: `apps/desktop/src/fixtures/demoData.ts`
  - Adds demo test/review/debug metadata.
- Modify: `apps/desktop/src/test/client.test.ts`
  - Covers HTTP/fake client workflow methods.
- Modify: `apps/desktop/src/test/App.test.tsx`
  - Covers task board test/review interactions.
- Modify: `docs/architecture.md`
  - Adds Phase 5 boundary and moves the loop to Completed.
- Modify: `README.md`
  - Adds Phase 5 local smoke notes.

---

## Task 1: Worker Test Runner

**Files:**
- Create: `apps/worker/app/ai_company_worker/test_runner.py`
- Create: `apps/worker/tests/test_test_runner.py`

- [ ] **Step 1: Write failing worker tests**

Create `apps/worker/tests/test_test_runner.py`:

```python
from pathlib import Path

from ai_company_worker.test_runner import (
    TestRunnerError,
    TestRunnerRequest,
    run_tests,
)


def test_run_tests_passes_all_commands(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / "README.md").write_text("# Demo\n", encoding="utf-8")

    result = run_tests(
        TestRunnerRequest(
            worktree_path=worktree,
            commands=[
                "python -c \"from pathlib import Path; assert Path('README.md').exists()\"",
                "python -c \"print('ok')\"",
            ],
        )
    )

    assert result.status == "passed"
    assert [item.command for item in result.command_results] == [
        "python -c \"from pathlib import Path; assert Path('README.md').exists()\"",
        "python -c \"print('ok')\"",
    ]
    assert [item.exit_code for item in result.command_results] == [0, 0]
    assert "ok" in result.command_results[1].stdout


def test_run_tests_stops_on_first_failed_command(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_tests(
        TestRunnerRequest(
            worktree_path=worktree,
            commands=[
                "python -c \"import sys; print('bad'); sys.exit(7)\"",
                "python -c \"print('should not run')\"",
            ],
        )
    )

    assert result.status == "failed"
    assert len(result.command_results) == 1
    assert result.command_results[0].exit_code == 7
    assert "bad" in result.command_results[0].stdout


def test_run_tests_marks_timeout_as_failed(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    result = run_tests(
        TestRunnerRequest(
            worktree_path=worktree,
            commands=["python -c \"import time; time.sleep(2)\""],
            timeout_seconds=0.1,
        )
    )

    assert result.status == "failed"
    assert result.command_results[0].exit_code is None
    assert "timed out" in result.command_results[0].stderr.lower()


def test_run_tests_rejects_missing_worktree(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    try:
        run_tests(TestRunnerRequest(worktree_path=missing, commands=["python -V"]))
    except TestRunnerError as exc:
        assert "Worktree path does not exist" in str(exc)
    else:
        raise AssertionError("Expected TestRunnerError")


def test_run_tests_rejects_empty_commands(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    try:
        run_tests(TestRunnerRequest(worktree_path=worktree, commands=[]))
    except TestRunnerError as exc:
        assert "No test commands configured" in str(exc)
    else:
        raise AssertionError("Expected TestRunnerError")
```

- [ ] **Step 2: Run worker test to verify RED**

Run:

```powershell
pytest apps/worker/tests/test_test_runner.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ai_company_worker.test_runner'`.

- [ ] **Step 3: Implement worker test runner**

Create `apps/worker/app/ai_company_worker/test_runner.py`:

```python
import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class TestRunnerError(RuntimeError):
    """Raised when tests cannot be run safely."""


class CommandResult(BaseModel):
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int


class TestRunnerRequest(BaseModel):
    worktree_path: Path
    commands: list[str] = Field(default_factory=list)
    timeout_seconds: float = 120.0


class TestRunnerResult(BaseModel):
    status: Literal["passed", "failed"]
    command_results: list[CommandResult]


def run_tests(request: TestRunnerRequest) -> TestRunnerResult:
    worktree_path = request.worktree_path.resolve()
    if not worktree_path.exists() or not worktree_path.is_dir():
        raise TestRunnerError(f"Worktree path does not exist: {worktree_path}")
    if not request.commands:
        raise TestRunnerError("No test commands configured")

    command_results: list[CommandResult] = []
    aggregate_status: Literal["passed", "failed"] = "passed"

    for command in request.commands:
        result = _run_command(worktree_path, command, request.timeout_seconds)
        command_results.append(result)
        if result.exit_code != 0:
            aggregate_status = "failed"
            break

    return TestRunnerResult(
        status=aggregate_status,
        command_results=command_results,
    )


def _run_command(
    worktree_path: Path,
    command: str,
    timeout_seconds: float,
) -> CommandResult:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=worktree_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(
            command=command,
            exit_code=None,
            stdout=stdout,
            stderr=(stderr + "\nCommand timed out").strip(),
            duration_ms=duration_ms,
        )
    except OSError as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        return CommandResult(
            command=command,
            exit_code=None,
            stdout="",
            stderr=str(exc),
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    return CommandResult(
        command=command,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=duration_ms,
    )
```

- [ ] **Step 4: Run worker tests to verify GREEN**

Run:

```powershell
pytest apps/worker/tests/test_test_runner.py apps/worker/tests/test_local_runner.py apps/worker/tests/test_simulator.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit worker test runner**

Run:

```powershell
git add apps/worker/app/ai_company_worker/test_runner.py apps/worker/tests/test_test_runner.py
git commit -m "feat: add local test command runner"
```

---

## Task 2: API Persistence and Schemas

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Test: `apps/api/tests/test_test_review_debug_api.py`

- [ ] **Step 1: Write failing persistence/schema tests**

Create `apps/api/tests/test_test_review_debug_api.py` with this initial test:

```python
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import (
    DebugAttempt,
    LocalTestRun,
    PatchReview,
    Project,
    Task,
)


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def test_test_review_and_debug_records_persist_json_payloads() -> None:
    with build_session() as session:
        project = Project(name="Demo")
        session.add(project)
        session.flush()
        task = Task(
            project_id=project.id,
            title="Patch task",
            role_required="backend",
            allowed_paths=["README.md"],
            required_tests=["python -V"],
        )
        session.add(task)
        session.flush()

        test_run = LocalTestRun(
            project_id=project.id,
            task_id=task.id,
            local_run_id="local_run_test",
            patch_artifact_id="patch_test",
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
        review = PatchReview(
            project_id=project.id,
            task_id=task.id,
            local_run_id="local_run_test",
            patch_artifact_id="patch_test",
            test_run_id=test_run.id,
            verdict="approved",
            issues=[],
            required_changes=[],
        )
        debug_attempt = DebugAttempt(
            project_id=project.id,
            task_id=task.id,
            patch_artifact_id="patch_test",
            test_run_id=test_run.id,
            root_cause="Tests failed.",
            fix_summary="Rerun implementation after fixing tests.",
        )
        session.add(test_run)
        session.add(review)
        session.add(debug_attempt)
        session.commit()

        persisted_test_run = session.get(LocalTestRun, test_run.id)
        persisted_review = session.get(PatchReview, review.id)
        persisted_debug = session.get(DebugAttempt, debug_attempt.id)

    assert persisted_test_run is not None
    assert persisted_test_run.command_results[0]["exit_code"] == 0
    assert persisted_review is not None
    assert persisted_review.verdict == "approved"
    assert persisted_debug is not None
    assert persisted_debug.status == "requested"
```

- [ ] **Step 2: Run persistence test to verify RED**

Run:

```powershell
pytest apps/api/tests/test_test_review_debug_api.py::test_test_review_and_debug_records_persist_json_payloads -v
```

Expected: FAIL with import errors for `LocalTestRun`, `PatchReview`, and `DebugAttempt`.

- [ ] **Step 3: Add SQLModel tables**

Append these classes in `apps/api/app/ai_company_api/models/entities.py` after `PatchArtifact` and before `TaskEvent`:

```python
class LocalTestRun(SQLModel, table=True):
    __tablename__ = "local_test_run"

    id: str = Field(default_factory=lambda: prefixed_id("test_run"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    status: str = Field(default="running", index=True)
    commands: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    command_results: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    failure_reason: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)


class PatchReview(SQLModel, table=True):
    __tablename__ = "patch_review"

    id: str = Field(default_factory=lambda: prefixed_id("review"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    local_run_id: str = Field(index=True, foreign_key="local_task_run.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    test_run_id: str | None = Field(default=None, index=True, foreign_key="local_test_run.id")
    reviewer_kind: str = "deterministic"
    verdict: str = Field(index=True)
    issues: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    required_changes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now, index=True)


class DebugAttempt(SQLModel, table=True):
    __tablename__ = "debug_attempt"

    id: str = Field(default_factory=lambda: prefixed_id("debug"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    review_id: str | None = Field(default=None, index=True, foreign_key="patch_review.id")
    test_run_id: str | None = Field(default=None, index=True, foreign_key="local_test_run.id")
    status: str = Field(default="requested", index=True)
    root_cause: str
    fix_summary: str
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

- [ ] **Step 4: Add API schemas**

Append these models in `apps/api/app/ai_company_api/schemas/api.py` after `PatchArtifactRead`:

```python
class CommandResultRead(BaseModel):
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int


class LocalTestRunRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    patch_artifact_id: str
    status: str
    commands: list[str]
    command_results: list[CommandResultRead]
    failure_reason: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime


class PatchReviewRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    local_run_id: str
    patch_artifact_id: str
    test_run_id: str | None
    reviewer_kind: str
    verdict: str
    issues: list[dict[str, Any]]
    required_changes: list[str]
    created_at: datetime


class DebugAttemptRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    patch_artifact_id: str
    review_id: str | None
    test_run_id: str | None
    status: str
    root_cause: str
    fix_summary: str
    created_at: datetime


class PatchTestRunResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    test_run: LocalTestRunRead
    debug_attempt: DebugAttemptRead | None = None


class PatchReviewResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    review: PatchReviewRead
    debug_attempt: DebugAttemptRead | None = None
```

- [ ] **Step 5: Run persistence test to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_test_review_debug_api.py::test_test_review_and_debug_records_persist_json_payloads -v
```

Expected: PASS.

- [ ] **Step 6: Commit persistence and schemas**

Run:

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/tests/test_test_review_debug_api.py
git commit -m "feat: add test review debug records"
```

---

## Task 3: API Test Run Workflow

**Files:**
- Create: `apps/api/app/ai_company_api/services/test_review_debug.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_test_review_debug_api.py`

- [ ] **Step 1: Add failing API tests for passing and failing test runs**

Append to `apps/api/tests/test_test_review_debug_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def run_git(repo_path: Path, *args: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def create_git_repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    run_git(repo_path, "init")
    run_git(repo_path, "branch", "-M", "main")
    (repo_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    run_git(repo_path, "add", "README.md")
    run_git(
        repo_path,
        "-c",
        "user.email=dev@example.com",
        "-c",
        "user.name=Dev User",
        "commit",
        "-m",
        "initial commit",
    )
    return repo_path


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def create_patch_ready_task(
    client: TestClient,
    repo_path: Path,
    required_tests: list[str],
) -> tuple[dict, dict, dict, dict]:
    project = client.post("/projects", json={"name": "Demo Project"}).json()
    repository = client.post(
        f"/projects/{project['id']}/repositories",
        json={
            "name": "Local repo",
            "local_path": str(repo_path),
            "default_branch": "main",
        },
    ).json()
    task = client.post(
        f"/projects/{project['id']}/tasks",
        json={
            "title": "Patch README",
            "role_required": "documentation",
            "allowed_paths": ["README.md"],
            "required_tests": required_tests,
        },
    ).json()
    local_run = client.post(
        f"/tasks/{task['id']}/local-runs",
        json={"repo_id": repository["id"]},
    ).json()
    artifact = client.get(f"/patch-artifacts/{local_run['patch_artifact_id']}").json()
    return project, task, local_run, artifact


def test_passing_test_run_moves_patch_ready_task_to_reviewing(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)
    with build_client() as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -c \"from pathlib import Path; assert Path('README.md').exists()\""],
        )

        response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")

        assert response.status_code == 201
        result = response.json()
        assert result["task"]["id"] == task["id"]
        assert result["task"]["status"] == "REVIEWING"
        assert result["patch_artifact"]["test_result"] == "passed"
        assert result["test_run"]["status"] == "passed"
        assert result["debug_attempt"] is None
        events = client.get(f"/tasks/{task['id']}/events").json()

    assert "test_run_started" in [event["event_type"] for event in events]
    assert "test_run_completed" in [event["event_type"] for event in events]


def test_failing_test_run_moves_task_to_fix_requested_and_creates_debug_attempt(
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)
    with build_client() as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -c \"import sys; print('fail'); sys.exit(3)\""],
        )

        response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")

        assert response.status_code == 201
        result = response.json()
        assert result["task"]["status"] == "FIX_REQUESTED"
        assert result["patch_artifact"]["test_result"] == "failed"
        assert result["test_run"]["status"] == "failed"
        assert result["debug_attempt"]["status"] == "requested"
        assert "Test command failed" in result["debug_attempt"]["root_cause"]
```

- [ ] **Step 2: Run API tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_test_review_debug_api.py::test_passing_test_run_moves_patch_ready_task_to_reviewing apps/api/tests/test_test_review_debug_api.py::test_failing_test_run_moves_task_to_fix_requested_and_creates_debug_attempt -v
```

Expected: FAIL with 404 for `/patch-artifacts/{patch_artifact_id}/test-runs`.

- [ ] **Step 3: Implement test workflow service**

Create `apps/api/app/ai_company_api/services/test_review_debug.py` with:

```python
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    DebugAttempt,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    PatchReview,
    Task,
    utc_now,
)
from ai_company_api.schemas.api import (
    CommandResultRead,
    DebugAttemptRead,
    LocalTestRunRead,
    PatchArtifactRead,
    PatchReviewRead,
    PatchReviewResultRead,
    PatchTestRunResultRead,
    TaskRead,
)
from ai_company_api.services.local_runner import get_patch_artifact as get_patch_artifact_read
from ai_company_api.services.repository import (
    create_task_event,
    get_task,
    transition_task,
)
from ai_company_api.services.task_state import TaskStatus
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
    local_run = _get_local_run(session, artifact.local_run_id)
    if task.status != TaskStatus.PATCH_READY:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be PATCH_READY before tests can run",
                "current_status": TaskStatus(task.status).value,
                "expected_status": TaskStatus.PATCH_READY.value,
            },
        )
    if local_run.worktree_path is None:
        raise HTTPException(status_code=400, detail="Local run has no worktree path")

    event_clock = _EventClock()
    _create_workflow_event(
        session,
        event_clock,
        task.id,
        "test_run_started",
        {"patch_artifact_id": artifact.id, "local_run_id": local_run.id},
    )
    transition_task(
        session,
        task.id,
        TaskStatus.SELF_TESTING,
        actor_type="system",
        actor_id="test_runner",
    )
    task = get_task(session, task.id)

    test_run = LocalTestRun(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        status="running",
        commands=list(task.required_tests),
        command_results=[],
    )
    session.add(test_run)
    session.flush()

    try:
        runner_result = RUN_TESTS(
            TestRunnerRequest(
                worktree_path=local_run.worktree_path,
                commands=list(task.required_tests),
            )
        )
        command_results = [
            item.model_dump()
            for item in runner_result.command_results
        ]
        test_run.status = runner_result.status
        test_run.command_results = command_results
        test_run.completed_at = utc_now()
        artifact.tests_run = list(task.required_tests)
        artifact.test_result = runner_result.status
        session.add(artifact)
        session.add(test_run)
    except TestRunnerError as exc:
        test_run.status = "failed"
        test_run.failure_reason = str(exc)
        test_run.completed_at = utc_now()
        artifact.tests_run = list(task.required_tests)
        artifact.test_result = "failed"
        session.add(artifact)
        session.add(test_run)

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

    debug_attempt = None
    if test_run.status == "passed":
        transition_task(
            session,
            task.id,
            TaskStatus.REVIEWING,
            actor_type="system",
            actor_id="test_runner",
        )
    else:
        debug_attempt = _create_debug_attempt(
            session,
            event_clock,
            task,
            artifact,
            review_id=None,
            test_run_id=test_run.id,
            root_cause="Test command failed or could not run.",
            fix_summary="Fix the failing test command output, then rerun local implementation.",
        )
        transition_task(
            session,
            task.id,
            TaskStatus.FIX_REQUESTED,
            actor_type="system",
            actor_id="test_runner",
        )

    session.commit()
    task = get_task(session, task.id)
    session.refresh(artifact)
    session.refresh(test_run)
    return PatchTestRunResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        test_run=_test_run_read(test_run),
        debug_attempt=_debug_attempt_read(debug_attempt) if debug_attempt else None,
    )
```

In the same file, add the read helpers used above:

```python
def list_patch_test_runs(session: Session, patch_artifact_id: str) -> list[LocalTestRunRead]:
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
        raise HTTPException(status_code=404, detail="Test run not found")
    return _test_run_read(test_run)


def list_debug_attempts(session: Session, task_id: str) -> list[DebugAttemptRead]:
    get_task(session, task_id)
    statement = (
        select(DebugAttempt)
        .where(DebugAttempt.task_id == task_id)
        .order_by(DebugAttempt.created_at, DebugAttempt.id)
    )
    return [_debug_attempt_read(attempt) for attempt in session.exec(statement).all()]


def _get_patch_artifact_entity(session: Session, patch_artifact_id: str) -> PatchArtifact:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return artifact


def _get_local_run(session: Session, local_run_id: str) -> LocalTaskRun:
    local_run = session.get(LocalTaskRun, local_run_id)
    if local_run is None:
        raise HTTPException(status_code=404, detail="Local run not found")
    return local_run


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
        command_results=[
            CommandResultRead(**item)
            for item in test_run.command_results
        ],
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


def _create_debug_attempt(
    session: Session,
    event_clock: "_EventClock",
    task: Task,
    artifact: PatchArtifact,
    review_id: str | None,
    test_run_id: str | None,
    root_cause: str,
    fix_summary: str,
) -> DebugAttempt:
    debug_attempt = DebugAttempt(
        project_id=task.project_id,
        task_id=task.id,
        patch_artifact_id=artifact.id,
        review_id=review_id,
        test_run_id=test_run_id,
        root_cause=root_cause,
        fix_summary=fix_summary,
    )
    session.add(debug_attempt)
    session.flush()
    _create_workflow_event(
        session,
        event_clock,
        task.id,
        "debug_attempt_created",
        {"debug_attempt_id": debug_attempt.id},
    )
    return debug_attempt


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
        "test_review_debug",
        payload,
    )
    event.created_at = event_clock.next()
```

- [ ] **Step 4: Add API routes for test runs and debug attempts**

Modify `apps/api/app/ai_company_api/api/routes.py` imports to include:

```python
    DebugAttemptRead,
    LocalTestRunRead,
    PatchTestRunResultRead,
```

Add service imports:

```python
from ai_company_api.services.test_review_debug import (
    get_test_run,
    list_debug_attempts,
    list_patch_test_runs,
    start_patch_test_run,
)
```

Add routes after `GET /patch-artifacts/{patch_artifact_id}`:

```python
@router.post(
    "/patch-artifacts/{patch_artifact_id}/test-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=PatchTestRunResultRead,
)
def post_patch_artifact_test_run(
    patch_artifact_id: str,
    session: SessionDep,
) -> PatchTestRunResultRead:
    return start_patch_test_run(session, patch_artifact_id)


@router.get(
    "/patch-artifacts/{patch_artifact_id}/test-runs",
    response_model=list[LocalTestRunRead],
)
def get_patch_artifact_test_runs(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[LocalTestRunRead]:
    return list_patch_test_runs(session, patch_artifact_id)


@router.get("/test-runs/{test_run_id}", response_model=LocalTestRunRead)
def get_test_run_by_id(test_run_id: str, session: SessionDep) -> LocalTestRunRead:
    return get_test_run(session, test_run_id)


@router.get("/tasks/{task_id}/debug-attempts", response_model=list[DebugAttemptRead])
def get_task_debug_attempts(
    task_id: str,
    session: SessionDep,
) -> list[DebugAttemptRead]:
    return list_debug_attempts(session, task_id)
```

- [ ] **Step 5: Run API workflow tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_test_review_debug_api.py::test_passing_test_run_moves_patch_ready_task_to_reviewing apps/api/tests/test_test_review_debug_api.py::test_failing_test_run_moves_task_to_fix_requested_and_creates_debug_attempt -v
```

Expected: PASS.

- [ ] **Step 6: Commit test run workflow**

Run:

```powershell
git add apps/api/app/ai_company_api/services/test_review_debug.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_test_review_debug_api.py
git commit -m "feat: run local tests for patch artifacts"
```

---

## Task 4: Deterministic Review Workflow

**Files:**
- Modify: `apps/api/app/ai_company_api/services/test_review_debug.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Test: `apps/api/tests/test_test_review_debug_api.py`

- [ ] **Step 1: Add failing review API tests**

Append to `apps/api/tests/test_test_review_debug_api.py`:

```python
def test_review_approves_patch_after_passing_tests(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)
    with build_client() as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -c \"from pathlib import Path; assert Path('README.md').exists()\""],
        )
        test_result = client.post(f"/patch-artifacts/{artifact['id']}/test-runs").json()

        response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")

        assert response.status_code == 201
        result = response.json()
        assert test_result["task"]["status"] == "REVIEWING"
        assert result["task"]["id"] == task["id"]
        assert result["task"]["status"] == "APPROVED"
        assert result["review"]["verdict"] == "approved"
        assert result["review"]["issues"] == []
        assert result["debug_attempt"] is None


def test_review_requests_changes_when_diff_is_missing(tmp_path: Path) -> None:
    from sqlmodel import Session

    from ai_company_api.db.session import build_engine
    from ai_company_api.models.entities import PatchArtifact

    database_url = f"sqlite:///{tmp_path / 'phase5.db'}"
    repo_path = create_git_repo(tmp_path)
    with TestClient(create_app(database_url=database_url)) as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -c \"from pathlib import Path; assert Path('README.md').exists()\""],
        )
        client.post(f"/patch-artifacts/{artifact['id']}/test-runs")

        engine = build_engine(database_url)
        with Session(engine) as session:
            persisted_artifact = session.get(PatchArtifact, artifact["id"])
            assert persisted_artifact is not None
            persisted_artifact.diff_text = ""
            session.add(persisted_artifact)
            session.commit()

        response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")

        assert response.status_code == 201
        result = response.json()
        assert result["task"]["status"] == "FIX_REQUESTED"
        assert result["review"]["verdict"] == "changes_requested"
        assert result["debug_attempt"]["status"] == "requested"
```

Use SQLModel session access in the test if the artifact needs to be mutated to `diff_text=""` before review.

- [ ] **Step 2: Run review tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_test_review_debug_api.py::test_review_approves_patch_after_passing_tests -v
```

Expected: FAIL with 404 for `/patch-artifacts/{patch_artifact_id}/reviews`.

- [ ] **Step 3: Implement deterministic review**

Append this to `apps/api/app/ai_company_api/services/test_review_debug.py`:

```python
def start_patch_review(
    session: Session,
    patch_artifact_id: str,
) -> PatchReviewResultRead:
    artifact = _get_patch_artifact_entity(session, patch_artifact_id)
    task = get_task(session, artifact.task_id)
    if task.status != TaskStatus.REVIEWING:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be REVIEWING before review can run",
                "current_status": TaskStatus(task.status).value,
                "expected_status": TaskStatus.REVIEWING.value,
            },
        )
    test_run = _latest_test_run(session, artifact.id)
    issues = _deterministic_review_issues(task, artifact, test_run)
    verdict = "changes_requested" if issues else "approved"
    required_changes = [
        issue["recommendation"]
        for issue in issues
        if isinstance(issue.get("recommendation"), str)
    ]

    event_clock = _EventClock()
    review = PatchReview(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=artifact.local_run_id,
        patch_artifact_id=artifact.id,
        test_run_id=test_run.id if test_run else None,
        verdict=verdict,
        issues=issues,
        required_changes=required_changes,
    )
    session.add(review)
    session.flush()
    _create_workflow_event(
        session,
        event_clock,
        task.id,
        "patch_review_created",
        {
            "patch_artifact_id": artifact.id,
            "review_id": review.id,
            "verdict": verdict,
        },
    )

    debug_attempt = None
    if verdict == "approved":
        transition_task(
            session,
            task.id,
            TaskStatus.APPROVED,
            actor_type="system",
            actor_id="reviewer",
        )
    else:
        debug_attempt = _create_debug_attempt(
            session,
            event_clock,
            task,
            artifact,
            review_id=review.id,
            test_run_id=test_run.id if test_run else None,
            root_cause="Deterministic review requested changes.",
            fix_summary="Address review issues, then rerun local implementation and tests.",
        )
        transition_task(
            session,
            task.id,
            TaskStatus.FIX_REQUESTED,
            actor_type="system",
            actor_id="reviewer",
        )

    session.commit()
    task = get_task(session, task.id)
    session.refresh(artifact)
    session.refresh(review)
    return PatchReviewResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        review=_review_read(review),
        debug_attempt=_debug_attempt_read(debug_attempt) if debug_attempt else None,
    )


def list_patch_reviews(session: Session, patch_artifact_id: str) -> list[PatchReviewRead]:
    _get_patch_artifact_entity(session, patch_artifact_id)
    statement = (
        select(PatchReview)
        .where(PatchReview.patch_artifact_id == patch_artifact_id)
        .order_by(PatchReview.created_at, PatchReview.id)
    )
    return [_review_read(review) for review in session.exec(statement).all()]


def get_patch_review(session: Session, review_id: str) -> PatchReviewRead:
    review = session.get(PatchReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Patch review not found")
    return _review_read(review)


def _latest_test_run(session: Session, patch_artifact_id: str) -> LocalTestRun | None:
    statement = (
        select(LocalTestRun)
        .where(LocalTestRun.patch_artifact_id == patch_artifact_id)
        .order_by(LocalTestRun.created_at.desc(), LocalTestRun.id.desc())
    )
    return session.exec(statement).first()


def _deterministic_review_issues(
    task: Task,
    artifact: PatchArtifact,
    test_run: LocalTestRun | None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if artifact.diff_text.strip() == "":
        issues.append(
            {
                "severity": "high",
                "category": "artifact",
                "problem": "Patch artifact has no diff.",
                "recommendation": "Generate a patch artifact with diff text before review.",
            }
        )
    if not artifact.files_changed:
        issues.append(
            {
                "severity": "high",
                "category": "artifact",
                "problem": "Patch artifact has no changed files.",
                "recommendation": "Generate a patch with at least one changed file.",
            }
        )
    if test_run is None:
        issues.append(
            {
                "severity": "high",
                "category": "testing",
                "problem": "No test run exists for the patch artifact.",
                "recommendation": "Run tests before review.",
            }
        )
    elif test_run.status != "passed":
        issues.append(
            {
                "severity": "high",
                "category": "testing",
                "problem": "Latest test run did not pass.",
                "recommendation": "Fix failing tests before review approval.",
            }
        )
    for file_changed in artifact.files_changed:
        try:
            from ai_company_worker.local_runner import ensure_changed_files_allowed

            ensure_changed_files_allowed([file_changed], task.allowed_paths)
        except Exception:
            issues.append(
                {
                    "severity": "high",
                    "category": "scope",
                    "problem": f"Changed file is outside allowed paths: {file_changed}",
                    "recommendation": "Restrict changes to task allowed_paths.",
                }
            )
    return issues


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
```

- [ ] **Step 4: Add review routes**

Modify `apps/api/app/ai_company_api/api/routes.py` imports to include:

```python
    PatchReviewRead,
    PatchReviewResultRead,
```

Extend the service import:

```python
    get_patch_review,
    list_patch_reviews,
    start_patch_review,
```

Add routes after test-run routes:

```python
@router.post(
    "/patch-artifacts/{patch_artifact_id}/reviews",
    status_code=status.HTTP_201_CREATED,
    response_model=PatchReviewResultRead,
)
def post_patch_artifact_review(
    patch_artifact_id: str,
    session: SessionDep,
) -> PatchReviewResultRead:
    return start_patch_review(session, patch_artifact_id)


@router.get(
    "/patch-artifacts/{patch_artifact_id}/reviews",
    response_model=list[PatchReviewRead],
)
def get_patch_artifact_reviews(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[PatchReviewRead]:
    return list_patch_reviews(session, patch_artifact_id)


@router.get("/patch-reviews/{review_id}", response_model=PatchReviewRead)
def get_patch_review_by_id(review_id: str, session: SessionDep) -> PatchReviewRead:
    return get_patch_review(session, review_id)
```

- [ ] **Step 5: Run review tests**

Run:

```powershell
pytest apps/api/tests/test_test_review_debug_api.py -v
```

Expected: PASS for the new API tests.

- [ ] **Step 6: Commit deterministic review workflow**

Run:

```powershell
git add apps/api/app/ai_company_api/services/test_review_debug.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_test_review_debug_api.py
git commit -m "feat: add deterministic patch review"
```

---

## Task 5: Desktop Workflow Controls

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
- Modify: `apps/desktop/src/fixtures/demoData.ts`
- Modify: `apps/desktop/src/test/client.test.ts`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Add failing desktop client tests**

Append tests in `apps/desktop/src/test/client.test.ts`:

```typescript
it("HTTP client starts patch tests and maps workflow metadata", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(
    jsonResponse({
      task: {
        id: "task_api",
        title: "Patch README",
        status: "REVIEWING",
        role_required: "documentation",
        updated_at: "2026-05-31T00:00:00Z"
      },
      patch_artifact: {
        id: "patch_api",
        task_id: "task_api",
        local_run_id: "local_run_api",
        summary: "Prepared patch.",
        files_changed: ["README.md"],
        tests_run: ["python -V"],
        test_result: "passed",
        risks: [],
        diff_text: "diff --git a/README.md b/README.md",
        created_at: "2026-05-31T00:00:00Z"
      },
      test_run: {
        id: "test_run_api",
        workspace_id: "dev_workspace",
        project_id: "project_demo",
        task_id: "task_api",
        local_run_id: "local_run_api",
        patch_artifact_id: "patch_api",
        status: "passed",
        commands: ["python -V"],
        command_results: [
          { command: "python -V", exit_code: 0, stdout: "Python", stderr: "", duration_ms: 1 }
        ],
        failure_reason: null,
        started_at: "2026-05-31T00:00:00Z",
        completed_at: "2026-05-31T00:00:01Z",
        created_at: "2026-05-31T00:00:00Z"
      },
      debug_attempt: null
    })
  );
  const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000/" });

  const result = await client.runPatchTests("patch_api");

  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8000/patch-artifacts/patch_api/test-runs",
    expect.objectContaining({ method: "POST" })
  );
  expect(result.task.status).toBe("REVIEWING");
  expect(result.test_run.status).toBe("passed");
});
```

- [ ] **Step 2: Add failing App tests**

Append tests in `apps/desktop/src/test/App.test.tsx`:

```tsx
it("runs tests for patch-ready tasks and shows review action", async () => {
  const user = userEvent.setup();
  const runPatchTests = vi.fn<ConsoleApiClient["runPatchTests"]>().mockResolvedValue({
    task: taskCardFixture("Patch README", {
      id: "task_created_from_planner",
      status: "REVIEWING"
    }),
    patch_artifact: patchArtifactFixture({ test_result: "passed", tests_run: ["python -V"] }),
    test_run: {
      id: "test_run_test",
      status: "passed",
      commands: ["python -V"],
      command_results: [
        { command: "python -V", exit_code: 0, stdout: "Python", stderr: "", duration_ms: 1 }
      ]
    },
    debug_attempt: null
  });
  const apiClient = createMockApiClient({
    listTasks: vi.fn().mockResolvedValue([
      taskCardFixture("Patch README", {
        id: "task_created_from_planner",
        status: "PATCH_READY",
        patch_artifact: patchArtifactFixture()
      })
    ]),
    runPatchTests
  });
  render(<App apiClient={apiClient} />);

  const board = within(screen.getByRole("complementary", { name: "Task context panel" }))
    .getByLabelText("Task board");
  await user.click(await within(board).findByRole("button", { name: "Run tests" }));

  expect(runPatchTests).toHaveBeenCalledWith("patch_test");
  expect(await within(board).findByText("REVIEWING")).toBeInTheDocument();
  expect(await within(board).findByRole("button", { name: "Review patch" })).toBeInTheDocument();
});
```

- [ ] **Step 3: Run desktop tests to verify RED**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
```

Expected: FAIL because `runPatchTests`, `reviewPatch`, and UI controls do not exist.

- [ ] **Step 4: Extend client types and fake/HTTP methods**

In `apps/desktop/src/api/client.ts`, add:

```typescript
export type CommandResultCard = {
  command: string;
  exit_code: number | null;
  stdout: string;
  stderr: string;
  duration_ms: number;
};

export type LocalTestRunCard = {
  id: string;
  status: string;
  commands: string[];
  command_results: CommandResultCard[];
  failure_reason?: string | null;
};

export type PatchReviewCard = {
  id: string;
  verdict: string;
  issues: Array<Record<string, string>>;
  required_changes: string[];
};

export type DebugAttemptCard = {
  id: string;
  status: string;
  root_cause: string;
  fix_summary: string;
};

export type PatchTestRunResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  test_run: LocalTestRunCard;
  debug_attempt?: DebugAttemptCard | null;
};

export type PatchReviewResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  review: PatchReviewCard;
  debug_attempt?: DebugAttemptCard | null;
};
```

Extend `TaskCard`:

```typescript
  test_run?: LocalTestRunCard;
  patch_review?: PatchReviewCard;
  debug_attempt?: DebugAttemptCard | null;
```

Extend `ConsoleApiClient`:

```typescript
  runPatchTests: (patchArtifactId: string) => Promise<PatchTestRunResult>;
  reviewPatch: (patchArtifactId: string) => Promise<PatchReviewResult>;
```

Add fake methods returning `REVIEWING` and `APPROVED` task states. Add HTTP methods:

```typescript
async runPatchTests(patchArtifactId: string) {
  const response = await fetch(
    apiUrl(options.baseUrl, `/patch-artifacts/${patchArtifactId}/test-runs`),
    { method: "POST", headers: { "Content-Type": "application/json" } }
  );
  const result = await readJsonResponse<ApiPatchTestRunResult>(
    response,
    `POST /patch-artifacts/${patchArtifactId}/test-runs`
  );
  return mapPatchTestRunResult(result);
},
async reviewPatch(patchArtifactId: string) {
  const response = await fetch(
    apiUrl(options.baseUrl, `/patch-artifacts/${patchArtifactId}/reviews`),
    { method: "POST", headers: { "Content-Type": "application/json" } }
  );
  const result = await readJsonResponse<ApiPatchReviewResult>(
    response,
    `POST /patch-artifacts/${patchArtifactId}/reviews`
  );
  return mapPatchReviewResult(result);
}
```

- [ ] **Step 5: Extend App and TaskBoard**

Add `runningTestTaskId`, `reviewingTaskId`, and workflow error maps in `apps/desktop/src/App.tsx`. Add handlers:

```typescript
async function handleRunPatchTests(task: TaskCard) {
  if (!task.patch_artifact || runningTestTaskId) return;
  setRunningTestTaskId(task.id);
  try {
    const result = await apiClient.runPatchTests(task.patch_artifact.id);
    setTasks((currentTasks) =>
      currentTasks.map((item) =>
        item.id === task.id
          ? {
              ...item,
              ...result.task,
              patch_artifact: result.patch_artifact,
              test_run: result.test_run,
              debug_attempt: result.debug_attempt
            }
          : item
      )
    );
  } finally {
    setRunningTestTaskId(null);
  }
}
```

Add `handleReviewPatch(task)` in `apps/desktop/src/App.tsx`:

```typescript
async function handleReviewPatch(task: TaskCard) {
  if (!task.patch_artifact || reviewingTaskId) {
    return;
  }

  setReviewingTaskId(task.id);
  try {
    const result = await apiClient.reviewPatch(task.patch_artifact.id);
    setTasks((currentTasks) =>
      currentTasks.map((item) =>
        item.id === task.id
          ? {
              ...item,
              ...result.task,
              patch_artifact: result.patch_artifact,
              patch_review: result.review,
              debug_attempt: result.debug_attempt
            }
          : item
      )
    );
  } finally {
    setReviewingTaskId(null);
  }
}
```

Modify `TaskBoard` props to receive `onRunPatchTests`, `onReviewPatch`, `runningTestTaskId`, and `reviewingTaskId`. Render:

```tsx
{onRunPatchTests && task.status === "PATCH_READY" && task.patch_artifact ? (
  <button type="button" className="task-run-button" onClick={() => onRunPatchTests(task)}>
    Run tests
  </button>
) : null}
{onReviewPatch && task.status === "REVIEWING" && task.patch_artifact ? (
  <button type="button" className="task-run-button" onClick={() => onReviewPatch(task)}>
    Review patch
  </button>
) : null}
```

Display `task.test_run?.status`, `task.patch_review?.verdict`, and `task.debug_attempt?.root_cause` in the existing compact metadata block.

- [ ] **Step 6: Run desktop tests**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit desktop workflow controls**

Run:

```powershell
git add apps/desktop/src/api/client.ts apps/desktop/src/App.tsx apps/desktop/src/components/TaskBoard.tsx apps/desktop/src/fixtures/demoData.ts apps/desktop/src/test/client.test.ts apps/desktop/src/test/App.test.tsx
git commit -m "feat: surface test and review workflow in desktop"
```

---

## Task 6: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update architecture**

In `docs/architecture.md`, add:

```markdown
## Phase 5 Boundary

Phase 5 adds the deterministic self-test, review, and debug-attempt loop. A patch-ready task can run configured test commands inside the local runner worktree, store test output, move to review, and then reach either `APPROVED` or `FIX_REQUESTED`.

Phase 5 does not add model-backed reviewers/debuggers, automatic code repair, commits, pushes, merges, PR creation, or cloud sandbox execution.
```

Move the test/review/debug item from Future to Completed.

- [ ] **Step 2: Update README**

Add a short section after the Phase 4 smoke test:

```markdown
## Phase 5 Test and Review Smoke Test

After a local run creates a patch artifact, call:

```powershell
Invoke-RestMethod -Uri "$base/patch-artifacts/$($artifact.id)/test-runs" -Method Post
Invoke-RestMethod -Uri "$base/patch-artifacts/$($artifact.id)/reviews" -Method Post
```

Passing tests move the task to `REVIEWING`; an approved deterministic review moves it to `APPROVED`. Failing tests or review issues move it to `FIX_REQUESTED` and create a debug attempt.
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

- JavaScript tests pass.
- TypeScript typecheck passes.
- Python tests pass.
- `git diff --check` exits 0.

- [ ] **Step 4: Commit docs**

Run:

```powershell
git add README.md docs/architecture.md
git commit -m "docs: describe phase 5 test review loop"
```

---

## Final Review Checklist

- [ ] Worker test runner executes commands inside the provided worktree.
- [ ] Worker test runner captures stdout, stderr, exit code, and duration.
- [ ] Passing tests move task from `PATCH_READY` through `SELF_TESTING` to `REVIEWING`.
- [ ] Failing tests move task from `SELF_TESTING` to `FIX_REQUESTED`.
- [ ] Failing tests create a `DebugAttempt`.
- [ ] Deterministic review approves valid tested patch artifacts and moves task to `APPROVED`.
- [ ] Deterministic review requests changes for invalid artifacts and moves task to `FIX_REQUESTED`.
- [ ] Review change requests create a `DebugAttempt`.
- [ ] API exposes test run, review, and debug attempt records.
- [ ] Desktop can run tests and review a patch from the task board.
- [ ] No model reviewer/debugger calls are introduced.
- [ ] No auto-commit, push, merge, or PR behavior is introduced.
- [ ] `pnpm test` passes.
- [ ] `pnpm typecheck` passes.
- [ ] `pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v` passes.
- [ ] `git diff --check` passes.

## Plan Self-Review

Spec coverage:

- Data model: Task 2.
- Test runner: Task 1.
- Test run API workflow: Task 3.
- Deterministic review workflow: Task 4.
- Desktop controls: Task 5.
- Documentation and verification: Task 6.

Scope check:

- The plan does not add model reviewer/debugger behavior.
- The plan does not add automatic repair, commit, push, merge, PR, or cloud sandbox behavior.
- The implementation stays within the existing Phase 4 local runner architecture.
