from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
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
    assert len(test_runs) == 1
    assert test_runs[0].patch_artifact_id is None
    assert test_runs[0].status == "failed"
    assert test_runs[0].commands == ["python -V"]
    assert test_runs[0].command_results == [
        {
            "command": "python -V",
            "exit_code": 1,
            "stdout": "",
            "stderr": "failed",
            "duration_ms": 3,
        }
    ]
    assert test_runs[0].failure_reason == "test_failed"
    assert cloud_run.patch_artifact_id is None
    assert cloud_run.failure_reason == "test_failed"
    assert local_run.patch_artifact_id is None
    assert local_run.failure_reason == "test_failed"
    assert persisted_task.status == TaskStatus.FIX_REQUESTED

    read_response = client.get(f"/test-runs/{test_runs[0].id}")
    assert read_response.status_code == 200
    assert read_response.json()["patch_artifact_id"] is None


def test_init_db_allows_cloud_test_run_without_patch_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-local-test-run.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table local_test_run (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null,
                    task_id varchar not null,
                    local_run_id varchar not null,
                    patch_artifact_id varchar not null,
                    status varchar not null,
                    commands json not null,
                    command_results json not null,
                    failure_reason varchar,
                    started_at datetime not null,
                    completed_at datetime,
                    created_at datetime not null
                )
                """
            )
        )

    init_db(engine)

    with engine.begin() as connection:
        columns = {
            row["name"]: row
            for row in connection.execute(
                text("PRAGMA table_info(local_test_run)")
            ).mappings()
        }
        connection.execute(
            text(
                """
                insert into local_test_run (
                    id,
                    workspace_id,
                    project_id,
                    task_id,
                    local_run_id,
                    patch_artifact_id,
                    status,
                    commands,
                    command_results,
                    started_at,
                    created_at
                )
                values (
                    'test_run_failed_cloud',
                    'dev_workspace',
                    'project_one',
                    'task_one',
                    'local_run_one',
                    null,
                    'failed',
                    '[]',
                    '[]',
                    '2026-05-31 00:00:00',
                    '2026-05-31 00:00:00'
                )
                """
            )
        )

    assert columns["patch_artifact_id"]["notnull"] == 0


def test_init_db_preserves_local_test_run_fks_when_patch_artifact_nullable() -> None:
    engine = build_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        connection.execute(text("create table project (id varchar not null primary key)"))
        connection.execute(text("create table task (id varchar not null primary key)"))
        connection.execute(
            text("create table local_task_run (id varchar not null primary key)")
        )
        connection.execute(
            text("create table patch_artifact (id varchar not null primary key)")
        )
        connection.execute(text("insert into project (id) values ('project_one')"))
        connection.execute(text("insert into task (id) values ('task_one')"))
        connection.execute(
            text("insert into local_task_run (id) values ('local_run_one')")
        )
        connection.execute(text("insert into patch_artifact (id) values ('patch_one')"))
        connection.execute(
            text(
                """
                create table local_test_run (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null references project(id),
                    task_id varchar not null references task(id),
                    local_run_id varchar not null references local_task_run(id),
                    patch_artifact_id varchar not null references patch_artifact(id),
                    status varchar not null,
                    commands json not null,
                    command_results json not null,
                    failure_reason varchar,
                    started_at datetime not null,
                    completed_at datetime,
                    created_at datetime not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                create table patch_review (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null references project(id),
                    task_id varchar not null references task(id),
                    local_run_id varchar not null references local_task_run(id),
                    patch_artifact_id varchar not null references patch_artifact(id),
                    test_run_id varchar references local_test_run(id),
                    reviewer_kind varchar not null,
                    verdict varchar not null,
                    issues json not null,
                    required_changes json not null,
                    created_at datetime not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into local_test_run (
                    id,
                    workspace_id,
                    project_id,
                    task_id,
                    local_run_id,
                    patch_artifact_id,
                    status,
                    commands,
                    command_results,
                    started_at,
                    created_at
                )
                values (
                    'test_run_legacy',
                    'dev_workspace',
                    'project_one',
                    'task_one',
                    'local_run_one',
                    'patch_one',
                    'passed',
                    '["python -V"]',
                    '[]',
                    '2026-05-31 00:00:00',
                    '2026-05-31 00:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into patch_review (
                    id,
                    workspace_id,
                    project_id,
                    task_id,
                    local_run_id,
                    patch_artifact_id,
                    test_run_id,
                    reviewer_kind,
                    verdict,
                    issues,
                    required_changes,
                    created_at
                )
                values (
                    'review_legacy',
                    'dev_workspace',
                    'project_one',
                    'task_one',
                    'local_run_one',
                    'patch_one',
                    'test_run_legacy',
                    'deterministic',
                    'approved',
                    '[]',
                    '[]',
                    '2026-05-31 00:00:01'
                )
                """
            )
        )

    with engine.connect() as connection:
        connection = connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text("PRAGMA foreign_keys=ON"))
        assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    init_db(engine)

    with engine.begin() as connection:
        foreign_keys_enabled = connection.execute(
            text("PRAGMA foreign_keys")
        ).scalar_one()
        local_test_run_fks = {
            (row["from"], row["table"], row["to"])
            for row in connection.execute(
                text("PRAGMA foreign_key_list(local_test_run)")
            ).mappings()
        }
        patch_review_fks = {
            (row["from"], row["table"], row["to"])
            for row in connection.execute(
                text("PRAGMA foreign_key_list(patch_review)")
            ).mappings()
        }
        tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        local_test_run_id = connection.execute(
            text("SELECT id FROM local_test_run WHERE id='test_run_legacy'")
        ).scalar_one()
        patch_review_test_run_id = connection.execute(
            text("SELECT test_run_id FROM patch_review WHERE id='review_legacy'")
        ).scalar_one()
        foreign_key_check_rows = list(
            connection.execute(text("PRAGMA foreign_key_check")).mappings()
        )

    assert foreign_keys_enabled == 1
    assert ("project_id", "project", "id") in local_test_run_fks
    assert ("task_id", "task", "id") in local_test_run_fks
    assert ("local_run_id", "local_task_run", "id") in local_test_run_fks
    assert ("patch_artifact_id", "patch_artifact", "id") in local_test_run_fks
    assert ("test_run_id", "local_test_run", "id") in patch_review_fks
    assert "local_test_run_notnull_legacy" not in tables
    assert local_test_run_id == "test_run_legacy"
    assert patch_review_test_run_id == "test_run_legacy"
    assert all(
        table != "local_test_run_notnull_legacy"
        for _from, table, _to in local_test_run_fks | patch_review_fks
    )
    assert foreign_key_check_rows == []


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
