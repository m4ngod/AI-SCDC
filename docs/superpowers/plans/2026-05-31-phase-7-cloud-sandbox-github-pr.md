# Phase 7 Cloud Sandbox and GitHub PR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GitHub-only cloud-run and pull-request workflow that lets a user store a GitHub PAT, register a GitHub repo, run a deterministic fake cloud sandbox, approve the resulting patch, and explicitly create a GitHub pull request after `HUMAN_APPROVAL`.

**Architecture:** Keep Phase 7 as a control-plane vertical slice. The backend owns GitHub credential metadata, GitHub repository registration, `CloudRun`, fake cloud patch generation, PR creation records, and the `PR_CREATED` task transition. To preserve the Phase 5 and Phase 6 workflow contracts, each fake cloud run creates a companion `LocalTaskRun` with `runner_kind="cloud_fake"` and a normal `PatchArtifact`; the existing test/review/approval tables continue to link through `local_run_id`.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, Pydantic v2, pytest, git CLI, GitHub REST API, React 19, TypeScript, Vite, Vitest, Testing Library.

---

## File Structure

- Modify: `apps/api/app/ai_company_api/models/entities.py`
  - Add GitHub credential status enum, repository provider fields, `CloudRun`, and `PullRequestRecord`.
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
  - Add GitHub credential/repository/cloud-run/pull-request request and read models.
- Modify: `apps/api/app/ai_company_api/services/task_state.py`
  - Add `PR_CREATED` and allow `HUMAN_APPROVAL -> PR_CREATED`.
- Create: `apps/api/app/ai_company_api/services/github_repository.py`
  - Own GitHub PAT metadata and GitHub repository registration.
- Create: `apps/api/app/ai_company_api/services/cloud_runner.py`
  - Own fake cloud run creation and companion local-run bridge.
- Create: `apps/api/app/ai_company_api/services/github_pull_request.py`
  - Own fake/real GitHub adapter boundary, idempotent PR creation, and task transition.
- Modify: `apps/api/app/ai_company_api/services/test_review_debug.py`
  - Treat `LocalTaskRun.runner_kind == "cloud_fake"` as a deterministic synthetic test execution path.
- Modify: `apps/api/app/ai_company_api/db/session.py`
  - Add SQLite upgrade helpers for repository Phase 7 columns and patch-review compatibility indexes if needed.
- Modify: `apps/api/app/ai_company_api/api/routes.py`
  - Add GitHub credential, GitHub repository, cloud-run, and pull-request routes.
- Create: `apps/api/tests/test_github_repository_api.py`
  - Cover PAT storage, no secret leakage, soft delete, and GitHub repo registration.
- Create: `apps/api/tests/test_cloud_run_api.py`
  - Cover fake cloud run, companion local-run bridge, patch artifact, synthetic test run, and review compatibility.
- Create: `apps/api/tests/test_pull_request_api.py`
  - Cover PR preconditions, fake adapter PR creation, idempotency, failure behavior, and `PR_CREATED`.
- Modify: `apps/desktop/src/api/client.ts`
  - Add Phase 7 card types, fake client methods, HTTP methods, and hydration.
- Modify: `apps/desktop/src/test/client.test.ts`
  - Cover Phase 7 fake and HTTP client behavior.
- Modify: `apps/desktop/src/App.tsx`
  - Add GitHub setup, cloud-run, and create-PR handlers and pending/error state.
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
  - Add `Run cloud`, cloud-run metadata, `Create PR`, and PR URL display.
- Modify: `apps/desktop/src/fixtures/demoData.ts`
  - Add GitHub/cloud-run/PR demo data.
- Modify: `apps/desktop/src/styles/app.css`
  - Add compact setup and PR display styles.
- Modify: `apps/desktop/src/test/App.test.tsx`
  - Cover setup, run cloud, create PR, and PR URL rendering.
- Modify: `docs/architecture.md`
  - Add Phase 7 boundary and completed roadmap item.
- Modify: `README.md`
  - Add Phase 7 smoke and verification notes.

---

## Task 1: GitHub Credentials and Repository Registration

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/github_repository.py`
- Modify: `apps/api/app/ai_company_api/services/repository.py`
- Modify: `apps/api/app/ai_company_api/db/session.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/tests/test_github_repository_api.py`

- [ ] **Step 1: Write failing GitHub credential and repository API tests**

Create `apps/api/tests/test_github_repository_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine
from ai_company_api.main import create_app
from ai_company_api.models.entities import GitHubCredential, Project, Repository


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def test_github_credential_never_returns_secret_fields(tmp_path: Path) -> None:
    client = build_client(tmp_path / "app.db")

    response = client.post(
        "/github-credentials",
        json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
    )
    list_response = client.get("/github-credentials")

    assert response.status_code == 201
    body = response.json()
    assert body["display_name"] == "Dev GitHub"
    assert body["token_last4"] == "7890"
    assert body["status"] == "active"
    assert "token" not in body
    assert "encrypted_token" not in body
    assert list_response.json() == [body]

    with Session(build_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")) as session:
        credential = session.exec(select(GitHubCredential)).one()
        assert credential.encrypted_token.startswith("dev-vault:v2:")
        assert credential.encrypted_token != "ghp_example1234567890"


def test_github_credential_delete_is_soft_delete(tmp_path: Path) -> None:
    client = build_client(tmp_path / "app.db")
    credential = client.post(
        "/github-credentials",
        json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
    ).json()

    response = client.delete(f"/github-credentials/{credential['id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert client.get("/github-credentials").json()[0]["status"] == "deleted"


def test_register_github_repository_requires_active_credential(tmp_path: Path) -> None:
    client = build_client(tmp_path / "app.db")
    project = client.post("/projects", json={"name": "GitHub project"}).json()

    response = client.post(
        f"/projects/{project['id']}/github-repositories",
        json={
            "name": "Demo remote",
            "repo_url": "https://github.com/example/demo",
            "github_owner": "example",
            "github_repo": "demo",
            "default_branch": "main",
            "github_credential_id": "github_credential_missing",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "GitHub credential not found"


def test_register_github_repository_persists_provider_metadata(tmp_path: Path) -> None:
    client = build_client(tmp_path / "app.db")
    project = client.post("/projects", json={"name": "GitHub project"}).json()
    credential = client.post(
        "/github-credentials",
        json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
    ).json()

    response = client.post(
        f"/projects/{project['id']}/github-repositories",
        json={
            "name": "Demo remote",
            "repo_url": "https://github.com/example/demo",
            "github_owner": "example",
            "github_repo": "demo",
            "default_branch": "main",
            "github_credential_id": credential["id"],
        },
    )
    list_response = client.get(f"/projects/{project['id']}/repositories")

    assert response.status_code == 201
    repository = response.json()
    assert repository["provider"] == "github"
    assert repository["local_path"] == ""
    assert repository["repo_url"] == "https://github.com/example/demo"
    assert repository["github_owner"] == "example"
    assert repository["github_repo"] == "demo"
    assert repository["github_credential_id"] == credential["id"]
    assert repository["connection_status"] == "active"
    assert list_response.json()[0]["id"] == repository["id"]

    with Session(build_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")) as session:
        persisted = session.get(Repository, repository["id"])
        assert persisted is not None
        assert persisted.provider == "github"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_github_repository_api.py -v
```

Expected: FAIL because `GitHubCredential`, schemas, service functions, and routes do not exist.

- [ ] **Step 3: Add models and schema types**

In `apps/api/app/ai_company_api/models/entities.py`, add after `ModelCredentialStatus`:

```python
class GitHubCredentialStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"
```

Extend `Repository`:

```python
    provider: str = Field(default="local", index=True)
    repo_url: str = ""
    github_owner: str | None = Field(default=None, index=True)
    github_repo: str | None = Field(default=None, index=True)
    github_credential_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="github_credential.id",
    )
    connection_status: str = Field(default="active", index=True)
```

Add `GitHubCredential` after `ModelCredential`:

```python
class GitHubCredential(SQLModel, table=True):
    __tablename__ = "github_credential"

    id: str = Field(
        default_factory=lambda: prefixed_id("github_credential"),
        primary_key=True,
    )
    workspace_id: str = Field(default="dev_workspace", index=True)
    display_name: str
    token_last4: str = ""
    encrypted_token: str
    status: GitHubCredentialStatus = Field(
        default=GitHubCredentialStatus.ACTIVE,
        sa_column=Column(
            SAEnum(
                GitHubCredentialStatus,
                name="github_credential_status",
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
                native_enum=False,
                validate_strings=True,
                create_constraint=True,
            ),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
```

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class GitHubCredentialStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class GitHubCredentialCreate(BaseModel):
    display_name: str = Field(min_length=1)
    token: SecretStr = Field(min_length=5)


class GitHubCredentialRead(BaseModel):
    id: str
    workspace_id: str
    display_name: str
    token_last4: str
    status: str
    created_at: datetime
    updated_at: datetime


class GitHubRepositoryCreate(BaseModel):
    name: str = Field(min_length=1)
    repo_url: str = Field(min_length=1)
    github_owner: str = Field(min_length=1)
    github_repo: str = Field(min_length=1)
    default_branch: str = Field(default="main", min_length=1)
    github_credential_id: str = Field(min_length=1)
```

Extend `RepositoryRead`:

```python
    provider: str
    repo_url: str
    github_owner: str | None
    github_repo: str | None
    github_credential_id: str | None
    connection_status: str
```

- [ ] **Step 4: Implement GitHub credential and repository service**

Create `apps/api/app/ai_company_api/services/github_repository.py`:

```python
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    GitHubCredential,
    GitHubCredentialStatus,
    Repository,
    utc_now,
)
from ai_company_api.schemas.api import (
    GitHubCredentialCreate,
    GitHubCredentialRead,
    GitHubRepositoryCreate,
    RepositoryRead,
)
from ai_company_api.services.repository import get_project
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault


def _credential_read(credential: GitHubCredential) -> GitHubCredentialRead:
    return GitHubCredentialRead(
        id=credential.id,
        workspace_id=credential.workspace_id,
        display_name=credential.display_name,
        token_last4=credential.token_last4,
        status=credential.status.value
        if isinstance(credential.status, GitHubCredentialStatus)
        else str(credential.status),
        created_at=credential.created_at,
        updated_at=credential.updated_at,
    )


def _repository_read(repository: Repository) -> RepositoryRead:
    return RepositoryRead(
        id=repository.id,
        workspace_id=repository.workspace_id,
        project_id=repository.project_id,
        name=repository.name,
        local_path=repository.local_path,
        default_branch=repository.default_branch,
        status=repository.status,
        provider=repository.provider,
        repo_url=repository.repo_url,
        github_owner=repository.github_owner,
        github_repo=repository.github_repo,
        github_credential_id=repository.github_credential_id,
        connection_status=repository.connection_status,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
    )


def get_github_credential(session: Session, credential_id: str) -> GitHubCredential:
    credential = session.get(GitHubCredential, credential_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="GitHub credential not found")
    return credential


def require_active_github_credential(
    session: Session,
    credential_id: str,
) -> GitHubCredential:
    credential = get_github_credential(session, credential_id)
    if credential.status != GitHubCredentialStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="GitHub credential is not active")
    return credential


def list_github_credentials(session: Session) -> list[GitHubCredentialRead]:
    statement = select(GitHubCredential).order_by(
        GitHubCredential.created_at,
        GitHubCredential.id,
    )
    return [_credential_read(credential) for credential in session.exec(statement).all()]


def create_github_credential(
    session: Session,
    data: GitHubCredentialCreate,
    vault: SecretVault | None = None,
) -> GitHubCredentialRead:
    token = data.token.get_secret_value()
    sealed = (vault or DevSecretVault()).seal(token)
    credential = GitHubCredential(
        display_name=data.display_name,
        token_last4=sealed.secret_last4,
        encrypted_token=sealed.encrypted_secret,
    )
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return _credential_read(credential)


def delete_github_credential(session: Session, credential_id: str) -> GitHubCredentialRead:
    credential = get_github_credential(session, credential_id)
    credential.status = GitHubCredentialStatus.DELETED
    credential.updated_at = utc_now()
    session.add(credential)
    session.commit()
    session.refresh(credential)
    return _credential_read(credential)


def create_github_repository(
    session: Session,
    project_id: str,
    data: GitHubRepositoryCreate,
) -> RepositoryRead:
    project = get_project(session, project_id)
    require_active_github_credential(session, data.github_credential_id)
    repository = Repository(
        project_id=project.id,
        name=data.name,
        local_path="",
        default_branch=data.default_branch,
        provider="github",
        repo_url=data.repo_url,
        github_owner=data.github_owner,
        github_repo=data.github_repo,
        github_credential_id=data.github_credential_id,
        connection_status="active",
    )
    session.add(repository)
    session.commit()
    session.refresh(repository)
    return _repository_read(repository)
```

- [ ] **Step 5: Wire repository reads to include Phase 7 fields**

In `apps/api/app/ai_company_api/services/repository.py`, update `_repository_read()`:

```python
def _repository_read(repository: ProjectRepository) -> RepositoryRead:
    return RepositoryRead(
        id=repository.id,
        workspace_id=repository.workspace_id,
        project_id=repository.project_id,
        name=repository.name,
        local_path=repository.local_path,
        default_branch=repository.default_branch,
        status=repository.status,
        provider=repository.provider,
        repo_url=repository.repo_url,
        github_owner=repository.github_owner,
        github_repo=repository.github_repo,
        github_credential_id=repository.github_credential_id,
        connection_status=repository.connection_status,
        created_at=repository.created_at,
        updated_at=repository.updated_at,
    )
```

- [ ] **Step 6: Add SQLite upgrade helper**

In `apps/api/app/ai_company_api/db/session.py`, call `_upgrade_sqlite_repository_phase_7_columns(engine)` from `init_db()` and add:

```python
def _upgrade_sqlite_repository_phase_7_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    repository_columns = {
        "provider": "VARCHAR DEFAULT 'local'",
        "repo_url": "VARCHAR DEFAULT ''",
        "github_owner": "VARCHAR",
        "github_repo": "VARCHAR",
        "github_credential_id": "VARCHAR",
        "connection_status": "VARCHAR DEFAULT 'active'",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "repository" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(repository)")).mappings()
        }
        for column_name, column_type in repository_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE repository ADD COLUMN {column_name} {column_type}")
                )
```

- [ ] **Step 7: Add routes**

In `apps/api/app/ai_company_api/api/routes.py`, import new schemas and service functions, then add:

```python
@router.get("/github-credentials", response_model=list[GitHubCredentialRead])
def get_github_credentials(session: SessionDep) -> list[GitHubCredentialRead]:
    return list_github_credentials(session)


@router.post(
    "/github-credentials",
    status_code=status.HTTP_201_CREATED,
    response_model=GitHubCredentialRead,
)
def post_github_credential(
    data: GitHubCredentialCreate,
    session: SessionDep,
) -> GitHubCredentialRead:
    return create_github_credential(session, data)


@router.delete(
    "/github-credentials/{credential_id}",
    response_model=GitHubCredentialRead,
)
def delete_github_credential_by_id(
    credential_id: str,
    session: SessionDep,
) -> GitHubCredentialRead:
    return delete_github_credential(session, credential_id)


@router.post(
    "/projects/{project_id}/github-repositories",
    status_code=status.HTTP_201_CREATED,
    response_model=RepositoryRead,
)
def post_project_github_repository(
    project_id: str,
    data: GitHubRepositoryCreate,
    session: SessionDep,
) -> RepositoryRead:
    return create_github_repository(session, project_id, data)
```

- [ ] **Step 8: Run tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_github_repository_api.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit GitHub credential and repository work**

Run:

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/github_repository.py apps/api/app/ai_company_api/services/repository.py apps/api/app/ai_company_api/db/session.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_github_repository_api.py
git commit -m "feat(api): add github credentials and repositories"
```

---

## Task 2: Fake Cloud Run and Phase 5 Compatibility

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Create: `apps/api/app/ai_company_api/services/cloud_runner.py`
- Modify: `apps/api/app/ai_company_api/services/test_review_debug.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/tests/test_cloud_run_api.py`

- [ ] **Step 1: Write failing cloud-run tests**

Create `apps/api/tests/test_cloud_run_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine
from ai_company_api.main import create_app
from ai_company_api.models.entities import CloudRun, LocalTaskRun, PatchArtifact, Project, Repository, Task
from ai_company_api.services.task_state import TaskStatus


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def create_cloud_task(session: Session) -> tuple[Project, Repository, Task]:
    project = Project(name="Cloud project")
    session.add(project)
    session.flush()
    repository = Repository(
        project_id=project.id,
        name="Demo remote",
        local_path="",
        default_branch="main",
        provider="github",
        repo_url="https://github.com/example/demo",
        github_owner="example",
        github_repo="demo",
        github_credential_id="github_credential_test",
        connection_status="active",
    )
    session.add(repository)
    session.flush()
    task = Task(
        project_id=project.id,
        title="Run fake cloud sandbox",
        role_required="backend",
        status=TaskStatus.CREATED,
        allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
        required_tests=["python -V"],
    )
    session.add(task)
    session.commit()
    session.refresh(project)
    session.refresh(repository)
    session.refresh(task)
    return project, repository, task


def test_start_cloud_run_creates_patch_artifact_and_bridge_local_run(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": repository.id})

    assert response.status_code == 201
    result = response.json()
    assert result["cloud_run"]["status"] == "patch_ready"
    assert result["cloud_run"]["sandbox_kind"] == "fake"
    assert result["cloud_run"]["head_branch"].startswith("ai-scdc/task-")
    assert result["patch_artifact"]["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert result["patch_artifact"]["test_result"] == "not_run"

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, result["cloud_run"]["id"])
        local_run = session.get(LocalTaskRun, result["cloud_run"]["local_run_id"])
        artifact = session.get(PatchArtifact, result["patch_artifact"]["id"])
        persisted_task = session.get(Task, task.id)

    assert cloud_run is not None
    assert local_run is not None
    assert local_run.runner_kind == "cloud_fake"
    assert local_run.patch_artifact_id == artifact.id
    assert cloud_run.patch_artifact_id == artifact.id
    assert persisted_task.status == TaskStatus.PATCH_READY


def test_cloud_run_rejects_cross_project_repository(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, _repository, task = create_cloud_task(session)
        other_project = Project(name="Other")
        session.add(other_project)
        session.flush()
        other_repo = Repository(
            project_id=other_project.id,
            name="Other remote",
            local_path="",
            default_branch="main",
            provider="github",
            repo_url="https://github.com/example/other",
            github_owner="example",
            github_repo="other",
            github_credential_id="github_credential_test",
            connection_status="active",
        )
        session.add(other_repo)
        session.commit()
        session.refresh(other_repo)

    response = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": other_repo.id})

    assert response.status_code == 400
    assert response.json()["detail"] == "Repository does not belong to task project"


def test_cloud_fake_patch_can_run_synthetic_tests_and_review(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    cloud_result = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": repository.id}).json()
    patch_artifact_id = cloud_result["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

    assert test_response.status_code == 201
    assert test_response.json()["test_run"]["status"] == "passed"
    assert test_response.json()["test_run"]["command_results"][0]["stdout"] == "cloud fake test passed"
    assert review_response.status_code == 201
    assert review_response.json()["review"]["verdict"] == "approved"
    assert review_response.json()["task"]["status"] == "APPROVED"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py -v
```

Expected: FAIL because `CloudRun`, cloud-run schemas, service, routes, and cloud fake test path do not exist.

- [ ] **Step 3: Add `CloudRun` model and schemas**

In `apps/api/app/ai_company_api/models/entities.py`, add after `LocalTaskRun`:

```python
class CloudRun(SQLModel, table=True):
    __tablename__ = "cloud_run"

    id: str = Field(default_factory=lambda: prefixed_id("cloud_run"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    local_run_id: str | None = Field(default=None, index=True, foreign_key="local_task_run.id")
    base_branch: str = ""
    head_branch: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    sandbox_kind: str = "fake"
    patch_artifact_id: str | None = Field(default=None, index=True)
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now)
```

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class CloudRunCreate(BaseModel):
    repo_id: str = Field(min_length=1)


class CloudRunRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    repo_id: str
    local_run_id: str | None
    base_branch: str
    head_branch: str
    status: str
    sandbox_kind: str
    patch_artifact_id: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class CloudRunResultRead(BaseModel):
    cloud_run: CloudRunRead
    patch_artifact: PatchArtifactRead | None = None
```

- [ ] **Step 4: Implement fake cloud runner service**

Create `apps/api/app/ai_company_api/services/cloud_runner.py`:

```python
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.models.entities import CloudRun, LocalTaskRun, PatchArtifact, Repository, Task, utc_now
from ai_company_api.schemas.api import CloudRunCreate, CloudRunRead, CloudRunResultRead, PatchArtifactRead
from ai_company_api.services.local_runner import _patch_artifact_read
from ai_company_api.services.repository import create_task_event, get_repository, get_task
from ai_company_api.services.task_state import InvalidTaskTransition, TaskStatus, allowed_next_statuses, validate_transition


def start_cloud_run(session: Session, task_id: str, data: CloudRunCreate) -> CloudRunResultRead:
    task = get_task(session, task_id)
    repository = get_repository(session, data.repo_id)
    _validate_github_repository_for_task(task, repository)

    event_clock = _EventClock()
    cloud_run = CloudRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        base_branch=repository.default_branch,
        head_branch="",
        status="queued",
        sandbox_kind="fake",
    )
    session.add(cloud_run)
    session.flush()
    cloud_run.head_branch = f"ai-scdc/task-{task.id}-{cloud_run.id}"
    session.add(cloud_run)

    local_run = LocalTaskRun(
        project_id=task.project_id,
        task_id=task.id,
        repo_id=repository.id,
        status="running",
        runner_kind="cloud_fake",
        base_branch=repository.default_branch,
        worktree_path=None,
    )
    session.add(local_run)
    session.flush()
    cloud_run.local_run_id = local_run.id
    cloud_run.status = "running"
    session.add(cloud_run)

    _create_cloud_event(
        session,
        event_clock,
        task.id,
        "cloud_run_started",
        {"cloud_run_id": cloud_run.id, "repo_id": repository.id, "sandbox_kind": "fake"},
    )
    _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.ASSIGNED)
    _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.IN_PROGRESS)

    artifact = PatchArtifact(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        summary="Fake cloud sandbox prepared a GitHub PR patch.",
        files_changed=["AI_SCDC_CLOUD_RUN.md"],
        tests_run=task.required_tests or ["python -V"],
        test_result="not_run",
        risks=["Fake cloud sandbox does not execute untrusted code."],
        diff_text=_fake_cloud_diff(task, cloud_run),
    )
    session.add(artifact)
    session.flush()

    local_run.status = "patch_ready"
    local_run.patch_artifact_id = artifact.id
    local_run.updated_at = utc_now()
    cloud_run.status = "patch_ready"
    cloud_run.patch_artifact_id = artifact.id
    cloud_run.updated_at = utc_now()
    task.repo_id = repository.id
    task.branch_name = cloud_run.head_branch
    task.worktree_ref = f"cloud://fake/{cloud_run.id}"

    _create_cloud_event(
        session,
        event_clock,
        task.id,
        "cloud_run_patch_ready",
        {
            "cloud_run_id": cloud_run.id,
            "patch_artifact_id": artifact.id,
            "head_branch": cloud_run.head_branch,
        },
    )
    _transition_task_for_cloud_runner(session, event_clock, task, TaskStatus.PATCH_READY)
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


def _validate_github_repository_for_task(task: Task, repository: Repository) -> None:
    if repository.project_id != task.project_id:
        raise HTTPException(status_code=400, detail="Repository does not belong to task project")
    if repository.provider != "github":
        raise HTTPException(status_code=400, detail="Cloud runs require a GitHub repository")
    if repository.connection_status != "active":
        raise HTTPException(status_code=400, detail="GitHub repository is not active")


def _fake_cloud_diff(task: Task, cloud_run: CloudRun) -> str:
    return (
        "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/AI_SCDC_CLOUD_RUN.md\n"
        "@@ -0,0 +1,4 @@\n"
        "+# AI SCDC Cloud Run\n"
        f"+Task: {task.title}\n"
        f"+Cloud run: {cloud_run.id}\n"
        "+Generated by the deterministic fake cloud sandbox.\n"
    )


def _cloud_run_read(cloud_run: CloudRun) -> CloudRunRead:
    return CloudRunRead(
        id=cloud_run.id,
        workspace_id=cloud_run.workspace_id,
        project_id=cloud_run.project_id,
        task_id=cloud_run.task_id,
        repo_id=cloud_run.repo_id,
        local_run_id=cloud_run.local_run_id,
        base_branch=cloud_run.base_branch,
        head_branch=cloud_run.head_branch,
        status=cloud_run.status,
        sandbox_kind=cloud_run.sandbox_kind,
        patch_artifact_id=cloud_run.patch_artifact_id,
        failure_reason=cloud_run.failure_reason,
        created_at=cloud_run.created_at,
        updated_at=cloud_run.updated_at,
    )


class _EventClock:
    def __init__(self) -> None:
        self._base = utc_now()
        self._offset = 0

    def next(self) -> datetime:
        self._offset += 1
        return self._base + timedelta(microseconds=self._offset)


def _create_cloud_event(
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


def _transition_task_for_cloud_runner(
    session: Session,
    event_clock: _EventClock,
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
    _create_cloud_event(
        session,
        event_clock,
        task.id,
        "task_transitioned",
        {"from_status": current_status.value, "to_status": next_status.value},
    )
```

- [ ] **Step 5: Add synthetic tests for `cloud_fake` local runs**

In `apps/api/app/ai_company_api/services/test_review_debug.py`, locate the helper that loads a local run and rejects missing worktree paths. Add a branch before command execution:

```python
if local_run.runner_kind == "cloud_fake":
    return _start_cloud_fake_test_run(session, artifact, task, local_run)
```

Implement the helper in the same service:

```python
def _start_cloud_fake_test_run(
    session: Session,
    artifact: PatchArtifact,
    task: Task,
    local_run: LocalTaskRun,
) -> PatchTestRunResultRead:
    event_clock = _EventClock()
    commands = task.required_tests or artifact.tests_run or ["python -V"]
    test_run = LocalTestRun(
        project_id=task.project_id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        status="passed",
        commands=commands,
        command_results=[
            {
                "command": command,
                "exit_code": 0,
                "stdout": "cloud fake test passed",
                "stderr": "",
                "duration_ms": 1,
            }
            for command in commands
        ],
        failure_reason=None,
        completed_at=utc_now(),
    )
    session.add(test_run)
    session.flush()
    artifact.test_result = "passed"
    artifact.tests_run = commands
    session.add(artifact)
    _create_test_event(
        session,
        event_clock,
        task.id,
        "patch_tests_passed",
        {
            "test_run_id": test_run.id,
            "patch_artifact_id": artifact.id,
            "runner_kind": local_run.runner_kind,
        },
    )
    _transition_task_for_tests(session, event_clock, task, TaskStatus.SELF_TESTING)
    _transition_task_for_tests(session, event_clock, task, TaskStatus.REVIEWING)
    session.commit()
    session.refresh(test_run)
    session.refresh(artifact)
    session.refresh(task)
    return PatchTestRunResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        test_run=_test_run_read(test_run),
        debug_attempt=None,
    )
```

Use the existing helper names from `test_review_debug.py`: `_EventClock`, `_create_workflow_event`, `_transition_task_for_workflow`, `_complete_test_run`, `_task_read`, `_patch_artifact_read`, and `_test_run_read`.

- [ ] **Step 6: Add routes**

In `apps/api/app/ai_company_api/api/routes.py`, add:

```python
@router.post(
    "/tasks/{task_id}/cloud-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=CloudRunResultRead,
)
def post_task_cloud_run(
    task_id: str,
    data: CloudRunCreate,
    session: SessionDep,
) -> CloudRunResultRead:
    return start_cloud_run(session, task_id, data)


@router.get("/tasks/{task_id}/cloud-runs", response_model=list[CloudRunRead])
def get_task_cloud_runs(task_id: str, session: SessionDep) -> list[CloudRunRead]:
    return list_cloud_runs(session, task_id)


@router.get("/cloud-runs/{cloud_run_id}", response_model=CloudRunRead)
def get_cloud_run_by_id(cloud_run_id: str, session: SessionDep) -> CloudRunRead:
    return get_cloud_run_read(session, cloud_run_id)
```

- [ ] **Step 7: Run tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_cloud_run_api.py apps/api/tests/test_test_review_debug_api.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit cloud-run work**

Run:

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/cloud_runner.py apps/api/app/ai_company_api/services/test_review_debug.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_cloud_run_api.py
git commit -m "feat(api): add fake cloud runs"
```

---

## Task 3: Pull Request Records and GitHub Adapter Boundary

**Files:**
- Modify: `apps/api/app/ai_company_api/models/entities.py`
- Modify: `apps/api/app/ai_company_api/schemas/api.py`
- Modify: `apps/api/app/ai_company_api/services/task_state.py`
- Create: `apps/api/app/ai_company_api/services/github_pull_request.py`
- Modify: `apps/api/app/ai_company_api/api/routes.py`
- Create: `apps/api/tests/test_pull_request_api.py`
- Modify: `apps/api/tests/test_task_state.py`

- [ ] **Step 1: Write failing pull-request tests**

Create `apps/api/tests/test_pull_request_api.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    GitHubCredential,
    LocalTaskRun,
    LocalTestRun,
    PatchApproval,
    PatchArtifact,
    PatchReview,
    Project,
    Repository,
    Task,
)
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.task_state import TaskStatus


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def create_human_approved_cloud_patch(session: Session, *, task_status: TaskStatus = TaskStatus.HUMAN_APPROVAL) -> tuple[Task, PatchApproval]:
    project = Project(name="PR project")
    session.add(project)
    session.flush()
    sealed = DevSecretVault().seal("ghp_example1234567890")
    credential = GitHubCredential(
        display_name="Dev GitHub",
        token_last4=sealed.secret_last4,
        encrypted_token=sealed.encrypted_secret,
    )
    session.add(credential)
    session.flush()
    repository = Repository(
        project_id=project.id,
        name="Demo remote",
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
    session.flush()
    task = Task(
        project_id=project.id,
        title="Create PR",
        role_required="backend",
        status=task_status,
        repo_id=repository.id,
        branch_name="ai-scdc/task-create-pr",
        worktree_ref="cloud://fake/cloud_run_demo",
        allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
        required_tests=["python -V"],
    )
    session.add(task)
    session.flush()
    local_run = LocalTaskRun(
        project_id=project.id,
        task_id=task.id,
        repo_id=repository.id,
        status="patch_ready",
        runner_kind="cloud_fake",
        base_branch="main",
        patch_artifact_id=None,
    )
    session.add(local_run)
    session.flush()
    cloud_run = CloudRun(
        project_id=project.id,
        task_id=task.id,
        repo_id=repository.id,
        local_run_id=local_run.id,
        base_branch="main",
        head_branch="ai-scdc/task-create-pr",
        status="patch_ready",
        sandbox_kind="fake",
    )
    session.add(cloud_run)
    session.flush()
    artifact = PatchArtifact(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        summary="Fake cloud patch.",
        files_changed=["AI_SCDC_CLOUD_RUN.md"],
        tests_run=["python -V"],
        test_result="passed",
        risks=[],
        diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\nnew file mode 100644\n--- /dev/null\n+++ b/AI_SCDC_CLOUD_RUN.md\n@@ -0,0 +1 @@\n+hello\n",
    )
    session.add(artifact)
    session.flush()
    local_run.patch_artifact_id = artifact.id
    cloud_run.patch_artifact_id = artifact.id
    test_run = LocalTestRun(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        status="passed",
        commands=["python -V"],
        command_results=[{"command": "python -V", "exit_code": 0, "stdout": "Python", "stderr": "", "duration_ms": 1}],
        failure_reason=None,
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
        verdict="approved",
        issues=[],
        required_changes=[],
    )
    session.add(review)
    session.flush()
    approval = PatchApproval(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
        review_id=review.id,
        status="approved",
        approved_by="dev_user",
        merge_instructions="Ready for PR.",
    )
    session.add(approval)
    session.commit()
    session.refresh(task)
    session.refresh(approval)
    return task, approval


def test_create_pull_request_requires_human_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _task, approval = create_human_approved_cloud_patch(session, task_status=TaskStatus.MERGE_READY)

    response = client.post(f"/patch-approvals/{approval.id}/pull-requests")

    assert response.status_code == 400
    assert response.json()["detail"]["expected_status"] == "HUMAN_APPROVAL"


def test_create_pull_request_uses_fake_adapter_and_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        task, approval = create_human_approved_cloud_patch(session)

    first = client.post(f"/patch-approvals/{approval.id}/pull-requests")
    second = client.post(f"/patch-approvals/{approval.id}/pull-requests")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["pull_request"]["id"] == first.json()["pull_request"]["id"]
    assert first.json()["task"]["status"] == "PR_CREATED"
    assert first.json()["pull_request"]["github_pr_url"] == "https://github.com/example/demo/pull/1"
    events = client.get(f"/tasks/{task.id}/events").json()
    assert [event["event_type"] for event in events].count("pull_request_created") == 1


def test_list_pull_requests_for_patch_artifact(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _task, approval = create_human_approved_cloud_patch(session)

    created = client.post(f"/patch-approvals/{approval.id}/pull-requests").json()
    response = client.get(f"/patch-artifacts/{approval.patch_artifact_id}/pull-requests")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [created["pull_request"]["id"]]
```

- [ ] **Step 2: Add task-state failing test**

In `apps/api/tests/test_task_state.py`, add:

```python
def test_human_approval_can_create_pr_for_system_actor() -> None:
    assert (
        validate_transition(
            TaskStatus.HUMAN_APPROVAL,
            TaskStatus.PR_CREATED,
            actor_type="system",
        )
        == TaskStatus.PR_CREATED
    )
```

Run:

```powershell
pytest apps/api/tests/test_pull_request_api.py apps/api/tests/test_task_state.py -v
```

Expected: FAIL because `PR_CREATED`, `PullRequestRecord`, service, schemas, and routes do not exist.

- [ ] **Step 3: Add `PR_CREATED` state**

In `apps/api/app/ai_company_api/services/task_state.py`:

```python
    PR_CREATED = "PR_CREATED"
```

Update transitions:

```python
    TaskStatus.HUMAN_APPROVAL: {
        TaskStatus.PR_CREATED,
        TaskStatus.MERGED,
        TaskStatus.CLOSED,
    },
    TaskStatus.PR_CREATED: {
        TaskStatus.CLOSED,
        TaskStatus.CANCELLED,
    },
```

Do not add `PR_CREATED` to `TERMINAL_STATUSES`.

- [ ] **Step 4: Add pull-request model and schemas**

In `apps/api/app/ai_company_api/models/entities.py`, add after `PatchApproval`:

```python
class PullRequestRecord(SQLModel, table=True):
    __tablename__ = "pull_request_record"
    __table_args__ = (
        UniqueConstraint(
            "patch_approval_id",
            name="uq_pull_request_record_patch_approval_id",
        ),
    )

    id: str = Field(default_factory=lambda: prefixed_id("pull_request"), primary_key=True)
    workspace_id: str = Field(default="dev_workspace", index=True)
    project_id: str = Field(index=True, foreign_key="project.id")
    task_id: str = Field(index=True, foreign_key="task.id")
    repo_id: str = Field(index=True, foreign_key="repository.id")
    patch_artifact_id: str = Field(index=True, foreign_key="patch_artifact.id")
    patch_approval_id: str = Field(index=True, foreign_key="patch_approval.id")
    cloud_run_id: str | None = Field(default=None, index=True, foreign_key="cloud_run.id")
    head_branch: str
    base_branch: str
    github_pr_number: int
    github_pr_url: str
    status: str = Field(default="created", index=True)
    created_by: str = "dev_user"
    created_at: datetime = Field(default_factory=utc_now, index=True)
```

In `apps/api/app/ai_company_api/schemas/api.py`, add:

```python
class PullRequestRead(BaseModel):
    id: str
    workspace_id: str
    project_id: str
    task_id: str
    repo_id: str
    patch_artifact_id: str
    patch_approval_id: str
    cloud_run_id: str | None
    head_branch: str
    base_branch: str
    github_pr_number: int
    github_pr_url: str
    status: str
    created_by: str
    created_at: datetime


class PullRequestResultRead(BaseModel):
    task: TaskRead
    patch_artifact: PatchArtifactRead
    approval: PatchApprovalRead
    pull_request: PullRequestRead
```

- [ ] **Step 5: Implement GitHub adapter and PR service**

Create `apps/api/app/ai_company_api/services/github_pull_request.py`:

```python
from dataclasses import dataclass
import json
import subprocess
import tempfile
from pathlib import Path
from urllib import request

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import CloudRun, PatchApproval, PatchArtifact, PullRequestRecord, Repository, Task, utc_now
from ai_company_api.schemas.api import PatchApprovalRead, PatchArtifactRead, PullRequestRead, PullRequestResultRead, TaskRead
from ai_company_api.services.github_repository import get_github_credential, require_active_github_credential
from ai_company_api.services.local_runner import _patch_artifact_read
from ai_company_api.services.patch_approval import _approval_read
from ai_company_api.services.repository import create_task_event, get_repository, get_task
from ai_company_api.services.secret_vault import DevSecretVault, SecretVault
from ai_company_api.services.task_state import InvalidTaskTransition, TaskStatus, allowed_next_statuses, validate_transition


@dataclass(frozen=True)
class CreatedPullRequest:
    number: int
    url: str


class GitHubPullRequestAdapter:
    def create_pull_request(
        self,
        *,
        repository: Repository,
        token: str,
        patch_artifact: PatchArtifact,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> CreatedPullRequest:
        with tempfile.TemporaryDirectory(prefix="ai-scdc-github-pr-") as tmp_dir:
            repo_path = Path(tmp_dir) / "repo"
            self._run_git(["clone", repository.repo_url, str(repo_path)], token=token)
            self._run_git(["-C", str(repo_path), "checkout", "-b", head_branch, f"origin/{base_branch}"], token=token)
            patch_file = Path(tmp_dir) / "patch.diff"
            patch_file.write_text(patch_artifact.diff_text, encoding="utf-8")
            self._run_git(["-C", str(repo_path), "apply", "--whitespace=nowarn", str(patch_file)], token=token)
            self._run_git(["-C", str(repo_path), "add", "."], token=token)
            self._run_git(["-C", str(repo_path), "-c", "user.email=ai-scdc@example.local", "-c", "user.name=AI SCDC", "commit", "-m", title], token=token)
            self._run_git(["-C", str(repo_path), "push", "origin", f"HEAD:{head_branch}"], token=token)
        return self._create_github_pr(
            repository=repository,
            token=token,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )

    def _run_git(self, args: list[str], *, token: str) -> None:
        result = subprocess.run(
            ["git", "-c", f"http.extraheader=AUTHORIZATION: bearer {token}", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(_redact_secret(result.stderr or result.stdout, token))

    def _create_github_pr(
        self,
        *,
        repository: Repository,
        token: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> CreatedPullRequest:
        payload = json.dumps(
            {"title": title, "head": head_branch, "base": base_branch, "body": body}
        ).encode("utf-8")
        github_request = request.Request(
            f"https://api.github.com/repos/{repository.github_owner}/{repository.github_repo}/pulls",
            data=payload,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "ai-scdc-dev",
            },
            method="POST",
        )
        try:
            with request.urlopen(github_request, timeout=30) as response:
                body_json = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(_redact_secret(str(exc), token)) from exc
        return CreatedPullRequest(number=int(body_json["number"]), url=str(body_json["html_url"]))


class FakeGitHubPullRequestAdapter:
    def create_pull_request(
        self,
        *,
        repository: Repository,
        token: str,
        patch_artifact: PatchArtifact,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> CreatedPullRequest:
        return CreatedPullRequest(
            number=1,
            url=f"https://github.com/{repository.github_owner}/{repository.github_repo}/pull/1",
        )


PULL_REQUEST_ADAPTER = FakeGitHubPullRequestAdapter()
SECRET_VAULT: SecretVault = DevSecretVault()


def create_pull_request_for_approval(
    session: Session,
    approval_id: str,
) -> tuple[PullRequestResultRead, int]:
    approval = session.get(PatchApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Patch approval not found")
    existing = _existing_pull_request(session, approval.id)
    if existing is not None:
        return _pull_request_result_read(session, approval, existing), 200

    task = get_task(session, approval.task_id)
    if TaskStatus(task.status) != TaskStatus.HUMAN_APPROVAL:
        current_status = TaskStatus(task.status)
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Task must be HUMAN_APPROVAL before pull request creation",
                "current_status": current_status.value,
                "expected_status": TaskStatus.HUMAN_APPROVAL.value,
                "allowed_next_statuses": allowed_next_statuses(current_status),
            },
        )
    artifact = session.get(PatchArtifact, approval.patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    cloud_run = _cloud_run_for_artifact(session, artifact.id)
    if cloud_run is None:
        raise HTTPException(status_code=400, detail="Pull request creation requires a cloud run")
    repository = get_repository(session, cloud_run.repo_id)
    if repository.provider != "github":
        raise HTTPException(status_code=400, detail="Pull request creation requires a GitHub repository")
    credential = require_active_github_credential(session, repository.github_credential_id or "")
    token = SECRET_VAULT.open(credential.encrypted_token)

    try:
        created = PULL_REQUEST_ADAPTER.create_pull_request(
            repository=repository,
            token=token,
            patch_artifact=artifact,
            head_branch=cloud_run.head_branch,
            base_branch=cloud_run.base_branch,
            title=f"{task.title}",
            body=f"Created by AI SCDC from patch artifact {artifact.id}.",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub pull request creation failed: {exc}") from exc

    try:
        pull_request = PullRequestRecord(
            project_id=task.project_id,
            task_id=task.id,
            repo_id=repository.id,
            patch_artifact_id=artifact.id,
            patch_approval_id=approval.id,
            cloud_run_id=cloud_run.id,
            head_branch=cloud_run.head_branch,
            base_branch=cloud_run.base_branch,
            github_pr_number=created.number,
            github_pr_url=created.url,
            status="created",
            created_by="dev_user",
        )
        session.add(pull_request)
        session.flush()
        _transition_task_for_pr(session, task, TaskStatus.PR_CREATED)
        create_task_event(
            session,
            task.id,
            "pull_request_created",
            "system",
            "github_pull_request",
            {
                "pull_request_id": pull_request.id,
                "github_pr_number": pull_request.github_pr_number,
                "github_pr_url": pull_request.github_pr_url,
                "head_branch": pull_request.head_branch,
                "base_branch": pull_request.base_branch,
            },
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        existing = _existing_pull_request(session, approval.id)
        if existing is not None:
            return _pull_request_result_read(session, approval, existing), 200
        raise HTTPException(status_code=409, detail="Pull request record uniqueness conflict") from exc

    session.refresh(pull_request)
    return _pull_request_result_read(session, approval, pull_request), 201


def list_pull_requests_for_patch_artifact(
    session: Session,
    patch_artifact_id: str,
) -> list[PullRequestRead]:
    artifact = session.get(PatchArtifact, patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    statement = (
        select(PullRequestRecord)
        .where(PullRequestRecord.patch_artifact_id == patch_artifact_id)
        .order_by(PullRequestRecord.created_at, PullRequestRecord.id)
    )
    return [_pull_request_read(record) for record in session.exec(statement).all()]


def get_pull_request(session: Session, pull_request_id: str) -> PullRequestRead:
    pull_request = session.get(PullRequestRecord, pull_request_id)
    if pull_request is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    return _pull_request_read(pull_request)


def _existing_pull_request(
    session: Session,
    patch_approval_id: str,
) -> PullRequestRecord | None:
    statement = (
        select(PullRequestRecord)
        .where(PullRequestRecord.patch_approval_id == patch_approval_id)
        .order_by(PullRequestRecord.created_at, PullRequestRecord.id)
        .limit(1)
    )
    return session.exec(statement).first()


def _cloud_run_for_artifact(
    session: Session,
    patch_artifact_id: str,
) -> CloudRun | None:
    statement = (
        select(CloudRun)
        .where(CloudRun.patch_artifact_id == patch_artifact_id)
        .order_by(CloudRun.created_at.desc(), CloudRun.id.desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _transition_task_for_pr(
    session: Session,
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
    create_task_event(
        session,
        task.id,
        "task_transitioned",
        "system",
        "github_pull_request",
        {"from_status": current_status.value, "to_status": next_status.value},
    )


def _pull_request_result_read(
    session: Session,
    approval: PatchApproval,
    pull_request: PullRequestRecord,
) -> PullRequestResultRead:
    task = get_task(session, approval.task_id)
    artifact = session.get(PatchArtifact, approval.patch_artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Patch artifact not found")
    return PullRequestResultRead(
        task=_task_read(task),
        patch_artifact=_patch_artifact_read(artifact),
        approval=_approval_read(approval),
        pull_request=_pull_request_read(pull_request),
    )


def _pull_request_read(pull_request: PullRequestRecord) -> PullRequestRead:
    return PullRequestRead(
        id=pull_request.id,
        workspace_id=pull_request.workspace_id,
        project_id=pull_request.project_id,
        task_id=pull_request.task_id,
        repo_id=pull_request.repo_id,
        patch_artifact_id=pull_request.patch_artifact_id,
        patch_approval_id=pull_request.patch_approval_id,
        cloud_run_id=pull_request.cloud_run_id,
        head_branch=pull_request.head_branch,
        base_branch=pull_request.base_branch,
        github_pr_number=pull_request.github_pr_number,
        github_pr_url=pull_request.github_pr_url,
        status=pull_request.status,
        created_by=pull_request.created_by,
        created_at=pull_request.created_at,
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


def _redact_secret(value: str, token: str) -> str:
    redacted = value.replace(token, "[redacted]")
    if len(token) >= 4:
        redacted = redacted.replace(token[-4:], "[redacted-last4]")
    return redacted
```

- [ ] **Step 6: Add routes**

In `apps/api/app/ai_company_api/api/routes.py`, add:

```python
@router.post(
    "/patch-approvals/{approval_id}/pull-requests",
    response_model=PullRequestResultRead,
)
def post_patch_approval_pull_request(
    approval_id: str,
    response: Response,
    session: SessionDep,
) -> PullRequestResultRead:
    result, status_code = create_pull_request_for_approval(session, approval_id)
    response.status_code = status_code
    return result


@router.get(
    "/patch-artifacts/{patch_artifact_id}/pull-requests",
    response_model=list[PullRequestRead],
)
def get_patch_artifact_pull_requests(
    patch_artifact_id: str,
    session: SessionDep,
) -> list[PullRequestRead]:
    return list_pull_requests_for_patch_artifact(session, patch_artifact_id)


@router.get("/pull-requests/{pull_request_id}", response_model=PullRequestRead)
def get_pull_request_by_id(
    pull_request_id: str,
    session: SessionDep,
) -> PullRequestRead:
    return get_pull_request(session, pull_request_id)
```

- [ ] **Step 7: Run tests to verify GREEN**

Run:

```powershell
pytest apps/api/tests/test_pull_request_api.py apps/api/tests/test_task_state.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit PR backend work**

Run:

```powershell
git add apps/api/app/ai_company_api/models/entities.py apps/api/app/ai_company_api/schemas/api.py apps/api/app/ai_company_api/services/task_state.py apps/api/app/ai_company_api/services/github_pull_request.py apps/api/app/ai_company_api/api/routes.py apps/api/tests/test_pull_request_api.py apps/api/tests/test_task_state.py
git commit -m "feat(api): add github pull request workflow"
```

---

## Task 4: Desktop API Contract and Hydration

**Files:**
- Modify: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/test/client.test.ts`

- [ ] **Step 1: Write failing desktop client tests**

In `apps/desktop/src/test/client.test.ts`, add tests:

```typescript
it("fake client creates github credentials and repositories", async () => {
  const credential = await fakeApiClient.createGitHubCredential({
    display_name: "Dev GitHub",
    token: "ghp_example1234567890"
  });
  const repository = await fakeApiClient.createGitHubRepository({
    name: "Demo remote",
    repo_url: "https://github.com/example/demo",
    github_owner: "example",
    github_repo: "demo",
    default_branch: "main",
    github_credential_id: credential.id
  });

  expect(credential).toMatchObject({
    display_name: "Dev GitHub",
    token_last4: "7890",
    status: "active"
  });
  expect(repository).toMatchObject({
    provider: "github",
    github_owner: "example",
    github_repo: "demo"
  });
});

it("fake client runs cloud and creates pull requests", async () => {
  const cloud = await fakeApiClient.startCloudRun("task_demo_created");
  const approval = await fakeApiClient.approvePatch(cloud.patch_artifact!.id);
  const human = await fakeApiClient.requestHumanApproval(approval.approval.id);
  const pullRequest = await fakeApiClient.createPullRequest(human.approval.id);

  expect(cloud.cloud_run.status).toBe("patch_ready");
  expect(pullRequest.task.status).toBe("PR_CREATED");
  expect(pullRequest.pull_request.github_pr_url).toBe("https://github.com/example/demo/pull/1");
});
```

Add HTTP tests:

```typescript
it("HTTP client creates github credentials and github repositories", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse(
        {
          id: "github_credential_api",
          workspace_id: "workspace_api",
          display_name: "Dev GitHub",
          token_last4: "7890",
          status: "active",
          created_at: "2026-05-31T00:00:00Z",
          updated_at: "2026-05-31T00:00:00Z"
        },
        { status: 201 }
      )
    )
    .mockResolvedValueOnce(
      jsonResponse(
        {
          id: "repo_github_api",
          workspace_id: "workspace_api",
          project_id: "project_demo",
          name: "Demo remote",
          local_path: "",
          default_branch: "main",
          status: "active",
          provider: "github",
          repo_url: "https://github.com/example/demo",
          github_owner: "example",
          github_repo: "demo",
          github_credential_id: "github_credential_api",
          connection_status: "active",
          created_at: "2026-05-31T00:00:00Z",
          updated_at: "2026-05-31T00:00:00Z"
        },
        { status: 201 }
      )
    );
  vi.stubGlobal("fetch", fetchMock);

  const client = createHttpApiClient({
    baseUrl: "http://127.0.0.1:8000/",
    projectId: "project_demo"
  });
  const credential = await client.createGitHubCredential({
    display_name: "Dev GitHub",
    token: "ghp_example1234567890"
  });
  const repository = await client.createGitHubRepository({
    name: "Demo remote",
    repo_url: "https://github.com/example/demo",
    github_owner: "example",
    github_repo: "demo",
    default_branch: "main",
    github_credential_id: credential.id
  });

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://127.0.0.1:8000/github-credentials",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({
        display_name: "Dev GitHub",
        token: "ghp_example1234567890"
      })
    })
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8000/projects/project_demo/github-repositories",
    expect.objectContaining({ method: "POST" })
  );
  expect(repository).toMatchObject({
    id: "repo_github_api",
    provider: "github",
    github_owner: "example",
    github_repo: "demo"
  });
});

it("HTTP client starts cloud runs and creates pull requests", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse(
        {
          cloud_run: {
            id: "cloud_run_api",
            workspace_id: "workspace_api",
            project_id: "project_demo",
            task_id: "task_api",
            repo_id: "repo_github_api",
            local_run_id: "local_run_api",
            base_branch: "main",
            head_branch: "ai-scdc/task-api",
            status: "patch_ready",
            sandbox_kind: "fake",
            patch_artifact_id: "patch_api",
            failure_reason: null,
            created_at: "2026-05-31T00:00:00Z",
            updated_at: "2026-05-31T00:00:01Z"
          },
          patch_artifact: {
            id: "patch_api",
            task_id: "task_api",
            local_run_id: "local_run_api",
            summary: "Fake cloud patch.",
            files_changed: ["AI_SCDC_CLOUD_RUN.md"],
            tests_run: ["python -V"],
            test_result: "not_run",
            risks: [],
            diff_text: "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md"
          }
        },
        { status: 201 }
      )
    )
    .mockResolvedValueOnce(
      jsonResponse(
        {
          task: {
            id: "task_api",
            title: "Create PR",
            status: "PR_CREATED",
            role_required: "backend",
            updated_at: "2026-05-31T00:01:00Z"
          },
          patch_artifact: {
            id: "patch_api",
            task_id: "task_api",
            local_run_id: "local_run_api",
            summary: "Fake cloud patch.",
            files_changed: ["AI_SCDC_CLOUD_RUN.md"],
            tests_run: ["python -V"],
            test_result: "passed",
            risks: [],
            diff_text: "diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md"
          },
          approval: {
            id: "patch_approval_api",
            task_id: "task_api",
            local_run_id: "local_run_api",
            patch_artifact_id: "patch_api",
            review_id: "review_api",
            status: "approved",
            approved_by: "dev_user",
            merge_instructions: "Ready for PR.",
            created_at: "2026-05-31T00:00:30Z"
          },
          pull_request: {
            id: "pull_request_api",
            task_id: "task_api",
            repo_id: "repo_github_api",
            patch_artifact_id: "patch_api",
            patch_approval_id: "patch_approval_api",
            cloud_run_id: "cloud_run_api",
            head_branch: "ai-scdc/task-api",
            base_branch: "main",
            github_pr_number: 1,
            github_pr_url: "https://github.com/example/demo/pull/1",
            status: "created",
            created_by: "dev_user",
            created_at: "2026-05-31T00:01:00Z"
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
  const cloudRun = await client.startCloudRun("task_api");
  const pullRequest = await client.createPullRequest("patch_approval_api");

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://127.0.0.1:8000/tasks/task_api/cloud-runs",
    expect.objectContaining({ method: "POST" })
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8000/patch-approvals/patch_approval_api/pull-requests",
    expect.objectContaining({ method: "POST" })
  );
  expect(cloudRun.cloud_run.head_branch).toBe("ai-scdc/task-api");
  expect(pullRequest.task.status).toBe("PR_CREATED");
  expect(pullRequest.pull_request.github_pr_url).toBe("https://github.com/example/demo/pull/1");
});
```

Add this hydration assertion to the existing persisted workflow hydration test after the approval response mock:

```typescript
.mockResolvedValueOnce(
  jsonResponse([
    {
      id: "cloud_run_api",
      workspace_id: "workspace_api",
      project_id: "project_demo",
      task_id: "task_api",
      repo_id: "repo_github_api",
      local_run_id: "local_run_api",
      base_branch: "main",
      head_branch: "ai-scdc/task-api",
      status: "patch_ready",
      sandbox_kind: "fake",
      patch_artifact_id: "patch_api",
      failure_reason: null,
      created_at: "2026-05-31T00:00:00Z",
      updated_at: "2026-05-31T00:00:01Z"
    }
  ])
)
.mockResolvedValueOnce(
  jsonResponse([
    {
      id: "pull_request_api",
      workspace_id: "workspace_api",
      project_id: "project_demo",
      task_id: "task_api",
      repo_id: "repo_github_api",
      patch_artifact_id: "patch_api",
      patch_approval_id: "patch_approval_api",
      cloud_run_id: "cloud_run_api",
      head_branch: "ai-scdc/task-api",
      base_branch: "main",
      github_pr_number: 1,
      github_pr_url: "https://github.com/example/demo/pull/1",
      status: "created",
      created_by: "dev_user",
      created_at: "2026-05-31T00:01:00Z"
    }
  ])
)
```

Add expectations:

```typescript
expect(tasks[0]).toMatchObject({
  cloud_run: {
    id: "cloud_run_api",
    head_branch: "ai-scdc/task-api"
  },
  pull_request: {
    id: "pull_request_api",
    github_pr_url: "https://github.com/example/demo/pull/1"
  }
});
```

- [ ] **Step 2: Run client tests to verify RED**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
```

Expected: FAIL because client types and methods do not exist.

- [ ] **Step 3: Add client types**

In `apps/desktop/src/api/client.ts`, add:

```typescript
export type GitHubCredentialCard = {
  id: string;
  workspace_id: string;
  display_name: string;
  token_last4: string;
  status: string;
  created_at: string;
  updated_at: string;
};

export type GitHubCredentialInput = {
  display_name: string;
  token: string;
};

export type GitHubRepositoryInput = {
  name: string;
  repo_url: string;
  github_owner: string;
  github_repo: string;
  default_branch: string;
  github_credential_id: string;
};

export type CloudRunCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  repo_id: string;
  local_run_id?: string | null;
  base_branch: string;
  head_branch: string;
  status: string;
  sandbox_kind: string;
  patch_artifact_id?: string | null;
  failure_reason?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type PullRequestCard = {
  id: string;
  workspace_id?: string;
  project_id?: string;
  task_id: string;
  repo_id: string;
  patch_artifact_id: string;
  patch_approval_id: string;
  cloud_run_id?: string | null;
  head_branch: string;
  base_branch: string;
  github_pr_number: number;
  github_pr_url: string;
  status: string;
  created_by: string;
  created_at: string;
};

export type CloudRunResult = {
  cloud_run: CloudRunCard;
  patch_artifact?: PatchArtifactCard | null;
};

export type PullRequestResult = {
  task: TaskCard;
  patch_artifact: PatchArtifactCard;
  approval: PatchApprovalCard;
  pull_request: PullRequestCard;
};
```

Extend `RepositoryCard` with optional Phase 7 fields and `TaskCard` with:

```typescript
  cloud_run?: CloudRunCard;
  pull_request?: PullRequestCard;
```

Extend `ConsoleApiClient`:

```typescript
  createGitHubCredential: (input: GitHubCredentialInput) => Promise<GitHubCredentialCard>;
  listGitHubCredentials: () => Promise<GitHubCredentialCard[]>;
  createGitHubRepository: (input: GitHubRepositoryInput) => Promise<RepositoryCard>;
  startCloudRun: (taskId: string) => Promise<CloudRunResult>;
  createPullRequest: (approvalId: string) => Promise<PullRequestResult>;
```

- [ ] **Step 4: Implement fake client methods**

In `fakeApiClient`, add deterministic methods that return:

- GitHub credential id `github_credential_demo`
- GitHub repository id `repo_github_demo`
- Cloud run id `cloud_run_${taskId}`
- Patch artifact id `patch_cloud_${taskId}`
- Pull request URL `https://github.com/example/demo/pull/1`

The fake `createPullRequest()` must return task status `PR_CREATED`.

- [ ] **Step 5: Implement HTTP client methods**

In `createHttpApiClient()`:

```typescript
    async createGitHubCredential(input) {
      const response = await fetch(apiUrl(options.baseUrl, "/github-credentials"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input)
      });
      return readJsonResponse<GitHubCredentialCard>(response, "POST /github-credentials");
    },
    async listGitHubCredentials() {
      const response = await fetch(apiUrl(options.baseUrl, "/github-credentials"));
      return readJsonResponse<GitHubCredentialCard[]>(response, "GET /github-credentials");
    },
    async createGitHubRepository(input) {
      const projectId = await getProjectId();
      const response = await fetch(
        apiUrl(options.baseUrl, `/projects/${projectId}/github-repositories`),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input)
        }
      );
      return readJsonResponse<RepositoryCard>(
        response,
        `POST /projects/${projectId}/github-repositories`
      );
    },
```

Add `startCloudRun()` and `createPullRequest()` using the same result mapping style as `startLocalRun()` and `approvePatch()`.

- [ ] **Step 6: Hydrate cloud and PR metadata in `listTasks()`**

Add `"PR_CREATED"` to `workflowStatuses`. In `hydrateTaskWorkflow()`, fetch cloud runs in parallel with local runs:

```typescript
const [localRuns, cloudRuns] = await Promise.all([
  listJson<ApiLocalTaskRun>(`/tasks/${task.id}/local-runs`, `GET /tasks/${task.id}/local-runs`),
  listJson<ApiCloudRun>(`/tasks/${task.id}/cloud-runs`, `GET /tasks/${task.id}/cloud-runs`)
]);
```

Prefer the latest cloud run with `patch_artifact_id` over local runs when present. After loading the patch artifact, fetch pull requests:

```typescript
const pullRequests = await listJson<ApiPullRequest>(
  `/patch-artifacts/${artifact.id}/pull-requests`,
  `GET /patch-artifacts/${artifact.id}/pull-requests`
);
```

Attach `cloud_run` and latest `pull_request` to `TaskCard`.

- [ ] **Step 7: Run client tests to verify GREEN**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit desktop client work**

Run:

```powershell
git add apps/desktop/src/api/client.ts apps/desktop/src/test/client.test.ts
git commit -m "feat(desktop): add github pr client contract"
```

---

## Task 5: Desktop GitHub Setup, Cloud Run, and Create PR UI

**Files:**
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/components/TaskBoard.tsx`
- Modify: `apps/desktop/src/fixtures/demoData.ts`
- Modify: `apps/desktop/src/styles/app.css`
- Modify: `apps/desktop/src/test/App.test.tsx`

- [ ] **Step 1: Write failing App tests**

In `apps/desktop/src/test/App.test.tsx`, add tests:

```typescript
it("registers a github repository from the setup panel", async () => {
  const user = userEvent.setup();
  const createGitHubCredential = vi.fn<ConsoleApiClient["createGitHubCredential"]>()
    .mockResolvedValue({
      id: "github_credential_test",
      workspace_id: "workspace_test",
      display_name: "Dev GitHub",
      token_last4: "7890",
      status: "active",
      created_at: "2026-05-31T00:00:00Z",
      updated_at: "2026-05-31T00:00:00Z"
    });
  const createGitHubRepository = vi.fn<ConsoleApiClient["createGitHubRepository"]>()
    .mockResolvedValue({
      id: "repo_github_test",
      name: "Demo remote",
      local_path: "",
      default_branch: "main",
      status: "active",
      provider: "github",
      repo_url: "https://github.com/example/demo",
      github_owner: "example",
      github_repo: "demo",
      github_credential_id: "github_credential_test",
      connection_status: "active"
    });
  const apiClient = createMockApiClient({ createGitHubCredential, createGitHubRepository });

  render(<App apiClient={apiClient} />);

  await user.type(screen.getByLabelText("GitHub token"), "ghp_example1234567890");
  await user.click(screen.getByRole("button", { name: "Connect GitHub repo" }));

  expect(createGitHubCredential).toHaveBeenCalled();
  expect(createGitHubRepository).toHaveBeenCalledWith(expect.objectContaining({
    github_owner: "example",
    github_repo: "demo"
  }));
  expect(await screen.findByText("GitHub repo connected")).toBeInTheDocument();
});

it("runs a cloud task and renders cloud branch metadata", async () => {
  const user = userEvent.setup();
  const task = createdTaskFixture();
  const startCloudRun = vi.fn<ConsoleApiClient["startCloudRun"]>().mockResolvedValue({
    cloud_run: cloudRunFixture(),
    patch_artifact: cloudPatchArtifactFixture()
  });
  const apiClient = createMockApiClient({
    listTasks: vi.fn().mockResolvedValue([task]),
    startCloudRun
  });

  render(<App apiClient={apiClient} />);

  const board = within(screen.getByLabelText("Task board"));
  await user.click(await board.findByRole("button", { name: "Run cloud" }));

  expect(startCloudRun).toHaveBeenCalledWith(task.id);
  expect(await board.findByText("PATCH_READY")).toBeInTheDocument();
  expect(board.getByText("ai-scdc/task-cloud")).toBeInTheDocument();
});

it("creates a pull request after human approval", async () => {
  const user = userEvent.setup();
  const task = humanApprovalTaskFixture();
  const createPullRequest = vi.fn<ConsoleApiClient["createPullRequest"]>().mockResolvedValue({
    task: { ...task, status: "PR_CREATED" },
    patch_artifact: task.patch_artifact!,
    approval: task.patch_approval!,
    pull_request: pullRequestFixture()
  });
  const apiClient = createMockApiClient({
    listTasks: vi.fn().mockResolvedValue([task]),
    createPullRequest
  });

  render(<App apiClient={apiClient} />);

  const board = within(screen.getByLabelText("Task board"));
  await user.click(await board.findByRole("button", { name: "Create PR" }));

  expect(createPullRequest).toHaveBeenCalledWith(task.patch_approval!.id);
  expect(await board.findByText("PR_CREATED")).toBeInTheDocument();
  expect(board.getByRole("link", { name: "https://github.com/example/demo/pull/1" })).toBeInTheDocument();
});
```

Add fixture helpers `cloudRunFixture()`, `cloudPatchArtifactFixture()`, `humanApprovalTaskFixture()`, and `pullRequestFixture()` near existing fixture helpers.

- [ ] **Step 2: Run App tests to verify RED**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/App.test.tsx
```

Expected: FAIL because UI and handlers do not exist.

- [ ] **Step 3: Add App state and handlers**

In `apps/desktop/src/App.tsx`, add state:

```typescript
const [githubSetupStatus, setGitHubSetupStatus] = useState<string | null>(null);
const [githubSetupError, setGitHubSetupError] = useState<string | null>(null);
const [runningCloudTaskId, setRunningCloudTaskId] = useState<string | null>(null);
const [creatingPullRequestTaskId, setCreatingPullRequestTaskId] = useState<string | null>(null);
```

Add handlers:

```typescript
async function handleConnectGitHubRepo(input: {
  token: string;
  repo_url: string;
  github_owner: string;
  github_repo: string;
  default_branch: string;
}) {
  setGitHubSetupStatus(null);
  setGitHubSetupError(null);
  try {
    const credential = await apiClient.createGitHubCredential({
      display_name: "Dev GitHub",
      token: input.token
    });
    await apiClient.createGitHubRepository({
      name: `${input.github_owner}/${input.github_repo}`,
      repo_url: input.repo_url,
      github_owner: input.github_owner,
      github_repo: input.github_repo,
      default_branch: input.default_branch,
      github_credential_id: credential.id
    });
    setGitHubSetupStatus("GitHub repo connected");
  } catch (error) {
    setGitHubSetupError(errorMessage(error, "Failed to connect GitHub repo"));
  }
}
```

Add `handleStartCloudRun(taskId)` and `handleCreatePullRequest(task)` following the existing `handleStartLocalRun()` and `handleRequestHumanApproval()` patterns. `handleCreatePullRequest()` must merge the returned `pull_request` into the task.

- [ ] **Step 4: Add minimal GitHub setup form**

Create the form inside `App.tsx` as a small internal component or inline section in `contextPanel`. Use labels:

- `GitHub token`
- `Repository URL`
- `Owner`
- `Repository`
- `Default branch`

Default input values for development:

```typescript
repo_url: "https://github.com/example/demo"
github_owner: "example"
github_repo: "demo"
default_branch: "main"
```

The submit button text must be `Connect GitHub repo`.

- [ ] **Step 5: Add TaskBoard controls and metadata**

Extend `TaskBoardProps`:

```typescript
  runningCloudTaskId?: string | null;
  creatingPullRequestTaskId?: string | null;
  onStartCloudRun?: (taskId: string) => void;
  onCreatePullRequest?: (task: TaskCard) => void;
```

Add a `Run cloud` button for `CREATED` tasks:

```tsx
{onStartCloudRun && task.status === "CREATED" ? (
  <button
    type="button"
    className="task-run-button"
    disabled={runningCloudTaskId !== null}
    onClick={() => onStartCloudRun(task.id)}
  >
    {runningCloudTaskId === task.id ? "Running cloud" : "Run cloud"}
  </button>
) : null}
```

Add a `Create PR` button:

```tsx
{onCreatePullRequest && task.status === "HUMAN_APPROVAL" && task.patch_approval ? (
  <button
    type="button"
    className="task-run-button"
    disabled={creatingPullRequestTaskId !== null}
    onClick={() => onCreatePullRequest(task)}
  >
    {creatingPullRequestTaskId === task.id ? "Creating PR" : "Create PR"}
  </button>
) : null}
```

Add metadata:

```tsx
{task.cloud_run ? (
  <div>
    <dt>Cloud run</dt>
    <dd>{`${task.cloud_run.status} on ${task.cloud_run.head_branch}`}</dd>
  </div>
) : null}
{task.pull_request ? (
  <div>
    <dt>Pull request</dt>
    <dd>
      <a href={task.pull_request.github_pr_url} target="_blank" rel="noreferrer">
        {task.pull_request.github_pr_url}
      </a>
    </dd>
  </div>
) : null}
```

- [ ] **Step 6: Update demo data and styles**

In `apps/desktop/src/fixtures/demoData.ts`, add one `HUMAN_APPROVAL` task with `cloud_run` and `patch_approval`, and one `PR_CREATED` task with `pull_request`.

In `apps/desktop/src/styles/app.css`, add compact form styling:

```css
.github-setup-form {
  display: grid;
  gap: 8px;
}

.github-setup-form label {
  display: grid;
  gap: 4px;
  color: #344047;
  font-size: 11px;
}

.github-setup-form input {
  min-width: 0;
  border: 1px solid #d7dee2;
  border-radius: 6px;
  padding: 7px 8px;
  color: #172026;
  font: inherit;
}
```

- [ ] **Step 7: Run desktop UI tests to verify GREEN**

Run:

```powershell
pnpm --filter @ai-scdc/desktop test -- src/test/App.test.tsx
pnpm --filter @ai-scdc/desktop typecheck
```

Expected: PASS.

- [ ] **Step 8: Commit UI work**

Run:

```powershell
git add apps/desktop/src/App.tsx apps/desktop/src/components/TaskBoard.tsx apps/desktop/src/fixtures/demoData.ts apps/desktop/src/styles/app.css apps/desktop/src/test/App.test.tsx
git commit -m "feat(desktop): add github pr controls"
```

---

## Task 6: Docs, Roadmap, Full Verification, and Review

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`

- [ ] **Step 1: Update architecture docs**

In `docs/architecture.md`, add after Phase 6:

```markdown
## Phase 7 Boundary

Phase 7 adds a GitHub-only pull request publishing boundary. A task can run through a deterministic fake cloud sandbox, produce a normal patch artifact, pass the existing local verification/review workflow, receive patch approval, move to `HUMAN_APPROVAL`, and then create a GitHub pull request only after the user clicks `Create PR`.

The first cloud sandbox is a control-plane fake worker, not a real container service. The API stores GitHub PAT metadata through the development secret vault, registers GitHub repositories, records `CloudRun` and `PullRequestRecord` rows, and moves tasks to `PR_CREATED` after successful PR creation. Phase 7 does not merge pull requests, write to default branches, deploy code, add GitHub OAuth, or add GitLab support.
```

Move Phase 7 into Completed after implementation:

```markdown
8. GitHub-only cloud-run and pull-request boundary with PAT metadata, fake cloud sandbox artifacts, explicit `Create PR`, durable PR records, and no automatic merge.
```

- [ ] **Step 2: Update README smoke notes**

Add a Phase 7 section:

```markdown
## Phase 7 GitHub PR Smoke Test

Phase 7 can create a real GitHub pull request after an approved patch reaches `HUMAN_APPROVAL`. Automated tests use the fake GitHub adapter and do not require network access.

Start the API:

```powershell
pnpm dev:api
```

In another PowerShell session:

```powershell
$base = "http://127.0.0.1:8000"
$secureToken = Read-Host "GitHub PAT" -AsSecureString
$githubToken = [System.Net.NetworkCredential]::new("", $secureToken).Password

function JsonBody($value) {
  $value | ConvertTo-Json -Depth 8 -Compress
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
  -Body (JsonBody @{ name = "Phase 7 smoke"; description = "GitHub PR smoke test" })

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
```

Continue by creating a task, starting a cloud run, running tests, reviewing, approving, requesting human approval, then:

```powershell
$pr = Invoke-RestMethod `
  -Uri "$base/patch-approvals/$($approval.approval.id)/pull-requests" `
  -Method Post

$pr.task.status
$pr.pull_request.github_pr_url
```

Expected task status:

```text
PR_CREATED
```

Do not commit GitHub PATs or paste them into chat. The API returns only credential metadata.
```

- [ ] **Step 3: Run focused verification**

Run:

```powershell
pytest apps/api/tests/test_github_repository_api.py apps/api/tests/test_cloud_run_api.py apps/api/tests/test_pull_request_api.py -v
pnpm --filter @ai-scdc/desktop test -- src/test/client.test.ts src/test/App.test.tsx
pnpm --filter @ai-scdc/desktop typecheck
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Run full verification**

Run:

```powershell
pnpm test
pnpm typecheck
pytest apps/api/tests apps/worker/tests services/llm-gateway/tests -v
git diff --check
```

Expected:

- `pnpm test`: JS and Python suites pass.
- `pnpm typecheck`: TypeScript typecheck passes.
- `pytest ... -v`: all Python tests pass. Existing `StarletteDeprecationWarning` is acceptable.
- `git diff --check`: no whitespace errors.

- [ ] **Step 5: Commit docs**

Run:

```powershell
git add README.md docs/architecture.md
git commit -m "docs: document phase 7 github pr workflow"
```

- [ ] **Step 6: Request final code review**

Dispatch a reviewer against the Phase 7 implementation branch. Ask it to verify:

- PATs are never returned or logged.
- Automated tests use fake GitHub behavior and do not need network.
- `Create PR` requires `HUMAN_APPROVAL`.
- PR creation is idempotent per patch approval.
- `PR_CREATED` is not used as a merge signal.
- Existing local Phase 4-6 workflows still work.

- [ ] **Step 7: Final sanity check**

Run:

```powershell
git status --short
git log --oneline -8
```

Expected:

- `git status --short` is empty.
- Recent commits include Phase 7 backend, desktop, docs, and any review-fix commits.

---

## Self-Review Notes

- Spec coverage: GitHub PAT metadata, GitHub repository registration, fake cloud run, Phase 5/6 compatibility, explicit `Create PR`, durable PR records, `PR_CREATED`, no automatic merge, and fake-test/no-network boundaries are covered.
- Scope check: The plan intentionally avoids GitLab, OAuth, GitHub App installation, real cloud workers, object storage, automatic merges, and commercial billing/RBAC.
- Type consistency: Backend uses `GitHubCredentialRead`, `CloudRunRead`, `CloudRunResultRead`, `PullRequestRead`, and `PullRequestResultRead`; desktop mirrors these as `GitHubCredentialCard`, `CloudRunCard`, `CloudRunResult`, `PullRequestCard`, and `PullRequestResult`.
- Compatibility detail: Fake cloud runs create a companion `LocalTaskRun` with `runner_kind="cloud_fake"` so existing patch artifacts, test runs, reviews, and approvals can keep using `local_run_id`.
