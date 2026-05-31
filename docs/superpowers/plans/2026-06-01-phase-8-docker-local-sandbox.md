# Phase 8 Docker Local Sandbox Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real local Docker sandbox executor for GitHub cloud runs while preserving the existing fake runner, review, approval, and PR creation flow.

**Architecture:** Keep `CloudRun` as the API control-plane record and add a small executor boundary with `fake` and `docker_local` implementations. Add repository-scoped sandbox profiles for whitelisted Docker images, patch commands, test commands, and allowed environment variables. The Docker executor creates normal `LocalTaskRun`, `PatchArtifact`, and `LocalTestRun` records so Phase 5 through Phase 7 continue to work without a parallel workflow.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, pytest, Docker CLI, git CLI, React 19, TypeScript, Vite, Vitest, Testing Library, PowerShell smoke commands.

---

## File Structure

- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Add `SandboxProfile` and Phase 8 `CloudRun` metadata columns.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Add sandbox profile schemas and extend `CloudRunCreate` / `CloudRunRead`.
- Modify: `apps/api/app/ai_company_api/db/session.py`
  - Add SQLite upgrade helper for existing `cloud_run` rows and indexes.
- Create: `apps/api/app/ai_company_api/services/sandbox_profiles.py`
  - Own sandbox profile creation, listing, reads, and validation.
- Create: `apps/api/app/ai_company_api/services/cloud_sandbox_executor.py`
  - Own executor result dataclasses, executor selection, fake executor implementation, and shared redaction helpers.
- Create: `apps/api/app/ai_company_api/services/docker_sandbox.py`
  - Own Docker command construction, process runner abstraction, Docker execution, git/diff capture, and failure mapping.
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Replace inline fake behavior with executor-backed orchestration and persist Docker results.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Add sandbox profile routes and pass extended cloud-run request data.
- Create: `apps/api/tests/test_sandbox_profile_api.py`
  - Cover profile CRUD, validation, cross-project/cross-repo rejection, defaults, and SQLite upgrade behavior.
- Create: `apps/api/tests/test_cloud_sandbox_executor.py`
  - Cover executor selection, fake executor compatibility, redaction, and command result mapping.
- Create: `apps/api/tests/test_docker_sandbox_executor.py`
  - Cover Docker CLI construction, no sensitive mounts, process-runner stubs, timeout/failure mapping, diff capture, and allowed-path checks.
- Modify: `apps/api/tests/test_cloud_run_api.py`
  - Add API-level Docker run success/failure cases while keeping fake cloud tests green.
- Modify: `apps/desktop/src/api/client.ts`
  - Add sandbox profile types, fake client methods, HTTP methods, and cloud-run request payload fields.
- Modify: `apps/desktop/src/App.tsx`
  - Add compact sandbox profile setup state and pass selected profile into cloud-run requests.
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
  - Display sandbox kind, failure reason, and command/test status summaries.
- Modify: `apps/desktop/src/fixtures/demoData.ts`
  - Add Docker local cloud-run fixture data.
- Modify: `apps/desktop/src/styles/app.css`
  - Add compact sandbox profile styles.
- Modify: `apps/desktop/src/test/client.test.ts`
  - Cover fake and HTTP sandbox profile client behavior plus extended cloud-run payloads.
- Modify: `apps/desktop/src/test/App.test.tsx`
  - Cover profile setup, Docker status display, and failed cloud-run messaging.
- Modify: `docs/architecture.md`
  - Record Phase 8 boundary and future remote sandbox path.
- Modify: `README.md`
  - Add Phase 8 PowerShell smoke test for Docker local execution.

---

## Task 1: Sandbox Profile Data Model and API

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Create: `apps/api/app/ai_company_api/services/sandbox_profiles.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/tests/test_sandbox_profile_api.py`

- [ ] **Step 1: Write failing sandbox profile API tests**

Create `apps/api/tests/test_sandbox_profile_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import GitHubCredential, Project, Repository, SandboxProfile
from ai_company_api.services.secret_vault import DevSecretVault


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def create_github_repo(session: Session) -> tuple[Project, Repository]:
    project = Project(name="Sandbox profile project")
    session.add(project)
    session.flush()
    sealed = DevSecretVault().seal("ghp_example1234567890")
    credential = GitHubCredential(
        display_name="GitHub",
        token_last4=sealed.secret_last4,
        encrypted_token=sealed.encrypted_secret,
    )
    session.add(credential)
    session.flush()
    repository = Repository(
        project_id=project.id,
        name="example/demo",
        local_path="",
        default_branch="main",
        provider="github",
        repo_url="https://github.com/example/demo",
        github_owner="example",
        github_repo="demo",
        github_credential_id=credential.id,
        connection_status="active",
    )
    session.add(repository)
    session.commit()
    session.refresh(project)
    session.refresh(repository)
    return project, repository


def profile_payload(repo_id: str) -> dict:
    return {
        "repo_id": repo_id,
        "name": "Default Docker profile",
        "docker_image": "python:3.11-slim",
        "patch_commands": [
            {
                "key": "write-note",
                "label": "Write note",
                "command": "python scripts/write_note.py",
                "timeout_seconds": 30,
                "is_default": True,
            }
        ],
        "test_commands": [
            {
                "key": "python-version",
                "label": "Python version",
                "command": "python -V",
                "timeout_seconds": 30,
                "is_default": True,
            }
        ],
        "allowed_env_vars": ["AI_SCDC_GITHUB_TOKEN"],
        "network_enabled": True,
    }


def test_create_and_list_sandbox_profile(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        init_db(session.get_bind())
        project, repository = create_github_repo(session)

    with build_client(database_path) as client:
        response = client.post(
            f"/projects/{project.id}/sandbox-profiles",
            json=profile_payload(repository.id),
        )
        list_response = client.get(f"/projects/{project.id}/sandbox-profiles")

    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == project.id
    assert body["repo_id"] == repository.id
    assert body["docker_image"] == "python:3.11-slim"
    assert body["patch_commands"][0]["key"] == "write-note"
    assert body["test_commands"][0]["key"] == "python-version"
    assert body["status"] == "active"
    assert list_response.json()[0]["id"] == body["id"]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted = session.get(SandboxProfile, body["id"])
        assert persisted is not None
        assert persisted.patch_commands[0]["key"] == "write-note"


def test_sandbox_profile_rejects_non_github_repo(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "Local project"}).json()
        repository = client.post(
            f"/projects/{project['id']}/repositories",
            json={
                "name": "Local repo",
                "local_path": "T:/repo",
                "default_branch": "main",
            },
        ).json()
        response = client.post(
            f"/projects/{project['id']}/sandbox-profiles",
            json=profile_payload(repository["id"]),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Sandbox profiles require a GitHub repository"


def test_sandbox_profile_rejects_cross_project_repo(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        init_db(session.get_bind())
        _project, repository = create_github_repo(session)

    with build_client(database_path) as client:
        other_project = client.post("/projects", json={"name": "Other"}).json()
        response = client.post(
            f"/projects/{other_project['id']}/sandbox-profiles",
            json=profile_payload(repository.id),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Repository does not belong to project"


def test_sandbox_profile_requires_single_default_command(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        init_db(session.get_bind())
        project, repository = create_github_repo(session)

    payload = profile_payload(repository.id)
    payload["patch_commands"][0]["is_default"] = False

    with build_client(database_path) as client:
        response = client.post(
            f"/projects/{project.id}/sandbox-profiles",
            json=payload,
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Sandbox profile requires exactly one default patch command"


def test_init_db_adds_phase_8_cloud_run_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "old-cloud-run.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table cloud_run (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null,
                    task_id varchar not null,
                    repo_id varchar not null,
                    local_run_id varchar,
                    base_branch varchar not null,
                    head_branch varchar not null,
                    status varchar not null,
                    sandbox_kind varchar not null,
                    patch_artifact_id varchar,
                    failure_reason varchar,
                    created_at datetime not null,
                    updated_at datetime not null
                )
                """
            )
        )

    init_db(engine)

    with engine.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        indexes = {
            row["name"]
            for row in connection.execute(text("PRAGMA index_list(cloud_run)")).mappings()
        }

    assert {"sandbox_profile_id", "patch_command_key", "test_command_keys", "command_results"} <= columns
    assert "ix_cloud_run_sandbox_profile_id" in indexes
```

- [ ] **Step 2: Run profile tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_sandbox_profile_api.py -v
```

Expected: FAIL because `SandboxProfile`, schemas, service, routes, and SQLite upgrade helper are missing.

- [ ] **Step 3: Add model and schemas**

In `apps/api/app/ai_company_api/models/entities.py`, add after `GitHubCredential`:

```python
class SandboxProfile(SQLModel, table=True):
    __tablename__ = "sandbox_profile"

    id: str = Field(
        default_factory=lambda: prefixed_id("sandbox_profile"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    name: str
    docker_image: str
    patch_commands: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    test_commands: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    allowed_env_vars: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    network_enabled: bool = True
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

Extend `CloudRun`:

```python
    sandbox_profile_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="sandbox_profile.id",
    )
    patch_command_key: str | None = None
    test_command_keys: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    command_results: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
```

In `apps/api/app/ai_company_api/schemas/api.py`, add after `GitHubRepositoryCreate`:

```python
class SandboxCommand(BaseModel):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    command: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    is_default: bool = False


class SandboxProfileCreate(BaseModel):
    repo_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    docker_image: str = Field(min_length=1)
    patch_commands: list[SandboxCommand] = Field(min_length=1)
    test_commands: list[SandboxCommand] = Field(default_factory=list)
    allowed_env_vars: list[str] = Field(default_factory=list)
    network_enabled: bool = True


class SandboxProfileRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    repo_id: str
    name: str
    docker_image: str
    patch_commands: list[SandboxCommand]
    test_commands: list[SandboxCommand]
    allowed_env_vars: list[str]
    network_enabled: bool
    status: str
    created_at: datetime
    updated_at: datetime
```

Extend `CloudRunCreate`:

```python
class CloudRunCreate(BaseModel):
    repo_id: str = Field(min_length=1)
    sandbox_profile_id: str | None = Field(default=None, min_length=1)
    patch_command_key: str | None = Field(default=None, min_length=1)
    test_command_keys: list[str] = Field(default_factory=list)
```

Move the existing `CommandResultRead` class above `CloudRunRead`, then extend `CloudRunRead`:

```python
    sandbox_profile_id: str | None
    patch_command_key: str | None
    test_command_keys: list[str]
    command_results: list[CommandResultRead]
```

- [ ] **Step 4: Add SQLite upgrade helper**

In `apps/api/app/ai_company_api/db/session.py`, call the new helper from `init_db()`:

```python
    _upgrade_sqlite_cloud_run_phase_8_columns(engine)
```

Add this helper near the other SQLite upgrade helpers:

```python
def _upgrade_sqlite_cloud_run_phase_8_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "sandbox_profile_id": "VARCHAR",
        "patch_command_key": "VARCHAR",
        "test_command_keys": "JSON",
        "command_results": "JSON",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cloud_run_sandbox_profile_id "
                "ON cloud_run (sandbox_profile_id)"
            )
        )
```

- [ ] **Step 5: Add sandbox profile service and routes**

Create `apps/api/app/ai_company_api/services/sandbox_profiles.py`:

```python
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import Repository, SandboxProfile, utc_now
from ai_company_api.schemas.api import SandboxCommand, SandboxProfileCreate, SandboxProfileRead


def create_sandbox_profile(
    session: Session,
    project_id: str,
    data: SandboxProfileCreate,
) -> SandboxProfileRead:
    repository = _get_repository(session, data.repo_id)
    if repository.project_id != project_id:
        raise HTTPException(status_code=400, detail="Repository does not belong to project")
    if repository.provider != "github":
        raise HTTPException(status_code=400, detail="Sandbox profiles require a GitHub repository")
    _validate_default_commands(data.patch_commands, "patch")
    if data.test_commands:
        _validate_default_commands(data.test_commands, "test")

    profile = SandboxProfile(
        project_id=project_id,
        repo_id=repository.id,
        name=data.name,
        docker_image=data.docker_image,
        patch_commands=[command.model_dump() for command in data.patch_commands],
        test_commands=[command.model_dump() for command in data.test_commands],
        allowed_env_vars=list(dict.fromkeys(data.allowed_env_vars)),
        network_enabled=data.network_enabled,
    )
    session.add(profile)
    session.flush()
    session.commit()
    session.refresh(profile)
    return _profile_read(profile)


def list_sandbox_profiles(session: Session, project_id: str) -> list[SandboxProfileRead]:
    statement = (
        select(SandboxProfile)
        .where(SandboxProfile.project_id == project_id)
        .order_by(SandboxProfile.created_at, SandboxProfile.id)
    )
    return [_profile_read(profile) for profile in session.exec(statement).all()]


def get_sandbox_profile_read(session: Session, profile_id: str) -> SandboxProfileRead:
    profile = get_sandbox_profile(session, profile_id)
    return _profile_read(profile)


def get_sandbox_profile(session: Session, profile_id: str) -> SandboxProfile:
    profile = session.get(SandboxProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Sandbox profile not found")
    return profile


def validate_sandbox_profile_for_repo(
    session: Session,
    profile_id: str,
    *,
    project_id: str,
    repo_id: str,
) -> SandboxProfile:
    profile = get_sandbox_profile(session, profile_id)
    if profile.status != "active":
        raise HTTPException(status_code=400, detail="Sandbox profile is not active")
    if profile.project_id != project_id:
        raise HTTPException(status_code=400, detail="Sandbox profile does not belong to project")
    if profile.repo_id != repo_id:
        raise HTTPException(status_code=400, detail="Sandbox profile does not belong to repository")
    return profile


def _get_repository(session: Session, repo_id: str) -> Repository:
    repository = session.get(Repository, repo_id)
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repository


def _validate_default_commands(commands: list[SandboxCommand], kind: str) -> None:
    _validate_command_keys(commands)
    default_count = sum(1 for command in commands if command.is_default)
    if default_count != 1:
        raise HTTPException(
            status_code=400,
            detail=f"Sandbox profile requires exactly one default {kind} command",
        )


def _validate_command_keys(commands: list[SandboxCommand]) -> None:
    keys = [command.key for command in commands]
    if len(keys) != len(set(keys)):
        raise HTTPException(status_code=400, detail="Sandbox command keys must be unique")


def _profile_read(profile: SandboxProfile) -> SandboxProfileRead:
    return SandboxProfileRead(
        id=profile.id,
        workspace_id=profile.workspace_id,
        project_id=profile.project_id,
        repo_id=profile.repo_id,
        name=profile.name,
        docker_image=profile.docker_image,
        patch_commands=[SandboxCommand(**command) for command in profile.patch_commands],
        test_commands=[SandboxCommand(**command) for command in profile.test_commands],
        allowed_env_vars=profile.allowed_env_vars,
        network_enabled=profile.network_enabled,
        status=profile.status,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )
```

In `apps/api/app/ai_company_api/api/routes.py`, import the schemas and service functions, then add:

```python
@router.post(
    "/projects/{project_id}/sandbox-profiles",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxProfileRead,
)
def post_project_sandbox_profile(
    project_id: str,
    data: SandboxProfileCreate,
    session: SessionDep,
) -> SandboxProfileRead:
    return create_sandbox_profile(session, project_id, data)


@router.get(
    "/projects/{project_id}/sandbox-profiles",
    response_model=list[SandboxProfileRead],
)
def get_project_sandbox_profiles(
    project_id: str,
    session: SessionDep,
) -> list[SandboxProfileRead]:
    return list_sandbox_profiles(session, project_id)


@router.get("/sandbox-profiles/{sandbox_profile_id}", response_model=SandboxProfileRead)
def get_sandbox_profile_by_id(
    sandbox_profile_id: str,
    session: SessionDep,
) -> SandboxProfileRead:
    return get_sandbox_profile_read(session, sandbox_profile_id)
```

- [ ] **Step 6: Run profile tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_sandbox_profile_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/services/sandbox_profiles.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_sandbox_profile_api.py
git commit -m "feat(api): add sandbox profile API"
```

---

## Task 2: Executor Boundary and Fake Runner Compatibility

**Files:**
- Create: `apps/api/app/ai_company_api/services/cloud_sandbox_executor.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/tests/test_cloud_sandbox_executor.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing executor boundary tests**

Create `apps/api/tests/test_cloud_sandbox_executor.py`:

```python
from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    FakeCloudSandboxExecutor,
    SandboxExecutionRequest,
    redact_secrets,
    select_cloud_sandbox_executor,
)


def test_selects_fake_executor_by_default(monkeypatch) -> None:
    monkeypatch.delenv("AI_SCDC_CLOUD_RUNNER", raising=False)

    executor = select_cloud_sandbox_executor()

    assert isinstance(executor, FakeCloudSandboxExecutor)


def test_redact_secrets_replaces_every_secret_value() -> None:
    text = "token ghp_example1234567890 and short ghp_example1234567890"

    assert redact_secrets(text, ["ghp_example1234567890"]) == "token [redacted] and short [redacted]"


def test_command_result_serialization_redacts_secret() -> None:
    result = CommandResult(
        command="git clone",
        exit_code=1,
        stdout="",
        stderr="failed ghp_example1234567890",
        duration_ms=25,
        timed_out=False,
    )

    assert result.redacted(["ghp_example1234567890"]).stderr == "failed [redacted]"


def test_fake_executor_keeps_existing_patch_shape() -> None:
    request = SandboxExecutionRequest(
        task_id="task_1",
        cloud_run_id="cloud_run_1",
        title="Fake cloud task",
        description="",
        repo_url="https://github.com/example/demo",
        base_branch="main",
        head_branch="ai-scdc/task-task_1-cloud_run_1",
        allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
        required_tests=["python -V"],
        docker_image=None,
        patch_command=None,
        test_commands=[],
        env={},
        network_enabled=True,
    )

    result = FakeCloudSandboxExecutor().run(request)

    assert result.status == "patch_ready"
    assert result.runner_kind == "cloud_fake"
    assert result.files_changed == ["AI_SCDC_CLOUD_RUN.md"]
    assert result.test_result == "not_run"
    assert "Fake cloud task" in result.diff_text
```

- [ ] **Step 2: Run executor tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_cloud_sandbox_executor.py -v
```

Expected: FAIL because `cloud_sandbox_executor.py` does not exist.

- [ ] **Step 3: Create executor result types and fake executor**

Create `apps/api/app/ai_company_api/services/cloud_sandbox_executor.py`:

```python
from dataclasses import dataclass, replace
import os
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def redacted(self, secrets: list[str]) -> "CommandResult":
        return replace(
            self,
            stdout=redact_secrets(self.stdout, secrets),
            stderr=redact_secrets(self.stderr, secrets),
        )

    def as_payload(self) -> dict:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "timed_out": self.timed_out,
        }


@dataclass(frozen=True)
class SandboxCommandSelection:
    key: str
    label: str
    command: str
    timeout_seconds: int


@dataclass(frozen=True)
class SandboxExecutionRequest:
    task_id: str
    cloud_run_id: str
    title: str
    description: str
    repo_url: str
    base_branch: str
    head_branch: str
    allowed_paths: list[str]
    required_tests: list[str]
    docker_image: str | None
    patch_command: SandboxCommandSelection | None
    test_commands: list[SandboxCommandSelection]
    env: dict[str, str]
    network_enabled: bool


@dataclass(frozen=True)
class SandboxExecutionResult:
    status: str
    runner_kind: str
    worktree_ref: str | None
    base_sha: str | None
    head_sha: str | None
    summary: str
    files_changed: list[str]
    tests_run: list[str]
    test_result: str
    risks: list[str]
    diff_text: str
    command_results: list[CommandResult]
    test_command_results: list[CommandResult]
    failure_reason: str | None = None


class CloudSandboxExecutor(Protocol):
    sandbox_kind: str

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        ...


class FakeCloudSandboxExecutor:
    sandbox_kind = "fake"

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        diff_text = (
            "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
            "new file mode 100644\n"
            "index 0000000..1111111\n"
            "--- /dev/null\n"
            "+++ b/AI_SCDC_CLOUD_RUN.md\n"
            "@@ -0,0 +1,3 @@\n"
            "+# AI-SCDC Cloud Run\n"
            f"+Task: {request.title}\n"
            f"+Cloud run: {request.cloud_run_id}\n"
        )
        return SandboxExecutionResult(
            status="patch_ready",
            runner_kind="cloud_fake",
            worktree_ref=f"cloud://fake/{request.cloud_run_id}",
            base_sha=None,
            head_sha=None,
            summary="Fake cloud run prepared a deterministic patch artifact.",
            files_changed=["AI_SCDC_CLOUD_RUN.md"],
            tests_run=[],
            test_result="not_run",
            risks=[],
            diff_text=diff_text,
            command_results=[],
            test_command_results=[],
            failure_reason=None,
        )


def select_cloud_sandbox_executor() -> CloudSandboxExecutor:
    runner = os.getenv("AI_SCDC_CLOUD_RUNNER", "fake").strip().lower()
    if runner == "docker_local":
        from ai_company_api.services.docker_sandbox import DockerLocalSandboxExecutor

        return DockerLocalSandboxExecutor()
    return FakeCloudSandboxExecutor()


def redact_secrets(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted
```

- [ ] **Step 4: Refactor cloud runner to call the executor**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, replace inline `_fake_cloud_diff` artifact construction with:

```python
from ai_company_api.services.cloud_sandbox_executor import (
    SandboxCommandSelection,
    SandboxExecutionRequest,
    select_cloud_sandbox_executor,
)
```

Inside `start_cloud_run`, create the `CloudRun` with an empty temporary head branch, flush it to obtain the id, then set the collision-resistant branch name before building the request:

```python
executor = select_cloud_sandbox_executor()
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
```

Persist result fields in the existing `LocalTaskRun`, `PatchArtifact`, and `CloudRun` writes:

```python
local_run.runner_kind = execution_result.runner_kind
local_run.base_sha = execution_result.base_sha
local_run.head_sha = execution_result.head_sha
local_run.worktree_path = execution_result.worktree_ref
cloud_run.command_results = [result.as_payload() for result in execution_result.command_results]
```

Use `execution_result.summary`, `files_changed`, `tests_run`, `test_result`, `risks`, and `diff_text` when creating `PatchArtifact`.

- [ ] **Step 5: Include Phase 8 fields in `CloudRunRead`**

In `_cloud_run_read` inside `cloud_runner.py`, add:

```python
        sandbox_profile_id=cloud_run.sandbox_profile_id,
        patch_command_key=cloud_run.patch_command_key,
        test_command_keys=cloud_run.test_command_keys,
        command_results=cloud_run.command_results,
```

If `CommandResultRead` rejects `timed_out`, normalize payloads before returning:

```python
def _command_result_payloads(cloud_run: CloudRun) -> list[dict]:
    return [
        {
            "command": item.get("command", ""),
            "exit_code": item.get("exit_code"),
            "stdout": item.get("stdout", ""),
            "stderr": item.get("stderr", ""),
            "duration_ms": item.get("duration_ms", 0),
        }
        for item in cloud_run.command_results
    ]
```

- [ ] **Step 6: Run executor and cloud-run compatibility tests**

Run:

```powershell
pytest apps/api/tests/test_cloud_sandbox_executor.py apps/api/tests/test_cloud_run_api.py -v
```

Expected: PASS, including the existing fake runner assertions.

- [ ] **Step 7: Commit Task 2**

```powershell
git add apps/api/app/ai_company_api/services/cloud_sandbox_executor.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/schemas/api.py apps/api/tests/test_cloud_sandbox_executor.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat(api): add cloud sandbox executor boundary"
```

---

## Task 3: Docker Executor Command Construction and Redaction

**Files:**
- Create: `apps/api/app/ai_company_api/services/docker_sandbox.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_sandbox_executor.py`
- Create: `apps/api/tests/test_docker_sandbox_executor.py`

- [ ] **Step 1: Write failing Docker construction tests**

Create `apps/api/tests/test_docker_sandbox_executor.py`:

```python
from pathlib import Path
import subprocess

from ai_company_api.services.cloud_sandbox_executor import (
    SandboxCommandSelection,
    SandboxExecutionRequest,
)
from ai_company_api.services.docker_sandbox import (
    DockerLocalSandboxExecutor,
    ProcessResult,
    RedactingProcessRunner,
)


class RecordingRunner:
    def __init__(self, results: list[ProcessResult] | None = None) -> None:
        self.calls: list[dict] = []
        self.results = results or []

    def run(self, args, *, cwd=None, env=None, timeout_seconds=30):
        self.calls.append(
            {
                "args": [str(item) for item in args],
                "cwd": cwd,
                "env": env,
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.results:
            return self.results.pop(0)
        return ProcessResult(args=[str(item) for item in args], exit_code=0, stdout="", stderr="", duration_ms=1)


def docker_request(tmp_path: Path) -> SandboxExecutionRequest:
    return SandboxExecutionRequest(
        task_id="task_1",
        cloud_run_id="cloud_run_1",
        title="Docker task",
        description="",
        repo_url="https://github.com/example/demo",
        base_branch="main",
        head_branch="ai-scdc/task-task_1-cloud_run_1",
        allowed_paths=["README.md"],
        required_tests=["python -V"],
        docker_image="python:3.11-slim",
        patch_command=SandboxCommandSelection(
            key="write-note",
            label="Write note",
            command="python scripts/write_note.py",
            timeout_seconds=30,
        ),
        test_commands=[
            SandboxCommandSelection(
                key="python-version",
                label="Python version",
                command="python -V",
                timeout_seconds=30,
            )
        ],
        env={"AI_SCDC_GITHUB_TOKEN": "ghp_example1234567890"},
        network_enabled=True,
    )


def test_docker_run_args_do_not_mount_host_home_or_docker_socket(tmp_path: Path) -> None:
    runner = RecordingRunner()
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)

    args = executor.build_docker_run_args(
        request=docker_request(tmp_path),
        workspace_path=tmp_path / "workspace",
        artifact_path=tmp_path / "artifacts",
        command="python -V",
        timeout_seconds=30,
    )
    joined = " ".join(args)

    assert "python:3.11-slim" in args
    assert "/var/run/docker.sock" not in joined
    assert str(Path.home()) not in joined
    assert "--network" in args
    assert "bridge" in args
    assert "-v" in args


def test_redacting_process_runner_removes_token_from_output(tmp_path: Path) -> None:
    base_runner = RecordingRunner(
        [
            ProcessResult(
                args=["git"],
                exit_code=1,
                stdout="",
                stderr="bad ghp_example1234567890",
                duration_ms=5,
            )
        ]
    )
    runner = RedactingProcessRunner(base_runner, ["ghp_example1234567890"])

    result = runner.run(["git", "clone"], timeout_seconds=1)

    assert result.stderr == "bad [redacted]"


def test_selects_docker_executor_when_enabled(monkeypatch) -> None:
    from ai_company_api.services.cloud_sandbox_executor import select_cloud_sandbox_executor

    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")

    executor = select_cloud_sandbox_executor()

    assert executor.sandbox_kind == "docker_local"
```

- [ ] **Step 2: Run Docker construction tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_docker_sandbox_executor.py -v
```

Expected: FAIL because `docker_sandbox.py` does not exist.

- [ ] **Step 3: Implement Docker process primitives**

Create `apps/api/app/ai_company_api/services/docker_sandbox.py`:

```python
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Protocol

from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    redact_secrets,
)


@dataclass(frozen=True)
class ProcessResult:
    args: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    def redacted(self, secrets: list[str]) -> "ProcessResult":
        return replace(
            self,
            stdout=redact_secrets(self.stdout, secrets),
            stderr=redact_secrets(self.stderr, secrets),
        )

    def to_command_result(self, command: str) -> CommandResult:
        return CommandResult(
            command=command,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=self.duration_ms,
            timed_out=self.timed_out,
        )


class ProcessRunner(Protocol):
    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> ProcessResult:
        ...


class SubprocessRunner:
    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> ProcessResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return ProcessResult(
                args=[str(item) for item in args],
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            return ProcessResult(
                args=[str(item) for item in args],
                exit_code=None,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                duration_ms=int((time.monotonic() - started) * 1000),
                timed_out=True,
            )


class RedactingProcessRunner:
    def __init__(self, base_runner: ProcessRunner, secrets: list[str]) -> None:
        self._base_runner = base_runner
        self._secrets = secrets

    def run(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> ProcessResult:
        result = self._base_runner.run(args, cwd=cwd, env=env, timeout_seconds=timeout_seconds)
        return result.redacted(self._secrets)


class DockerLocalSandboxExecutor:
    sandbox_kind = "docker_local"

    def __init__(
        self,
        *,
        process_runner: ProcessRunner | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self._process_runner = process_runner or SubprocessRunner()
        self._workspace_root = workspace_root

    def build_docker_run_args(
        self,
        *,
        request: SandboxExecutionRequest,
        workspace_path: Path,
        artifact_path: Path,
        command: str,
        timeout_seconds: int,
    ) -> list[str]:
        network_args = ["--network", "bridge"] if request.network_enabled else ["--network", "none"]
        env_args: list[str] = []
        for name in sorted(request.env):
            env_args.extend(["-e", name])
        return [
            "docker",
            "run",
            "--rm",
            *network_args,
            "-v",
            f"{workspace_path.as_posix()}:/workspace",
            "-v",
            f"{artifact_path.as_posix()}:/artifacts",
            "-w",
            "/workspace/repo",
            *env_args,
            request.docker_image or "python:3.11-slim",
            "sh",
            "-lc",
            command,
        ]

    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        raise NotImplementedError("Docker execution workflow is added in Task 4")
```

- [ ] **Step 4: Run construction tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_docker_sandbox_executor.py::test_docker_run_args_do_not_mount_host_home_or_docker_socket apps/api/tests/test_docker_sandbox_executor.py::test_redacting_process_runner_removes_token_from_output -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add apps/api/app/ai_company_api/services/docker_sandbox.py apps/api/tests/test_docker_sandbox_executor.py
git commit -m "feat(api): add docker sandbox process boundary"
```

---

## Task 4: Docker Sandbox Success Flow with Stubbed Process Runner

**Files:**
- Modify: `apps/api/app/ai_company_api/services/docker_sandbox.py`
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/tests/test_docker_sandbox_executor.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Add failing Docker executor success test**

Append to `apps/api/tests/test_docker_sandbox_executor.py`:

```python
def test_docker_executor_captures_diff_and_test_result(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            ProcessResult(args=["docker", "version"], exit_code=0, stdout="Docker", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "clone"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "checkout"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "branch"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "patch"], exit_code=0, stdout="patched", stderr="", duration_ms=2),
            ProcessResult(args=["docker", "intent-to-add"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "name-only"], exit_code=0, stdout="README.md\n", stderr="", duration_ms=1),
            ProcessResult(
                args=["docker", "diff"],
                exit_code=0,
                stdout="diff --git a/README.md b/README.md\n+Docker patch\n",
                stderr="",
                duration_ms=1,
            ),
            ProcessResult(args=["docker", "base-sha"], exit_code=0, stdout="abc123\n", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "head-sha"], exit_code=0, stdout="def456\n", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "test"], exit_code=0, stdout="Python 3.11\n", stderr="", duration_ms=3),
        ]
    )
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)

    result = executor.run(docker_request(tmp_path))

    assert result.status == "patch_ready"
    assert result.runner_kind == "docker_local"
    assert result.files_changed == ["README.md"]
    assert result.diff_text.startswith("diff --git a/README.md")
    assert result.base_sha == "abc123"
    assert result.head_sha == "def456"
    assert result.tests_run == ["python-version"]
    assert result.test_result == "passed"
    assert result.test_command_results[0].stdout == "Python 3.11\n"
    assert result.failure_reason is None
```

- [ ] **Step 2: Run success test to verify RED**

Run:

```powershell
pytest apps/api/tests/test_docker_sandbox_executor.py::test_docker_executor_captures_diff_and_test_result -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement Docker success workflow**

In `DockerLocalSandboxExecutor.run`, replace `NotImplementedError` with:

```python
    def run(self, request: SandboxExecutionRequest) -> SandboxExecutionResult:
        command_results: list[CommandResult] = []
        test_results: list[CommandResult] = []
        secrets = list(request.env.values())
        runner = RedactingProcessRunner(self._process_runner, secrets)

        with tempfile.TemporaryDirectory(
            prefix="ai-scdc-docker-",
            dir=str(self._workspace_root) if self._workspace_root else None,
        ) as tmp:
            root = Path(tmp)
            workspace_path = root / "workspace"
            artifact_path = root / "artifacts"
            workspace_path.mkdir(parents=True, exist_ok=True)
            artifact_path.mkdir(parents=True, exist_ok=True)

            docker_version = runner.run(["docker", "version"], timeout_seconds=15)
            command_results.append(docker_version.to_command_result("docker version"))
            if docker_version.exit_code != 0 or docker_version.timed_out:
                return _failed_result("docker_unavailable", "docker_local", command_results, test_results)

            steps = [
                ("clone", f"git clone {request.repo_url} .", 300),
                ("checkout", f"git checkout {request.base_branch}", 60),
                ("branch", f"git checkout -B {request.head_branch}", 60),
                ("patch", request.patch_command.command if request.patch_command else "", request.patch_command.timeout_seconds if request.patch_command else 300),
                ("intent-to-add", "git add -N .", 60),
                ("name-only", "git diff --name-only", 60),
                ("diff", "git diff --no-ext-diff", 60),
                ("base-sha", f"git rev-parse origin/{request.base_branch}", 60),
                ("head-sha", "git rev-parse HEAD", 60),
            ]

            captured: dict[str, ProcessResult] = {}
            for label, command, timeout_seconds in steps:
                process_result = runner.run(
                    self.build_docker_run_args(
                        request=request,
                        workspace_path=workspace_path,
                        artifact_path=artifact_path,
                        command=command,
                        timeout_seconds=timeout_seconds,
                    ),
                    env=request.env,
                    timeout_seconds=timeout_seconds,
                )
                captured[label] = process_result
                command_results.append(process_result.to_command_result(command))
                if process_result.exit_code != 0 or process_result.timed_out:
                    failure = {
                        "clone": "repo_checkout_failed",
                        "checkout": "repo_checkout_failed",
                        "branch": "repo_checkout_failed",
                        "patch": "patch_command_failed",
                        "intent-to-add": "artifact_capture_failed",
                        "name-only": "artifact_capture_failed",
                        "diff": "artifact_capture_failed",
                        "base-sha": "artifact_capture_failed",
                        "head-sha": "artifact_capture_failed",
                    }[label]
                    return _failed_result(failure, "docker_local", command_results, test_results)

            files_changed = sorted(
                line.strip()
                for line in captured["name-only"].stdout.splitlines()
                if line.strip()
            )
            diff_text = captured["diff"].stdout
            if not diff_text.strip():
                return _failed_result("no_patch_produced", "docker_local", command_results, test_results)

            _ensure_files_allowed(files_changed, request.allowed_paths)

            test_status = "passed"
            for command in request.test_commands:
                process_result = runner.run(
                    self.build_docker_run_args(
                        request=request,
                        workspace_path=workspace_path,
                        artifact_path=artifact_path,
                        command=command.command,
                        timeout_seconds=command.timeout_seconds,
                    ),
                    env=request.env,
                    timeout_seconds=command.timeout_seconds,
                )
                test_results.append(process_result.to_command_result(command.command))
                if process_result.exit_code != 0 or process_result.timed_out:
                    test_status = "failed"

            failure_reason = "test_failed" if test_status == "failed" else None
            return SandboxExecutionResult(
                status="patch_ready" if failure_reason is None else "failed",
                runner_kind="docker_local",
                worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                base_sha=captured["base-sha"].stdout.strip() or None,
                head_sha=captured["head-sha"].stdout.strip() or None,
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=files_changed,
                tests_run=[command.key for command in request.test_commands],
                test_result=test_status,
                risks=[],
                diff_text=diff_text,
                command_results=command_results,
                test_command_results=test_results,
                failure_reason=failure_reason,
            )
```

Add helper functions in the same file:

```python
def _failed_result(
    failure_reason: str,
    runner_kind: str,
    command_results: list[CommandResult],
    test_results: list[CommandResult],
) -> SandboxExecutionResult:
    return SandboxExecutionResult(
        status="failed",
        runner_kind=runner_kind,
        worktree_ref=None,
        base_sha=None,
        head_sha=None,
        summary="",
        files_changed=[],
        tests_run=[],
        test_result="not_run",
        risks=[],
        diff_text="",
        command_results=command_results,
        test_command_results=test_results,
        failure_reason=failure_reason,
    )


def _ensure_files_allowed(files_changed: list[str], allowed_paths: list[str]) -> None:
    from ai_company_worker.local_runner import LocalRunnerError, ensure_changed_files_allowed

    try:
        ensure_changed_files_allowed(files_changed, allowed_paths)
    except LocalRunnerError as exc:
        raise DockerSandboxError(str(exc)) from exc


class DockerSandboxError(RuntimeError):
    pass
```

- [ ] **Step 4: Persist Docker test results from cloud runner**

In `apps/api/app/ai_company_api/services/cloud_runner.py`, import `LocalTestRun` and persist a test run when `execution_result.test_command_results` is not empty:

```python
test_run = None
if execution_result.test_command_results:
    test_run = LocalTestRun(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        status=execution_result.test_result,
        commands=execution_result.tests_run,
        command_results=[
            {
                "command": result.command,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            }
            for result in execution_result.test_command_results
        ],
        failure_reason=execution_result.failure_reason,
        completed_at=utc_now(),
    )
    session.add(test_run)
```

Set `cloud_run.command_results` from `execution_result.command_results` and set `cloud_run.failure_reason` from `execution_result.failure_reason`.

- [ ] **Step 5: Run Docker success tests**

Run:

```powershell
pytest apps/api/tests/test_docker_sandbox_executor.py::test_docker_executor_captures_diff_and_test_result apps/api/tests/test_cloud_run_api.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```powershell
git add apps/api/app/ai_company_api/services/docker_sandbox.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/tests/test_docker_sandbox_executor.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat(api): run docker sandbox cloud tasks"
```

---

## Task 5: Docker Cloud-Run API Integration and Failure Semantics

**Files:**
- Modify: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/services/docker_sandbox.py`
- Modify: `apps/api/app/ai_company_api/services/sandbox_profiles.py`
- Modify: `apps/api/tests/test_cloud_run_api.py`
- Modify: `apps/api/tests/test_docker_sandbox_executor.py`

- [ ] **Step 1: Write failing API integration tests**

Append to `apps/api/tests/test_cloud_run_api.py`:

```python
def test_docker_cloud_run_requires_active_sandbox_profile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={"repo_id": repository.id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Docker cloud runs require a sandbox profile"


def test_docker_cloud_run_records_docker_unavailable(tmp_path: Path, monkeypatch) -> None:
    from ai_company_api.services import cloud_runner
    from ai_company_api.services.cloud_sandbox_executor import SandboxExecutionResult

    class UnavailableExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                worktree_ref=None,
                base_sha=None,
                head_sha=None,
                summary="",
                files_changed=[],
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[],
                test_command_results=[],
                failure_reason="docker_unavailable",
            )

    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")
    monkeypatch.setattr(cloud_runner, "select_cloud_sandbox_executor", lambda: UnavailableExecutor())
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, task.project_id, repository.id)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={
            "repo_id": repository.id,
            "sandbox_profile_id": profile.id,
            "patch_command_key": "write-note",
            "test_command_keys": ["python-version"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["cloud_run"]["status"] == "failed"
    assert body["cloud_run"]["failure_reason"] == "docker_unavailable"
    assert body["patch_artifact"] is None


def test_docker_cloud_run_test_failure_keeps_patch_artifact(tmp_path: Path, monkeypatch) -> None:
    from ai_company_api.services import cloud_runner
    from ai_company_api.services.cloud_sandbox_executor import CommandResult, SandboxExecutionResult

    class FailingTestExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                worktree_ref="cloud://docker-local/cloud_run_test",
                base_sha="abc123",
                head_sha="def456",
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["python-version"],
                test_result="failed",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[],
                test_command_results=[
                    CommandResult(
                        command="python -V",
                        exit_code=1,
                        stdout="",
                        stderr="failed",
                        duration_ms=5,
                    )
                ],
                failure_reason="test_failed",
            )

    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")
    monkeypatch.setattr(cloud_runner, "select_cloud_sandbox_executor", lambda: FailingTestExecutor())
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, task.project_id, repository.id)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={
            "repo_id": repository.id,
            "sandbox_profile_id": profile.id,
            "patch_command_key": "write-note",
            "test_command_keys": ["python-version"],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["cloud_run"]["status"] == "failed"
    assert body["cloud_run"]["failure_reason"] == "test_failed"
    assert body["patch_artifact"]["test_result"] == "failed"

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        test_runs = session.exec(select(LocalTestRun)).all()
        persisted_task = session.get(Task, task.id)

    assert len(test_runs) == 1
    assert test_runs[0].status == "failed"
    assert persisted_task.status == TaskStatus.PATCH_READY
```

Add helper near `create_cloud_task`:

```python
def create_profile_entity(session: Session, project_id: str, repo_id: str):
    from ai_company_api.models.entities import SandboxProfile

    profile = SandboxProfile(
        project_id=project_id,
        repo_id=repo_id,
        name="Default Docker profile",
        docker_image="python:3.11-slim",
        patch_commands=[
            {
                "key": "write-note",
                "label": "Write note",
                "command": "python scripts/write_note.py",
                "timeout_seconds": 30,
                "is_default": True,
            }
        ],
        test_commands=[
            {
                "key": "python-version",
                "label": "Python version",
                "command": "python -V",
                "timeout_seconds": 30,
                "is_default": True,
            }
        ],
        allowed_env_vars=["AI_SCDC_GITHUB_TOKEN"],
        network_enabled=True,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile
```

- [ ] **Step 2: Run integration tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -v
```

Expected: FAIL because Docker profile selection and failure persistence are incomplete.

- [ ] **Step 3: Resolve profile commands in cloud runner**

In `cloud_runner.py`, import:

```python
from ai_company_api.services.sandbox_profiles import validate_sandbox_profile_for_repo
```

Add helpers:

```python
def _select_profile_commands(profile, data: CloudRunCreate):
    patch_command = _select_command(
        profile.patch_commands,
        data.patch_command_key,
        "patch",
    )
    test_commands = [
        _select_command(profile.test_commands, key, "test")
        for key in data.test_command_keys
    ]
    if not test_commands:
        test_commands = [command for command in profile.test_commands if command.get("is_default")]
    return patch_command, test_commands


def _select_command(commands: list[dict], requested_key: str | None, kind: str) -> dict:
    if requested_key:
        for command in commands:
            if command.get("key") == requested_key:
                return command
        raise HTTPException(status_code=400, detail=f"Unknown sandbox {kind} command key")
    defaults = [command for command in commands if command.get("is_default")]
    if len(defaults) != 1:
        raise HTTPException(status_code=400, detail=f"Sandbox profile requires exactly one default {kind} command")
    return defaults[0]
```

When `executor.sandbox_kind == "docker_local"`, require `data.sandbox_profile_id`:

```python
if executor.sandbox_kind == "docker_local":
    if data.sandbox_profile_id is None:
        raise HTTPException(status_code=400, detail="Docker cloud runs require a sandbox profile")
    profile = validate_sandbox_profile_for_repo(
        session,
        data.sandbox_profile_id,
        project_id=task.project_id,
        repo_id=repository.id,
    )
    patch_command, test_commands = _select_profile_commands(profile, data)
else:
    profile = None
    patch_command = None
    test_commands = []
```

Convert command dicts into `SandboxCommandSelection` before building `SandboxExecutionRequest`.

- [ ] **Step 4: Persist failed cloud runs without creating empty artifacts**

In `cloud_runner.py`, after executor returns:

```python
if execution_result.failure_reason and execution_result.diff_text.strip() == "":
    local_run.status = "failed"
    local_run.failure_reason = execution_result.failure_reason
    local_run.updated_at = utc_now()
    cloud_run.status = "failed"
    cloud_run.failure_reason = execution_result.failure_reason
    cloud_run.command_results = [result.as_payload() for result in execution_result.command_results]
    cloud_run.updated_at = utc_now()
    task.repo_id = repository.id
    task.branch_name = head_branch
    task.worktree_ref = execution_result.worktree_ref
    session.add(local_run)
    session.add(cloud_run)
    session.add(task)
    _create_cloud_run_event(
        session,
        event_clock,
        task.id,
        "cloud_run_failed",
        {
            "cloud_run_id": cloud_run.id,
            "failure_reason": execution_result.failure_reason,
        },
    )
    session.commit()
    session.refresh(cloud_run)
    return CloudRunResultRead(cloud_run=_cloud_run_read(cloud_run), patch_artifact=None)
```

When diff exists but `failure_reason == "test_failed"`, create the artifact and test run, mark cloud/local run failed, transition the task to `PATCH_READY`, and rely on review/test gates to block approval.

- [ ] **Step 5: Map Docker allowed-path errors**

In `docker_sandbox.py`, catch `DockerSandboxError` in `run` and return:

```python
        except DockerSandboxError as exc:
            return _failed_result(
                "artifact_capture_failed",
                "docker_local",
                command_results,
                test_results,
            )
```

Append a test:

```python
def test_docker_executor_rejects_changes_outside_allowed_paths(tmp_path: Path) -> None:
    runner = RecordingRunner(
        [
            ProcessResult(args=["docker", "version"], exit_code=0, stdout="Docker", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "clone"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "checkout"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "branch"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "patch"], exit_code=0, stdout="patched", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "intent-to-add"], exit_code=0, stdout="", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "name-only"], exit_code=0, stdout="secret.txt\n", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "diff"], exit_code=0, stdout="diff --git a/secret.txt b/secret.txt\n+secret\n", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "base-sha"], exit_code=0, stdout="abc123\n", stderr="", duration_ms=1),
            ProcessResult(args=["docker", "head-sha"], exit_code=0, stdout="def456\n", stderr="", duration_ms=1),
        ]
    )
    executor = DockerLocalSandboxExecutor(process_runner=runner, workspace_root=tmp_path)

    result = executor.run(docker_request(tmp_path))

    assert result.status == "failed"
    assert result.failure_reason == "artifact_capture_failed"
```

- [ ] **Step 6: Run integration and failure tests**

Run:

```powershell
pytest apps/api/tests/test_docker_sandbox_executor.py apps/api/tests/test_cloud_run_api.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit Task 5**

```powershell
git add apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/docker_sandbox.py apps/api/app/ai_company_api/services/sandbox_profiles.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_docker_sandbox_executor.py
git commit -m "feat(api): persist docker cloud run results"
```

---

## Task 6: Desktop Sandbox Profile Controls and Docker Status Display

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
- Modify: `apps/desktop/src/fixtures/demoData.ts`
- Modify: `apps/desktop/src/styles/app.css`
- Modify: `apps/desktop/src/test/client.test.ts`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Write failing desktop client tests**

Append to `apps/desktop/src/test/client.test.ts`:

```typescript
it("fake client creates sandbox profiles and passes cloud run profile keys", async () => {
  const credential = await fakeApiClient.createGitHubCredential({
    display_name: "GitHub",
    token: "ghp_example1234567890"
  });
  const repository = await fakeApiClient.createGitHubRepository("project_demo", {
    github_credential_id: credential.id,
    repo_url: "https://github.com/example/demo",
    github_owner: "example",
    github_repo: "demo"
  });

  const profile = await fakeApiClient.createSandboxProfile("project_demo", {
    repo_id: repository.id,
    name: "Default Docker profile",
    docker_image: "python:3.11-slim",
    patch_commands: [
      {
        key: "write-note",
        label: "Write note",
        command: "python scripts/write_note.py",
        timeout_seconds: 30,
        is_default: true
      }
    ],
    test_commands: [
      {
        key: "python-version",
        label: "Python version",
        command: "python -V",
        timeout_seconds: 30,
        is_default: true
      }
    ],
    allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
    network_enabled: true
  });
  const cloud = await fakeApiClient.startCloudRun("task_demo_created", {
    sandbox_profile_id: profile.id,
    patch_command_key: "write-note",
    test_command_keys: ["python-version"]
  });

  expect(profile.docker_image).toBe("python:3.11-slim");
  expect(cloud.cloud_run.sandbox_kind).toBe("docker_local");
});

it("HTTP client posts sandbox profile and cloud run profile keys", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(jsonResponse({
      id: "sandbox_profile_api",
      workspace_id: "dev_workspace",
      project_id: "project_demo",
      repo_id: "repo_github_api",
      name: "Default Docker profile",
      docker_image: "python:3.11-slim",
      patch_commands: [{
        key: "write-note",
        label: "Write note",
        command: "python scripts/write_note.py",
        timeout_seconds: 30,
        is_default: true
      }],
      test_commands: [{
        key: "python-version",
        label: "Python version",
        command: "python -V",
        timeout_seconds: 30,
        is_default: true
      }],
      allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
      network_enabled: true,
      status: "active",
      created_at: "2026-06-01T00:00:00Z",
      updated_at: "2026-06-01T00:00:00Z"
    }), { status: 201 })
    .mockResolvedValueOnce(jsonResponse({
      cloud_run: {
        id: "cloud_run_api",
        task_id: "task_api",
        repo_id: "repo_github_api",
        local_run_id: "local_run_api",
        base_branch: "main",
        head_branch: "ai-scdc/task-api",
        status: "patch_ready",
        sandbox_kind: "docker_local",
        patch_artifact_id: "patch_api",
        failure_reason: null,
        sandbox_profile_id: "sandbox_profile_api",
        patch_command_key: "write-note",
        test_command_keys: ["python-version"],
        command_results: [],
        created_at: "2026-06-01T00:00:00Z",
        updated_at: "2026-06-01T00:00:00Z"
      },
      patch_artifact: {
        id: "patch_api",
        task_id: "task_api",
        local_run_id: "local_run_api",
        summary: "Docker patch",
        files_changed: ["README.md"],
        tests_run: ["python-version"],
        test_result: "passed",
        diff_text: "diff --git a/README.md b/README.md\n+patch"
      }
    }), { status: 201 });
  const client = createHttpApiClient({ baseUrl: "http://127.0.0.1:8000", fetchImpl: fetchMock });

  await client.createSandboxProfile("project_demo", {
    repo_id: "repo_github_api",
    name: "Default Docker profile",
    docker_image: "python:3.11-slim",
    patch_commands: [{
      key: "write-note",
      label: "Write note",
      command: "python scripts/write_note.py",
      timeout_seconds: 30,
      is_default: true
    }],
    test_commands: [{
      key: "python-version",
      label: "Python version",
      command: "python -V",
      timeout_seconds: 30,
      is_default: true
    }],
    allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
    network_enabled: true
  });
  await client.startCloudRun("task_api", {
    sandbox_profile_id: "sandbox_profile_api",
    patch_command_key: "write-note",
    test_command_keys: ["python-version"]
  });

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://127.0.0.1:8000/projects/project_demo/sandbox-profiles",
    expect.objectContaining({ method: "POST" })
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8000/tasks/task_api/cloud-runs",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        repo_id: "repo_github_api",
        sandbox_profile_id: "sandbox_profile_api",
        patch_command_key: "write-note",
        test_command_keys: ["python-version"]
      })
    })
  );
});
```

- [ ] **Step 2: Run client tests to verify RED**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: FAIL because sandbox profile types and methods do not exist.

- [ ] **Step 3: Add desktop client types and methods**

In `apps/desktop/src/api/client.ts`, add:

```typescript
export type SandboxCommandCard = {
  key: string;
  label: string;
  command: string;
  timeout_seconds: number;
  is_default: boolean;
};

export type SandboxProfileCard = {
  id: string;
  workspace_id?: string;
  project_id: string;
  repo_id: string;
  name: string;
  docker_image: string;
  patch_commands: SandboxCommandCard[];
  test_commands: SandboxCommandCard[];
  allowed_env_vars: string[];
  network_enabled: boolean;
  status: string;
  created_at?: string;
  updated_at?: string;
};

export type SandboxProfileInput = {
  repo_id: string;
  name: string;
  docker_image: string;
  patch_commands: SandboxCommandCard[];
  test_commands: SandboxCommandCard[];
  allowed_env_vars: string[];
  network_enabled: boolean;
};

export type CloudRunInput = {
  sandbox_profile_id?: string;
  patch_command_key?: string;
  test_command_keys?: string[];
};
```

Extend `CloudRunCard`:

```typescript
  sandbox_profile_id?: string | null;
  patch_command_key?: string | null;
  test_command_keys?: string[];
  command_results?: CommandResultCard[];
```

Extend `ConsoleApiClient`:

```typescript
  createSandboxProfile: (
    projectId: string,
    input: SandboxProfileInput
  ) => Promise<SandboxProfileCard>;
  listSandboxProfiles: (projectId: string) => Promise<SandboxProfileCard[]>;
  startCloudRun: (taskId: string, input?: CloudRunInput) => Promise<CloudRunResult>;
```

Update fake client `startCloudRun` to accept `input?: CloudRunInput` and set:

```typescript
sandbox_kind: input?.sandbox_profile_id ? "docker_local" : "fake",
sandbox_profile_id: input?.sandbox_profile_id ?? null,
patch_command_key: input?.patch_command_key ?? null,
test_command_keys: input?.test_command_keys ?? [],
command_results: []
```

Add fake methods:

```typescript
async createSandboxProfile(projectId, input) {
  return {
    id: "sandbox_profile_demo",
    workspace_id: "dev_workspace",
    project_id: projectId,
    repo_id: input.repo_id,
    name: input.name,
    docker_image: input.docker_image,
    patch_commands: input.patch_commands,
    test_commands: input.test_commands,
    allowed_env_vars: input.allowed_env_vars,
    network_enabled: input.network_enabled,
    status: "active",
    created_at: nowIso(),
    updated_at: nowIso()
  };
},
async listSandboxProfiles(_projectId) {
  return [];
}
```

Update HTTP methods:

```typescript
async createSandboxProfile(projectId, input) {
  const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/sandbox-profiles`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input)
  });
  return readJsonResponse<ApiSandboxProfile>(response, `POST /projects/${projectId}/sandbox-profiles`);
},
async listSandboxProfiles(projectId) {
  const response = await fetch(apiUrl(options.baseUrl, `/projects/${projectId}/sandbox-profiles`));
  return readJsonResponse<ApiSandboxProfile[]>(response, `GET /projects/${projectId}/sandbox-profiles`);
},
async startCloudRun(taskId, input = {}) {
  const repository = await selectGitHubRepository();
  const response = await fetch(apiUrl(options.baseUrl, `/tasks/${taskId}/cloud-runs`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      repo_id: repository.id,
      ...input
    })
  });
  return mapCloudRunResult(await readJsonResponse<ApiCloudRunResult>(response, `POST /tasks/${taskId}/cloud-runs`));
}
```

- [ ] **Step 4: Add UI tests for Docker status display**

Append to `apps/desktop/src/test/App.test.tsx`:

```typescript
it("renders docker local cloud run metadata and failure reason", async () => {
  renderAppWithClient({
    listTasks: vi.fn().mockResolvedValue([
      {
        ...taskCardFixture("Docker failed task"),
        id: "task_docker",
        cloud_run: {
          ...cloudRunFixture(),
          sandbox_kind: "docker_local",
          status: "failed",
          failure_reason: "docker_unavailable"
        }
      }
    ])
  });

  const board = await screen.findByLabelText("Task board");

  expect(within(board).getByText(/docker_local/)).toBeInTheDocument();
  expect(within(board).getByText(/docker_unavailable/)).toBeInTheDocument();
});
```

- [ ] **Step 5: Update TaskBoard display**

In `apps/desktop/src/components/TaskBoard.tsx`, change the cloud run `<dd>`:

```tsx
<dd>
  {task.cloud_run.status} via {task.cloud_run.sandbox_kind ?? "fake"} on{" "}
  <span>{task.cloud_run.head_branch}</span>
  {task.cloud_run.failure_reason ? (
    <span className="task-inline-error"> {task.cloud_run.failure_reason}</span>
  ) : null}
</dd>
```

- [ ] **Step 6: Add compact profile setup in App**

In `apps/desktop/src/App.tsx`, add state:

```typescript
const [sandboxProfileId, setSandboxProfileId] = useState<string | null>(null);
const [sandboxProfileStatus, setSandboxProfileStatus] = useState<string | null>(null);
```

After GitHub setup succeeds, call:

```typescript
const profile = await client.createSandboxProfile(project.id, {
  repo_id: repository.id,
  name: "Default Docker profile",
  docker_image: "python:3.11-slim",
  patch_commands: [
    {
      key: "write-note",
      label: "Write note",
      command: "python scripts/write_note.py",
      timeout_seconds: 300,
      is_default: true
    }
  ],
  test_commands: [
    {
      key: "python-version",
      label: "Python version",
      command: "python -V",
      timeout_seconds: 300,
      is_default: true
    }
  ],
  allowed_env_vars: ["AI_SCDC_GITHUB_TOKEN"],
  network_enabled: true
});
setSandboxProfileId(profile.id);
setSandboxProfileStatus(`Sandbox profile ready: ${profile.docker_image}`);
```

Pass profile input when starting cloud runs:

```typescript
const result = await client.startCloudRun(taskId, sandboxProfileId ? {
  sandbox_profile_id: sandboxProfileId,
  patch_command_key: "write-note",
  test_command_keys: ["python-version"]
} : undefined);
```

Render profile status near GitHub setup:

```tsx
{sandboxProfileStatus ? <p>{sandboxProfileStatus}</p> : null}
```

- [ ] **Step 7: Run desktop tests**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit Task 6**

```powershell
git add apps/desktop/src/api/client.ts apps/desktop/src/App.tsx apps/desktop/src/components/TaskBoard.tsx apps/desktop/src/fixtures/demoData.ts apps/desktop/src/styles/app.css apps/desktop/src/test/client.test.ts apps/desktop/src/test/App.test.tsx
git commit -m "feat(desktop): add docker sandbox profile controls"
```

---

## Task 7: Documentation and Phase Boundary Updates

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/specs/2026-06-01-phase-8-docker-local-sandbox-design.md` if implementation choices require wording alignment.

- [ ] **Step 1: Add README Phase 8 PowerShell smoke test**

Append after the Phase 7 smoke section in `README.md`:

```markdown
## Phase 8 Docker Local Sandbox Smoke Test

Phase 8 can run a GitHub cloud task inside a local Docker container. The fake cloud runner remains the default. Start the API with `AI_SCDC_CLOUD_RUNNER=docker_local` to use Docker. Start the API with `AI_SCDC_GITHUB_PR_ADAPTER=real` only when you also want the final approved PR step to create a real GitHub pull request.

Prerequisites:

- Docker Desktop is installed and running.
- The target GitHub repository is accessible with your PAT.
- The repository contains the command used by the sandbox profile, or you choose a command that exists in the selected Docker image and repository.

Start the API:

```powershell
$env:AI_SCDC_CLOUD_RUNNER = "docker_local"
$env:AI_SCDC_GITHUB_PR_ADAPTER = "fake"
pnpm dev:api
```

In another PowerShell session:

```powershell
$base = "http://127.0.0.1:8000"
$secureToken = Read-Host "GitHub PAT" -AsSecureString
$githubToken = [System.Net.NetworkCredential]::new("", $secureToken).Password

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 12 -Compress
}

$credential = Invoke-RestMethod `
  -Uri "$base/github-credentials" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    display_name = "Local GitHub"
    token = $githubToken
  })

$githubOwner = Read-Host "GitHub owner"
$githubRepo = Read-Host "GitHub repo"

$project = Invoke-RestMethod `
  -Uri "$base/projects" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    name = "Phase 8 smoke"
    description = "Docker local sandbox smoke test"
  })

$repository = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/github-repositories" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    name = "$githubOwner/$githubRepo"
    repo_url = "https://github.com/$githubOwner/$githubRepo"
    github_owner = $githubOwner
    github_repo = $githubRepo
    default_branch = "main"
    github_credential_id = $credential.id
  })

$profile = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/sandbox-profiles" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    repo_id = $repository.id
    name = "Default Docker profile"
    docker_image = "python:3.11-slim"
    patch_commands = @(@{
      key = "write-note"
      label = "Write note"
      command = "python - <<'PY'\nfrom pathlib import Path\np = Path('AI_SCDC_DOCKER_RUN.md')\np.write_text('# Docker local sandbox\\n', encoding='utf-8')\nPY"
      timeout_seconds = 300
      is_default = $true
    })
    test_commands = @(@{
      key = "python-version"
      label = "Python version"
      command = "python -V"
      timeout_seconds = 120
      is_default = $true
    })
    allowed_env_vars = @("AI_SCDC_GITHUB_TOKEN")
    network_enabled = $true
  })

$task = Invoke-RestMethod `
  -Uri "$base/projects/$($project.id)/tasks" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    title = "Create Docker sandbox smoke file"
    description = "Create a Docker-produced patch artifact for Phase 8 smoke testing."
    role_required = "backend"
    acceptance_criteria = @("Docker sandbox patch is produced and reviewed.")
    allowed_paths = @("AI_SCDC_DOCKER_RUN.md")
    required_tests = @("python-version")
    repo_id = $repository.id
    branch_name = $repository.default_branch
  })

$cloudRun = Invoke-RestMethod `
  -Uri "$base/tasks/$($task.id)/cloud-runs" `
  -Method Post `
  -ContentType "application/json" `
  -Body (JsonBody @{
    repo_id = $repository.id
    sandbox_profile_id = $profile.id
    patch_command_key = "write-note"
    test_command_keys = @("python-version")
  })

[ordered]@{
  cloud_run_status = $cloudRun.cloud_run.status
  sandbox_kind = $cloudRun.cloud_run.sandbox_kind
  failure_reason = $cloudRun.cloud_run.failure_reason
  files_changed = $cloudRun.patch_artifact.files_changed -join ", "
  test_result = $cloudRun.patch_artifact.test_result
}

Remove-Variable githubToken, secureToken -ErrorAction SilentlyContinue
```

Expected:

```text
cloud_run_status = patch_ready
sandbox_kind = docker_local
files_changed = AI_SCDC_DOCKER_RUN.md
test_result = passed
```
```

- [ ] **Step 2: Update architecture roadmap**

In `docs/architecture.md`, update the phase summary to state:

```markdown
Phase 8 adds the first real sandbox executor by running GitHub cloud tasks inside local Docker. The executor is still local-first and synchronous, but it establishes the sandbox profile, command whitelist, redacted logs, Docker failure codes, and artifact capture contract needed for future remote cloud workers.
```

Update the roadmap future item from "Real cloud sandbox workers" to:

```markdown
- Remote cloud sandbox workers with queueing, object storage, cancellation, and live log streaming.
```

- [ ] **Step 3: Run documentation checks**

Run:

```powershell
git diff --check
```

Expected: no output.

- [ ] **Step 4: Commit Task 7**

```powershell
git add README.md docs/architecture.md docs/superpowers/specs/2026-06-01-phase-8-docker-local-sandbox-design.md
git commit -m "docs: document phase 8 docker sandbox smoke test"
```

---

## Task 8: Full Verification and Local Smoke

**Files:**
- No code edits expected.
- Optional local verification notes can be added to `README.md` only when a command is corrected during smoke testing.

- [ ] **Step 1: Run backend focused tests**

Run:

```powershell
pytest apps/api/tests/test_sandbox_profile_api.py apps/api/tests/test_cloud_sandbox_executor.py apps/api/tests/test_docker_sandbox_executor.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_pull_request_api.py -v
```

Expected: PASS.

- [ ] **Step 2: Run full Python tests**

Run:

```powershell
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend tests and typecheck**

Run:

```powershell
pnpm test
pnpm typecheck
```

Expected: PASS.

- [ ] **Step 4: Run diff whitespace check**

Run:

```powershell
git diff --check
```

Expected: no output.

- [ ] **Step 5: Run local Docker smoke when Docker Desktop is available**

Start API:

```powershell
$env:AI_SCDC_CLOUD_RUNNER = "docker_local"
$env:AI_SCDC_GITHUB_PR_ADAPTER = "fake"
pnpm dev:api
```

In another PowerShell session, run the Phase 8 smoke commands from `README.md`.

Expected output includes:

```text
cloud_run_status = patch_ready
sandbox_kind = docker_local
failure_reason =
files_changed = AI_SCDC_DOCKER_RUN.md
test_result = passed
```

- [ ] **Step 6: Commit smoke documentation corrections when present**

If the smoke test required a README command correction, run:

```powershell
git add README.md
git commit -m "docs: refine phase 8 docker smoke commands"
```

Expected: either a commit is created for a real README correction, or no commit is made because the documented commands worked.

---

## Final Completion Checklist

- [ ] `AI_SCDC_CLOUD_RUNNER=fake` keeps all existing fake cloud tests passing.
- [ ] `AI_SCDC_CLOUD_RUNNER=docker_local` requires an active sandbox profile.
- [ ] Docker unavailable maps to `docker_unavailable`.
- [ ] Repo checkout failures map to `repo_checkout_failed`.
- [ ] Patch command failures map to `patch_command_failed`.
- [ ] Empty diffs map to `no_patch_produced`.
- [ ] Test failures map to `test_failed` while preserving patch artifact and test logs.
- [ ] Artifact capture and allowed-path failures map to `artifact_capture_failed`.
- [ ] Token values are redacted from command results, exceptions, persisted payloads, and UI-visible strings.
- [ ] The existing review, approval, human approval, and PR creation workflow works with Docker-produced patch artifacts.
- [ ] README includes a PowerShell Docker smoke test.
