import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    GitHubCredential,
    Project,
    Repository,
    SandboxProfile,
)
from ai_company_api.services.secret_vault import DevSecretVault


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def run_git(repo_path: Path, *args: str) -> str:
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
    repo_path = create_git_repo(tmp_path)

    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "Local project"}).json()
        repository = client.post(
            f"/projects/{project['id']}/repositories",
            json={
                "name": "Local repo",
                "local_path": str(repo_path),
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
    assert response.json()["detail"] == (
        "Sandbox profile requires exactly one default patch command"
    )


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

    assert {
        "sandbox_profile_id",
        "patch_command_key",
        "test_command_keys",
        "command_results",
    } <= columns
    assert "ix_cloud_run_sandbox_profile_id" in indexes
