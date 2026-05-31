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
