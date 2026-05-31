from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    Project,
    Repository,
    Task,
)
from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionResult,
)
from ai_company_api.services.task_state import TaskStatus


def build_client(database_path: Path) -> TestClient:
    database_url = f"sqlite:///{database_path.as_posix()}"
    init_db(build_engine(database_url))
    return TestClient(create_app(database_url=database_url))


def create_cloud_task(
    session: Session,
    *,
    provider: str = "github",
    connection_status: str = "active",
    required_tests: list[str] | None = None,
) -> tuple[Project, Repository, Task]:
    project = Project(name="Cloud project")
    session.add(project)
    session.flush()
    repository = Repository(
        project_id=project.id,
        name="Demo remote",
        local_path="",
        default_branch="main",
        provider=provider,
        repo_url="https://github.com/example/demo",
        github_owner="example",
        github_repo="demo",
        github_credential_id="github_credential_test",
        connection_status=connection_status,
    )
    session.add(repository)
    session.flush()
    task = Task(
        project_id=project.id,
        title="Run fake cloud sandbox",
        role_required="backend",
        status=TaskStatus.CREATED,
        allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
        required_tests=["python -V"] if required_tests is None else required_tests,
    )
    session.add(task)
    session.commit()
    session.refresh(project)
    session.refresh(repository)
    session.refresh(task)
    return project, repository, task


def test_cloud_runner_command_payloads_redact_secrets_before_persistence() -> None:
    from ai_company_api.services.cloud_runner import _command_result_payloads

    payloads = _command_result_payloads(
        [
            CommandResult(
                command=(
                    "git clone "
                    "https://ghp_example1234567890@github.com/example/demo"
                ),
                exit_code=1,
                stdout="seen ghp_example1234567890",
                stderr="failed ghp_example1234567890",
                duration_ms=25,
                timed_out=True,
            )
        ],
        secrets=["ghp_example1234567890"],
    )

    assert payloads == [
        {
            "command": "git clone https://[redacted]@github.com/example/demo",
            "exit_code": 1,
            "stdout": "seen [redacted]",
            "stderr": "failed [redacted]",
            "duration_ms": 25,
        }
    ]


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
    assert result["cloud_run"]["head_branch"] == (
        f"ai-scdc/task-{task.id}-{result['cloud_run']['id']}"
    )
    assert result["patch_artifact"]["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert result["patch_artifact"]["test_result"] == "not_run"

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, result["cloud_run"]["id"])
        local_run = session.get(LocalTaskRun, result["cloud_run"]["local_run_id"])
        artifact = session.get(PatchArtifact, result["patch_artifact"]["id"])
        persisted_task = session.get(Task, task.id)

    assert cloud_run is not None
    assert local_run is not None
    assert artifact is not None
    assert persisted_task is not None
    assert local_run.runner_kind == "cloud_fake"
    assert local_run.worktree_path == f"cloud://fake/{cloud_run.id}"
    assert local_run.patch_artifact_id == artifact.id
    assert cloud_run.patch_artifact_id == artifact.id
    assert persisted_task.status == TaskStatus.PATCH_READY
    assert persisted_task.branch_name == f"ai-scdc/task-{task.id}-{cloud_run.id}"
    assert persisted_task.worktree_ref == f"cloud://fake/{cloud_run.id}"


def test_start_cloud_run_persists_executor_test_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerResultExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            return SandboxExecutionResult(
                status="patch_ready",
                runner_kind="docker_local",
                base_sha="abc123",
                head_sha="def456",
                worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["python -V"],
                test_result="passed",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[
                    CommandResult(
                        command="git clone https://[redacted]@github.com/example/demo",
                        exit_code=0,
                        stdout="",
                        stderr="",
                        duration_ms=10,
                    )
                ],
                test_command_results=[
                    CommandResult(
                        command="python -V",
                        exit_code=0,
                        stdout="Python 3.11\n",
                        stderr="",
                        duration_ms=3,
                    )
                ],
                failure_reason=None,
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerResultExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": repository.id})

    assert response.status_code == 201
    result = response.json()
    assert result["cloud_run"]["command_results"][0]["command"] == (
        "git clone https://[redacted]@github.com/example/demo"
    )

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        test_runs = session.exec(select(LocalTestRun)).all()

    assert len(test_runs) == 1
    assert test_runs[0].status == "passed"
    assert test_runs[0].commands == ["python -V"]
    assert test_runs[0].command_results == [
        {
            "command": "python -V",
            "exit_code": 0,
            "stdout": "Python 3.11\n",
            "stderr": "",
            "duration_ms": 3,
        }
    ]
    assert test_runs[0].failure_reason is None


def test_start_cloud_run_failure_does_not_create_patch_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FailedDockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                base_sha=None,
                head_sha=None,
                worktree_ref=None,
                summary="",
                files_changed=[],
                tests_run=["python -V"],
                test_result="failed",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command="docker version",
                        exit_code=0,
                        stdout="Docker",
                        stderr="",
                        duration_ms=1,
                    )
                ],
                test_command_results=[
                    CommandResult(
                        command="python -V",
                        exit_code=1,
                        stdout="",
                        stderr="failed",
                        duration_ms=3,
                    )
                ],
                failure_reason="test_failed",
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FailedDockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": repository.id})

    assert response.status_code == 201
    result = response.json()
    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "failed"
    assert result["cloud_run"]["failure_reason"] == "test_failed"
    assert result["cloud_run"]["patch_artifact_id"] is None
    assert [item["command"] for item in result["cloud_run"]["command_results"]] == [
        "docker version",
        "python -V",
    ]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, result["cloud_run"]["id"])
        local_run = session.get(LocalTaskRun, result["cloud_run"]["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
        test_runs = session.exec(select(LocalTestRun)).all()
        persisted_task = session.get(Task, task.id)

    assert cloud_run is not None
    assert local_run is not None
    assert persisted_task is not None
    assert artifacts == []
    assert test_runs == []
    assert cloud_run.patch_artifact_id is None
    assert cloud_run.failure_reason == "test_failed"
    assert local_run.patch_artifact_id is None
    assert local_run.failure_reason == "test_failed"
    assert persisted_task.status == TaskStatus.FIX_REQUESTED


def test_list_and_get_cloud_run_routes_return_created_run(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    create_response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={"repo_id": repository.id},
    )
    assert create_response.status_code == 201
    cloud_run_id = create_response.json()["cloud_run"]["id"]
    list_response = client.get(f"/tasks/{task.id}/cloud-runs")
    get_response = client.get(f"/cloud-runs/{cloud_run_id}")

    assert list_response.status_code == 200
    assert [cloud_run["id"] for cloud_run in list_response.json()] == [cloud_run_id]
    assert get_response.status_code == 200
    assert get_response.json()["id"] == cloud_run_id


def test_cloud_run_ignores_unvalidated_sandbox_profile_fields(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    response = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={
            "repo_id": repository.id,
            "sandbox_profile_id": "sandbox_profile_unvalidated",
            "patch_command_key": "patch",
            "test_command_keys": ["test"],
        },
    )

    assert response.status_code == 201
    cloud_run = response.json()["cloud_run"]
    assert cloud_run["sandbox_profile_id"] is None
    assert cloud_run["patch_command_key"] is None
    assert cloud_run["test_command_keys"] == []
    assert cloud_run["command_results"] == []

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted = session.get(CloudRun, cloud_run["id"])

    assert persisted is not None
    assert persisted.sandbox_profile_id is None
    assert persisted.patch_command_key is None
    assert persisted.test_command_keys == []
    assert persisted.command_results == []


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
        task_id = task.id
        session.refresh(other_repo)
        other_repo_id = other_repo.id

    response = client.post(f"/tasks/{task_id}/cloud-runs", json={"repo_id": other_repo_id})

    assert response.status_code == 400
    assert response.json()["detail"] == "Repository does not belong to task project"


def test_cloud_run_rejects_non_github_repository(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session, provider="local")

    response = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": repository.id})

    assert response.status_code == 400
    assert response.json()["detail"] == "Cloud runs require a GitHub repository"


def test_cloud_run_rejects_inactive_github_repository(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(
            session,
            connection_status="inactive",
        )

    response = client.post(f"/tasks/{task.id}/cloud-runs", json={"repo_id": repository.id})

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub repository is not active"


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


def test_cloud_fake_test_run_records_result_for_each_required_command(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    required_tests = ["python -V", "pytest -q"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(
            session,
            required_tests=required_tests,
        )

    cloud_result = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={"repo_id": repository.id},
    ).json()
    patch_artifact_id = cloud_result["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")

    assert test_response.status_code == 201
    test_run = test_response.json()["test_run"]
    assert test_run["commands"] == required_tests
    assert [result["command"] for result in test_run["command_results"]] == required_tests
    assert [result["stdout"] for result in test_run["command_results"]] == [
        "cloud fake test passed",
        "cloud fake test passed",
    ]
    assert [result["exit_code"] for result in test_run["command_results"]] == [0, 0]


def test_cloud_fake_test_run_persists_fallback_command_when_required_tests_empty(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session, required_tests=[])

    cloud_result = client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={"repo_id": repository.id},
    ).json()
    patch_artifact_id = cloud_result["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")

    assert test_response.status_code == 201
    result = test_response.json()
    test_run = result["test_run"]
    assert test_run["commands"] == ["cloud fake test"]
    assert [item["command"] for item in test_run["command_results"]] == [
        "cloud fake test"
    ]
    assert test_run["command_results"][0]["stdout"] == "cloud fake test passed"
    assert result["patch_artifact"]["tests_run"] == ["cloud fake test"]
