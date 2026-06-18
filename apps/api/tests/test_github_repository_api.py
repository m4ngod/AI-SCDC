from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import GitHubCredential, Repository, WorkspaceAuditLog


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def auth_headers(
    *,
    user_id: str = "dev_user",
    workspace_id: str = "dev_workspace",
    organization_id: str = "dev_organization",
    roles: str = "owner",
) -> dict[str, str]:
    return {
        "x-ai-scdc-user-id": user_id,
        "x-ai-scdc-workspace-id": workspace_id,
        "x-ai-scdc-organization-id": organization_id,
        "x-ai-scdc-roles": roles,
    }


def test_github_credential_never_returns_secret_fields(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
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
    with build_client(tmp_path / "app.db") as client:
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.delete(f"/github-credentials/{credential['id']}")

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        assert client.get("/github-credentials").json()[0]["status"] == "deleted"


def test_github_credential_validation_error_redacts_token(tmp_path: Path) -> None:
    raw_token = "ghp"

    with build_client(tmp_path / "app.db") as client:
        response = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": raw_token},
        )

    response_text = str(response.json())
    assert response.status_code == 422
    assert raw_token not in response_text
    assert "[redacted]" in response_text


def test_register_github_repository_requires_active_credential(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
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
    with build_client(tmp_path / "app.db") as client:
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


def test_register_github_repository_requires_repository_write_and_audits_denial(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    owner_headers = auth_headers(roles="owner")
    viewer_headers = auth_headers(user_id="viewer_user", roles="viewer")

    with build_client(database_path) as client:
        project = client.post(
            "/projects",
            json={"name": "GitHub project"},
            headers=owner_headers,
        ).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
            headers=owner_headers,
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Denied remote",
                "repo_url": "https://github.com/example/denied",
                "github_owner": "example",
                "github_repo": "denied",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
            headers=viewer_headers,
        )

    assert response.status_code == 403
    with Session(build_engine(database_url)) as session:
        repositories = session.exec(select(Repository)).all()
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "github_repository.create"
            )
        ).one()

    assert repositories == []
    assert audit_log.resource_type == "repository"
    assert audit_log.resource_id is None
    assert audit_log.access_level.value == "high_value_write"
    assert audit_log.success is False
    assert audit_log.status_code == 403
    assert audit_log.error_code == "insufficient_workspace_role"


def test_register_github_repository_records_workspace_audit(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    owner_headers = auth_headers(roles="owner")
    developer_headers = auth_headers(user_id="developer_user", roles="developer")

    with build_client(database_path) as client:
        project = client.post(
            "/projects",
            json={"name": "GitHub project"},
            headers=owner_headers,
        ).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
            headers=owner_headers,
        ).json()
        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Audited remote",
                "repo_url": "https://github.com/example/audited",
                "github_owner": "example",
                "github_repo": "audited",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
            headers=developer_headers,
        )

    assert response.status_code == 201
    repository = response.json()
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "github_repository.create"
            )
        ).one()

    assert audit_log.resource_type == "repository"
    assert audit_log.resource_id == repository["id"]
    assert audit_log.access_level.value == "high_value_write"
    assert audit_log.success is True
    assert audit_log.status_code == 201
    assert audit_log.metadata_json == {
        "project_id": project["id"],
        "github_credential_id": credential["id"],
        "provider": "github",
    }
    assert "ghp_example" not in str(audit_log.model_dump())


def test_repository_delete_marks_repository_inactive(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()
        repository = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://github.com/example/demo",
                "github_owner": "example",
                "github_repo": "demo",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        ).json()

        response = client.delete(f"/repositories/{repository['id']}")
        list_response = client.get(f"/projects/{project['id']}/repositories")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert response.json()["connection_status"] == "inactive"
    assert list_response.json()[0]["status"] == "deleted"
    assert list_response.json()[0]["connection_status"] == "inactive"


def test_register_github_repository_normalizes_github_url(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://github.com/example/demo.git",
                "github_owner": "example",
                "github_repo": "demo",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        )

    assert response.status_code == 201
    assert response.json()["repo_url"] == "https://github.com/example/demo.git"


def test_register_github_repository_rejects_non_github_url(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://evil.example/example/demo",
                "github_owner": "example",
                "github_repo": "demo",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub repository URL must match owner/repo"


def test_register_github_repository_rejects_userinfo_url(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://user:secret@github.com/example/demo",
                "github_owner": "example",
                "github_repo": "demo",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub repository URL must not include credentials"


def test_register_github_repository_rejects_owner_repo_mismatch(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://github.com/example/other",
                "github_owner": "example",
                "github_repo": "demo",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub repository URL must match owner/repo"


def test_register_github_repository_rejects_multisegment_owner(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://github.com/example/team/demo",
                "github_owner": "example/team",
                "github_repo": "demo",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub owner and repo must be single path segments"


def test_register_github_repository_rejects_encoded_slash_repo(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db") as client:
        project = client.post("/projects", json={"name": "GitHub project"}).json()
        credential = client.post(
            "/github-credentials",
            json={"display_name": "Dev GitHub", "token": "ghp_example1234567890"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/github-repositories",
            json={
                "name": "Demo remote",
                "repo_url": "https://github.com/example/demo%2Fsecret",
                "github_owner": "example",
                "github_repo": "demo%2Fsecret",
                "default_branch": "main",
                "github_credential_id": credential["id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub owner and repo must be single path segments"


def test_request_sessions_do_not_initialize_database_per_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import ai_company_api.db.session as db_session

    def fail_request_time_init_db(_engine) -> None:
        raise AssertionError("init_db should only run during lifespan startup")

    with build_client(tmp_path / "app.db") as client:
        monkeypatch.setattr(db_session, "init_db", fail_request_time_init_db)
        response = client.get("/github-credentials")

    assert response.status_code == 200


def test_init_db_adds_github_repository_columns_defaults_and_indexes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "old-repository.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table repository (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null,
                    name varchar not null,
                    local_path varchar not null,
                    default_branch varchar not null,
                    status varchar not null,
                    created_at datetime not null,
                    updated_at datetime not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into repository (
                    id,
                    workspace_id,
                    project_id,
                    name,
                    local_path,
                    default_branch,
                    status,
                    created_at,
                    updated_at
                )
                values (
                    'repo_legacy',
                    'dev_workspace',
                    'project_legacy',
                    'Legacy repo',
                    'T:/legacy',
                    'main',
                    'active',
                    '2026-01-01 00:00:00',
                    '2026-01-01 00:00:00'
                )
                """
            )
        )

    init_db(engine)

    with engine.connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(repository)")).mappings()
        }
        indexes = {
            row["name"]
            for row in connection.execute(text("PRAGMA index_list(repository)")).mappings()
        }
        legacy_row = connection.execute(
            text(
                """
                select
                    provider,
                    repo_url,
                    github_owner,
                    github_repo,
                    github_credential_id,
                    connection_status
                from repository
                where id = 'repo_legacy'
                """
            )
        ).mappings().one()

    assert {
        "provider",
        "repo_url",
        "github_owner",
        "github_repo",
        "github_credential_id",
        "connection_status",
    } <= columns
    assert legacy_row["provider"] == "local"
    assert legacy_row["repo_url"] == ""
    assert legacy_row["github_owner"] is None
    assert legacy_row["github_repo"] is None
    assert legacy_row["github_credential_id"] is None
    assert legacy_row["connection_status"] == "active"
    assert {
        "ix_repository_provider",
        "ix_repository_github_owner",
        "ix_repository_github_repo",
        "ix_repository_github_credential_id",
        "ix_repository_connection_status",
    } <= indexes
