import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    DebugAttempt,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    PatchReview,
    Project,
    Repository,
    Task,
)


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


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
        repository = Repository(
            project_id=project.id,
            name="Demo repo",
            local_path=".",
            default_branch="main",
        )
        session.add(repository)
        session.flush()
        local_run = LocalTaskRun(
            project_id=project.id,
            task_id=task.id,
            repo_id=repository.id,
            status="completed",
        )
        session.add(local_run)
        session.flush()
        patch_artifact = PatchArtifact(
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
        session.add(patch_artifact)
        session.flush()

        test_run = LocalTestRun(
            project_id=project.id,
            task_id=task.id,
            local_run_id=local_run.id,
            patch_artifact_id=patch_artifact.id,
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
            local_run_id=local_run.id,
            patch_artifact_id=patch_artifact.id,
            test_run_id=test_run.id,
            verdict="approved",
            issues=[],
            required_changes=[],
        )
        debug_attempt = DebugAttempt(
            project_id=project.id,
            task_id=task.id,
            patch_artifact_id=patch_artifact.id,
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


def test_passing_test_run_moves_patch_ready_task_to_reviewing(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)
    with build_client(tmp_path / "api.db") as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            [
                "python -c \"from pathlib import Path; "
                "assert Path('README.md').exists()\""
            ],
        )

        response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert response.status_code == 201
    result = response.json()
    assert result["task"]["status"] == "REVIEWING"
    assert result["patch_artifact"]["test_result"] == "passed"
    assert result["patch_artifact"]["tests_run"] == [
        "python -c \"from pathlib import Path; assert Path('README.md').exists()\""
    ]
    assert result["test_run"]["status"] == "passed"
    assert result["test_run"]["command_results"][0]["exit_code"] == 0
    assert result["debug_attempt"] is None

    event_types = [event["event_type"] for event in events_response.json()]
    assert "test_run_started" in event_types
    assert "test_run_completed" in event_types


def test_failing_test_run_moves_task_to_fix_requested_and_creates_debug_attempt(
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)
    with build_client(tmp_path / "api.db") as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -c \"import sys; print('bad'); sys.exit(7)\""],
        )

        response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert response.status_code == 201
    result = response.json()
    assert result["task"]["status"] == "FIX_REQUESTED"
    assert result["patch_artifact"]["test_result"] == "failed"
    assert result["test_run"]["status"] == "failed"
    assert result["test_run"]["command_results"][0]["exit_code"] == 7
    assert result["debug_attempt"]["status"] == "requested"
    assert "Test command failed" in result["debug_attempt"]["root_cause"]

    event_types = [event["event_type"] for event in events_response.json()]
    assert "test_run_started" in event_types
    assert "test_run_completed" in event_types
    assert "debug_attempt_created" in event_types
