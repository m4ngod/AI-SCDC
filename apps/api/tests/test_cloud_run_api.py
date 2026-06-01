from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    TaskEvent,
    GitHubCredential,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    Project,
    Repository,
    SandboxProfile,
    Task,
)
from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
)
from ai_company_api.services.secret_vault import DevSecretVault
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
    sealed = DevSecretVault().seal("ghp_cloud_runner_secret1234")
    credential = GitHubCredential(
        display_name="Cloud runner credential",
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
        provider=provider,
        repo_url="https://github.com/example/demo",
        github_owner="example",
        github_repo="demo",
        github_credential_id=credential.id,
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


def create_profile_entity(
    session: Session,
    project: Project,
    repository: Repository,
    *,
    docker_image: str = "python:3.11-bookworm",
    patch_commands: list[dict] | None = None,
    test_commands: list[dict] | None = None,
    allowed_env_vars: list[str] | None = None,
    network_enabled: bool = True,
    status: str = "active",
) -> SandboxProfile:
    profile = SandboxProfile(
        project_id=project.id,
        repo_id=repository.id,
        name="Default docker sandbox",
        docker_image=docker_image,
        patch_commands=patch_commands
        if patch_commands is not None
        else [
            {
                "key": "patch",
                "label": "Patch",
                "command": "python patch.py",
                "timeout_seconds": 120,
                "is_default": True,
            }
        ],
        test_commands=test_commands
        if test_commands is not None
        else [
            {
                "key": "test",
                "label": "Test",
                "command": "pytest -q",
                "timeout_seconds": 300,
                "is_default": True,
            }
        ],
        allowed_env_vars=[] if allowed_env_vars is None else allowed_env_vars,
        network_enabled=network_enabled,
        status=status,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


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
            "timed_out": True,
        }
    ]


def test_docker_cloud_run_redacts_github_token_from_persisted_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                base_sha=None,
                head_sha=None,
                worktree_ref=None,
                summary="",
                files_changed=[],
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command=f"git clone {request.github_token}",
                        exit_code=1,
                        stdout=f"stdout {request.github_token}",
                        stderr=f"stderr {request.github_token}",
                        duration_ms=10,
                    )
                ],
                test_command_results=[],
                failure_reason="repo_checkout_failed",
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 201
    payload = response.json()["cloud_run"]["command_results"][0]
    assert "ghp_cloud_runner_secret1234" not in str(payload)
    assert payload["command"] == "git clone [redacted]"
    assert payload["stdout"] == "stdout [redacted]"
    assert payload["stderr"] == "stderr [redacted]"


def test_docker_cloud_run_requires_active_sandbox_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AI_SCDC_CLOUD_RUNNER", "docker_local")
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Docker cloud runs require a sandbox profile"


def test_docker_cloud_run_validates_profile_before_opening_github_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class VaultShouldNotOpen:
        def open(self, _encrypted_secret: str) -> str:
            raise AssertionError("vault should not open token before profile validation")

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    monkeypatch.setattr(cloud_runner, "DevSecretVault", VaultShouldNotOpen)
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": "sandbox_profile_missing"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Sandbox profile not found"


def test_docker_cloud_run_rejects_unknown_patch_command_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "patch_command_key": "missing",
            "test_command_keys": ["test"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown sandbox patch command key"


def test_docker_cloud_run_rejects_unknown_test_command_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "patch_command_key": "patch",
            "test_command_keys": ["missing"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown sandbox test command key"


def test_docker_cloud_run_uses_default_profile_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    captured_requests: list[SandboxExecutionRequest] = []

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            captured_requests.append(request)
            return SandboxExecutionResult(
                status="patch_ready",
                runner_kind="docker_local",
                base_sha="abc123",
                head_sha="def456",
                worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["pytest -q"],
                test_result="passed",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[],
                test_command_results=[],
                failure_reason=None,
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(
            session,
            project,
            repository,
            patch_commands=[
                {
                    "key": "patch_default",
                    "label": "Patch default",
                    "command": "python patch.py",
                    "timeout_seconds": 120,
                    "is_default": True,
                },
                {
                    "key": "patch_alt",
                    "label": "Patch alt",
                    "command": "python patch-alt.py",
                    "timeout_seconds": 90,
                    "is_default": False,
                },
            ],
            test_commands=[
                {
                    "key": "unit",
                    "label": "Unit",
                    "command": "pytest -q",
                    "timeout_seconds": 300,
                    "is_default": True,
                },
                {
                    "key": "lint",
                    "label": "Lint",
                    "command": "ruff check .",
                    "timeout_seconds": 60,
                    "is_default": False,
                },
            ],
        )
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 201
    assert captured_requests[0].patch_command is not None
    assert captured_requests[0].patch_command.key == "patch_default"
    assert [command.key for command in captured_requests[0].test_commands] == ["unit"]
    assert getattr(captured_requests[0], "github_token", None) == "ghp_cloud_runner_secret1234"
    assert response.json()["cloud_run"]["patch_command_key"] == "patch_default"
    assert response.json()["cloud_run"]["test_command_keys"] == ["unit"]


def test_docker_cloud_run_records_docker_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    captured_requests: list[SandboxExecutionRequest] = []

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            captured_requests.append(request)
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                base_sha=None,
                head_sha=None,
                worktree_ref=None,
                summary="",
                files_changed=[],
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command="docker version",
                        exit_code=1,
                        stdout="",
                        stderr="docker unavailable",
                        duration_ms=3,
                    )
                ],
                test_command_results=[],
                failure_reason="docker_unavailable",
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(
            session,
            project,
            repository,
            docker_image="python:3.12-bookworm",
            network_enabled=False,
        )
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "patch_command_key": "patch",
            "test_command_keys": ["test"],
        },
    )

    assert response.status_code == 201
    result = response.json()
    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "failed"
    assert result["cloud_run"]["failure_reason"] == "docker_unavailable"
    assert result["cloud_run"]["sandbox_profile_id"] == profile_id
    assert result["cloud_run"]["patch_command_key"] == "patch"
    assert result["cloud_run"]["test_command_keys"] == ["test"]
    assert captured_requests[0].docker_image == "python:3.12-bookworm"
    assert captured_requests[0].network_enabled is False

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, result["cloud_run"]["id"])
        artifacts = session.exec(select(PatchArtifact)).all()
        task_after_failure = session.get(Task, task_id)

    assert cloud_run is not None
    assert cloud_run.sandbox_profile_id == profile_id
    assert cloud_run.patch_command_key == "patch"
    assert cloud_run.test_command_keys == ["test"]
    assert artifacts == []
    assert task_after_failure is not None
    assert task_after_failure.status == TaskStatus.CREATED
    assert task_after_failure.branch_name is None
    assert task_after_failure.worktree_ref is None


def test_docker_cloud_run_started_state_is_committed_before_executor_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    observed: dict[str, str | None] = {}

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            with Session(engine) as observer:
                cloud_run = observer.get(CloudRun, request.cloud_run_id)
                observed["cloud_run_status"] = cloud_run.status if cloud_run else None
                observed["local_run_id"] = cloud_run.local_run_id if cloud_run else None
                local_run = (
                    observer.get(LocalTaskRun, cloud_run.local_run_id)
                    if cloud_run and cloud_run.local_run_id
                    else None
                )
                observed["local_run_status"] = local_run.status if local_run else None
                event = observer.exec(
                    select(TaskEvent).where(
                        TaskEvent.task_id == request.task_id,
                        TaskEvent.event_type == "cloud_run_started",
                    )
                ).first()
                observed["event_type"] = event.event_type if event else None
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                base_sha=None,
                head_sha=None,
                worktree_ref=None,
                summary="",
                files_changed=[],
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command="docker version",
                        exit_code=1,
                        stdout="",
                        stderr="docker unavailable",
                        duration_ms=3,
                    )
                ],
                test_command_results=[],
                failure_reason="docker_unavailable",
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    client = build_client(database_path)
    with Session(engine) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 201
    assert observed["cloud_run_status"] == "running"
    assert observed["local_run_id"] is not None
    assert observed["local_run_status"] == "running"
    assert observed["event_type"] == "cloud_run_started"


def test_docker_cloud_run_can_retry_after_setup_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def __init__(self) -> None:
            self.calls = 0

        def run(self, request):
            self.calls += 1
            if self.calls == 1:
                return SandboxExecutionResult(
                    status="failed",
                    runner_kind="docker_local",
                    base_sha=None,
                    head_sha=None,
                    worktree_ref=None,
                    summary="",
                    files_changed=[],
                    tests_run=[],
                    test_result="not_run",
                    risks=[],
                    diff_text="",
                    command_results=[
                        CommandResult(
                            command="docker version",
                            exit_code=1,
                            stdout="",
                            stderr="docker unavailable",
                            duration_ms=3,
                        )
                    ],
                    test_command_results=[],
                    failure_reason="docker_unavailable",
                )
            return SandboxExecutionResult(
                status="patch_ready",
                runner_kind="docker_local",
                base_sha="abc123",
                head_sha="def456",
                worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["pytest -q"],
                test_result="passed",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[],
                test_command_results=[
                    CommandResult(
                        command="pytest -q",
                        exit_code=0,
                        stdout="passed",
                        stderr="",
                        duration_ms=10,
                    )
                ],
                failure_reason=None,
            )

    executor = DockerExecutor()
    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: executor,
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    first_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    second_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert first_response.status_code == 201
    assert first_response.json()["patch_artifact"] is None
    assert second_response.status_code == 201
    assert second_response.json()["cloud_run"]["status"] == "patch_ready"
    assert second_response.json()["patch_artifact"] is not None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted_task = session.get(Task, task_id)

    assert persisted_task is not None
    assert persisted_task.status == TaskStatus.PATCH_READY
    assert persisted_task.branch_name == (
        f"ai-scdc/task-{task_id}-{second_response.json()['cloud_run']['id']}"
    )
    assert persisted_task.worktree_ref == (
        f"cloud://docker-local/{second_response.json()['cloud_run']['id']}"
    )


def test_docker_cloud_run_setup_failure_preserves_existing_patch_ready_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
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
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command="docker version",
                        exit_code=1,
                        stdout="",
                        stderr="docker unavailable",
                        duration_ms=3,
                    )
                ],
                test_command_results=[],
                failure_reason="docker_unavailable",
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task.status = TaskStatus.PATCH_READY
        task.branch_name = "ai-scdc/task-existing"
        task.worktree_ref = "cloud://docker-local/existing"
        session.add(task)
        session.commit()
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 201
    assert response.json()["patch_artifact"] is None
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted_task = session.get(Task, task_id)

    assert persisted_task is not None
    assert persisted_task.status == TaskStatus.PATCH_READY
    assert persisted_task.branch_name == "ai-scdc/task-existing"
    assert persisted_task.worktree_ref == "cloud://docker-local/existing"


def test_docker_cloud_run_allowed_env_vars_whitelist_and_redact_results(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    captured_requests: list[SandboxExecutionRequest] = []

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            captured_requests.append(request)
            secret = request.env["SANDBOX_TOKEN"]
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                base_sha=None,
                head_sha=None,
                worktree_ref=None,
                summary="",
                files_changed=[],
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="",
                command_results=[
                    CommandResult(
                        command=f"echo {secret}",
                        exit_code=1,
                        stdout=f"saw {secret}",
                        stderr=f"failed {secret}",
                        duration_ms=3,
                    )
                ],
                test_command_results=[],
                failure_reason="docker_unavailable",
            )

    monkeypatch.setenv("SANDBOX_TOKEN", "secret-token-value")
    monkeypatch.setenv("IGNORED_TOKEN", "ignored-token-value")
    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(
            session,
            project,
            repository,
            allowed_env_vars=["SANDBOX_TOKEN", "MISSING_TOKEN"],
        )
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 201
    assert captured_requests[0].env == {"SANDBOX_TOKEN": "secret-token-value"}
    command_result = response.json()["cloud_run"]["command_results"][0]
    assert command_result["command"] == "echo [redacted]"
    assert command_result["stdout"] == "saw [redacted]"
    assert command_result["stderr"] == "failed [redacted]"


def test_docker_cloud_run_rejects_credentials_embedded_in_repo_url_before_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run for userinfo repo URL")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        repository.repo_url = "https://user:secret-token@github.com/example/demo"
        session.add(repository)
        session.commit()
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub repository URL must not include credentials"


def test_docker_cloud_run_rejects_tampered_github_url_before_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run for tampered repo metadata")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        repository.repo_url = "https://github.com/example/other"
        session.add(repository)
        session.commit()
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub repository URL must match owner/repo"


def test_docker_cloud_run_rejects_encoded_slash_repo_metadata_before_executor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run for encoded slash repo")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        repository.repo_url = "https://github.com/example/demo%2Fsecret"
        repository.github_repo = "demo%2Fsecret"
        session.add(repository)
        session.commit()
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "GitHub owner and repo must be single path segments"


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
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

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
            "timed_out": False,
        }
    ]
    assert test_runs[0].failure_reason is None


def test_docker_cloud_run_persisted_passing_tests_can_review_without_rerun(
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
                command_results=[],
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

    def fail_if_rerun(request):
        raise AssertionError(f"RUN_TESTS should not run against {request.worktree_path}")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerResultExecutor(),
    )
    monkeypatch.setattr(
        "ai_company_api.services.test_review_debug.RUN_TESTS",
        fail_if_rerun,
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    cloud_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    patch_artifact_id = cloud_response.json()["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

    assert cloud_response.status_code == 201
    assert test_response.status_code == 201
    assert test_response.json()["task"]["status"] == "REVIEWING"
    assert test_response.json()["test_run"]["status"] == "passed"
    assert test_response.json()["test_run"]["command_results"][0]["stdout"] == (
        "Python 3.11\n"
    )
    assert review_response.status_code == 201
    assert review_response.json()["task"]["status"] == "APPROVED"
    assert review_response.json()["review"]["verdict"] == "approved"


def test_docker_cloud_run_without_tests_can_review_after_synthetic_test_bridge(
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
                tests_run=[],
                test_result="not_run",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[],
                test_command_results=[],
                failure_reason=None,
            )

    def fail_if_rerun(request):
        raise AssertionError(f"RUN_TESTS should not run against {request.worktree_path}")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerResultExecutor(),
    )
    monkeypatch.setattr(
        "ai_company_api.services.test_review_debug.RUN_TESTS",
        fail_if_rerun,
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session, required_tests=[])
        profile = create_profile_entity(
            session,
            project,
            repository,
            test_commands=[],
        )
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    cloud_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    patch_artifact_id = cloud_response.json()["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

    assert cloud_response.status_code == 201
    assert cloud_response.json()["patch_artifact"]["test_result"] == "not_run"
    assert test_response.status_code == 201
    assert test_response.json()["task"]["status"] == "REVIEWING"
    assert test_response.json()["patch_artifact"]["test_result"] == "passed"
    assert test_response.json()["patch_artifact"]["tests_run"] == []
    assert test_response.json()["test_run"]["status"] == "passed"
    assert test_response.json()["test_run"]["commands"] == []
    assert test_response.json()["test_run"]["command_results"] == []
    assert test_response.json()["debug_attempt"] is None
    assert review_response.status_code == 201
    assert review_response.json()["task"]["status"] == "APPROVED"
    assert review_response.json()["review"]["verdict"] == "approved"


def test_docker_cloud_run_test_failure_keeps_patch_artifact(
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
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["python -V"],
                test_result="failed",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[
                    CommandResult(
                        command="python patch.py",
                        exit_code=0,
                        stdout="patched",
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
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert response.status_code == 201
    result = response.json()
    assert result["cloud_run"]["status"] == "failed"
    assert result["cloud_run"]["failure_reason"] == "test_failed"
    assert result["patch_artifact"] is not None
    assert result["patch_artifact"]["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert result["patch_artifact"]["test_result"] == "failed"
    assert result["cloud_run"]["patch_artifact_id"] == result["patch_artifact"]["id"]
    assert [item["command"] for item in result["cloud_run"]["command_results"]] == [
        "python patch.py",
    ]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, result["cloud_run"]["id"])
        local_run = session.get(LocalTaskRun, result["cloud_run"]["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
        test_runs = session.exec(select(LocalTestRun)).all()
        persisted_task = session.get(Task, task_id)

    assert cloud_run is not None
    assert local_run is not None
    assert persisted_task is not None
    assert len(artifacts) == 1
    assert artifacts[0].id == result["patch_artifact"]["id"]
    assert len(test_runs) == 1
    assert test_runs[0].patch_artifact_id == artifacts[0].id
    assert test_runs[0].status == "failed"
    assert test_runs[0].commands == ["python -V"]
    assert test_runs[0].command_results == [
        {
            "command": "python -V",
            "exit_code": 1,
            "stdout": "",
            "stderr": "failed",
            "duration_ms": 3,
            "timed_out": False,
        }
    ]
    assert test_runs[0].failure_reason == "test_failed"
    assert cloud_run.patch_artifact_id == artifacts[0].id
    assert cloud_run.failure_reason == "test_failed"
    assert local_run.patch_artifact_id == artifacts[0].id
    assert local_run.failure_reason == "test_failed"
    assert persisted_task.status == TaskStatus.PATCH_READY

    read_response = client.get(f"/test-runs/{test_runs[0].id}")
    assert read_response.status_code == 200
    assert read_response.json()["patch_artifact_id"] == artifacts[0].id


def test_docker_cloud_run_persisted_failed_tests_request_fix_without_rerun(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FailedDockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, request):
            return SandboxExecutionResult(
                status="failed",
                runner_kind="docker_local",
                base_sha="abc123",
                head_sha="def456",
                worktree_ref=f"cloud://docker-local/{request.cloud_run_id}",
                summary="Docker local sandbox produced a patch artifact.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["python -V"],
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
                        duration_ms=3,
                    )
                ],
                failure_reason="test_failed",
            )

    def fail_if_rerun(request):
        raise AssertionError(f"RUN_TESTS should not run against {request.worktree_path}")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FailedDockerExecutor(),
    )
    monkeypatch.setattr(
        "ai_company_api.services.test_review_debug.RUN_TESTS",
        fail_if_rerun,
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    cloud_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    patch_artifact_id = cloud_response.json()["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

    assert cloud_response.status_code == 201
    assert test_response.status_code == 201
    assert test_response.json()["task"]["status"] == "FIX_REQUESTED"
    assert test_response.json()["test_run"]["status"] == "failed"
    assert test_response.json()["debug_attempt"]["status"] == "requested"
    assert "Test command failed" in test_response.json()["debug_attempt"]["root_cause"]
    assert review_response.status_code == 400
    assert review_response.json()["detail"]["current_status"] == "FIX_REQUESTED"


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
