import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from ai_company_api.main import create_app
from ai_company_worker.local_runner import LocalRunnerError


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


def test_create_list_and_get_project_repository(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)

    with build_client(tmp_path / "api.db") as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        create_response = client.post(
            f"/projects/{project['id']}/repositories",
            json={
                "name": "Demo Repo",
                "local_path": str(repo_path),
                "default_branch": "main",
            },
        )
        list_response = client.get(f"/projects/{project['id']}/repositories")

    assert create_response.status_code == 201
    repository = create_response.json()
    assert repository["project_id"] == project["id"]
    assert repository["name"] == "Demo Repo"
    assert Path(repository["local_path"]) == repo_path.resolve()
    assert repository["default_branch"] == "main"
    assert repository["status"] == "active"
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [repository["id"]]

    with build_client(tmp_path / "api.db") as client:
        get_response = client.get(f"/repositories/{repository['id']}")

    assert get_response.status_code == 200
    assert get_response.json()["id"] == repository["id"]


def test_create_repository_rejects_missing_project(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)

    with build_client(tmp_path / "api.db") as client:
        response = client.post(
            "/projects/project_missing/repositories",
            json={"name": "Demo Repo", "local_path": str(repo_path)},
        )

    assert response.status_code == 404


def test_create_repository_rejects_non_git_local_path(tmp_path: Path) -> None:
    not_repo = tmp_path / "not-repo"
    not_repo.mkdir()

    with build_client(tmp_path / "api.db") as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        response = client.post(
            f"/projects/{project['id']}/repositories",
            json={"name": "Not Repo", "local_path": str(not_repo)},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Local path is not a git repository"


def test_start_local_run_creates_patch_artifact_and_marks_task_patch_ready(
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)

    with build_client(tmp_path / "api.db") as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        repository = client.post(
            f"/projects/{project['id']}/repositories",
            json={"name": "Demo Repo", "local_path": str(repo_path)},
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Update README",
                "role_required": "documentation",
                "allowed_paths": ["README.md"],
                "required_tests": ["pytest apps/worker/tests/test_local_runner.py -v"],
            },
        ).json()

        run_response = client.post(
            f"/tasks/{task['id']}/local-runs",
            json={"repo_id": repository["id"]},
        )
        task_response = client.get(f"/tasks/{task['id']}")
        runs_response = client.get(f"/tasks/{task['id']}/local-runs")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert run_response.status_code == 201
    local_run = run_response.json()
    assert local_run["task_id"] == task["id"]
    assert local_run["repo_id"] == repository["id"]
    assert local_run["status"] == "patch_ready"
    assert local_run["patch_artifact_id"] is not None
    assert Path(local_run["worktree_path"]).is_dir()
    assert ".worktrees" in Path(local_run["worktree_path"]).parts

    updated_task = task_response.json()
    assert updated_task["status"] == "PATCH_READY"
    assert updated_task["repo_id"] == repository["id"]
    assert updated_task["branch_name"] == repository["default_branch"]
    assert updated_task["worktree_ref"] == local_run["worktree_path"]

    assert [item["id"] for item in runs_response.json()] == [local_run["id"]]
    event_types = [event["event_type"] for event in events_response.json()]
    assert event_types == [
        "task_created",
        "local_run_started",
        "task_transitioned",
        "task_transitioned",
        "patch_artifact_created",
        "task_transitioned",
    ]

    with build_client(tmp_path / "api.db") as client:
        artifact_response = client.get(
            f"/patch-artifacts/{local_run['patch_artifact_id']}"
        )

    assert artifact_response.status_code == 200
    artifact = artifact_response.json()
    assert artifact["task_id"] == task["id"]
    assert artifact["local_run_id"] == local_run["id"]
    assert artifact["files_changed"] == ["README.md"]
    assert artifact["tests_run"] == ["pytest apps/worker/tests/test_local_runner.py -v"]
    assert artifact["test_result"] == "not_run"
    assert "README.md" in artifact["diff_text"]


def test_start_local_run_rejects_cross_project_repository(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)

    with build_client(tmp_path / "api.db") as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        repository = client.post(
            f"/projects/{other_project['id']}/repositories",
            json={"name": "Other Repo", "local_path": str(repo_path)},
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Update README",
                "role_required": "documentation",
                "allowed_paths": ["README.md"],
            },
        ).json()

        response = client.post(
            f"/tasks/{task['id']}/local-runs",
            json={"repo_id": repository["id"]},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Repository does not belong to task project"


def test_start_local_run_records_worker_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)

    def fail_runner(_request):
        raise LocalRunnerError("No safe allowed path is available")

    monkeypatch.setattr("ai_company_api.services.local_runner.RUN_LOCAL_TASK", fail_runner)

    with build_client(tmp_path / "api.db") as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        repository = client.post(
            f"/projects/{project['id']}/repositories",
            json={"name": "Demo Repo", "local_path": str(repo_path)},
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Update README",
                "role_required": "documentation",
                "allowed_paths": ["README.md"],
            },
        ).json()

        response = client.post(
            f"/tasks/{task['id']}/local-runs",
            json={"repo_id": repository["id"]},
        )
        task_response = client.get(f"/tasks/{task['id']}")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert response.status_code == 201
    local_run = response.json()
    assert local_run["status"] == "failed"
    assert local_run["failure_reason"] == "No safe allowed path is available"
    assert local_run["patch_artifact_id"] is None
    assert task_response.json()["status"] == "FIX_REQUESTED"
    event_types = [event["event_type"] for event in events_response.json()]
    assert event_types == [
        "task_created",
        "local_run_started",
        "task_transitioned",
        "task_transitioned",
        "local_run_failed",
        "task_transitioned",
    ]
