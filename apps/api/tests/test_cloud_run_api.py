import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import SQLModel, Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    CloudRunLogEntry,
    TaskEvent,
    GitHubCredential,
    LocalTaskRun,
    LocalTestRun,
    PatchArtifact,
    Project,
    Repository,
    SandboxProfile,
    Task,
    utc_now,
)
from ai_company_api.services.cloud_sandbox_executor import (
    CommandResult,
    SandboxExecutionRequest,
    SandboxExecutionResult,
)
from ai_company_api.services.object_storage import (
    ObjectStorageWrite,
    get_object_storage_provider,
)
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.task_state import TaskStatus


def build_client(database_path: Path) -> TestClient:
    database_url = f"sqlite:///{database_path.as_posix()}"
    init_db(build_engine(database_url))
    return TestClient(create_app(database_url=database_url))


def enqueue_and_process_cloud_run(
    client: TestClient,
    task_id: str,
    request_body: dict,
) -> dict:
    enqueue_response = client.post(f"/tasks/{task_id}/cloud-runs", json=request_body)
    assert enqueue_response.status_code == 201
    queued_result = enqueue_response.json()
    assert queued_result["cloud_run"]["status"] == "queued"
    assert queued_result["patch_artifact"] is None

    process_response = client.post(
        f"/cloud-runs/{queued_result['cloud_run']['id']}/process"
    )
    assert process_response.status_code == 200
    return process_response.json()


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


def test_cloud_run_log_payload_redacts_token_like_keys() -> None:
    from ai_company_api.services.cloud_runner import redact_sensitive_values

    payload = {
        "githubToken": "ghp_secret_token",
        "access_token": "access-secret",
        "visible": "kept",
        "nested": [{"nestedSecret": "inner-secret"}],
    }

    assert redact_sensitive_values(payload) == {
        "githubToken": "***REDACTED***",
        "access_token": "***REDACTED***",
        "visible": "kept",
        "nested": [{"nestedSecret": "***REDACTED***"}],
    }


def test_start_cloud_run_enqueues_fake_run_without_executor_work(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    response = client.post(f"/tasks/{task_id}/cloud-runs", json={"repo_id": repo_id})

    assert response.status_code == 201
    result = response.json()
    cloud_run = result["cloud_run"]
    assert cloud_run["status"] == "queued"
    assert cloud_run["sandbox_kind"] == "fake"
    assert cloud_run["head_branch"] == f"ai-scdc/task-{task_id}-{cloud_run['id']}"
    assert cloud_run["local_run_id"] is not None
    assert cloud_run["failure_reason"] is None
    assert result["patch_artifact"] is None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted_cloud_run = session.get(CloudRun, cloud_run["id"])
        local_run = session.get(LocalTaskRun, cloud_run["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
        task_after_enqueue = session.get(Task, task_id)
        log_entries = session.exec(
            select(CloudRunLogEntry).where(
                CloudRunLogEntry.cloud_run_id == cloud_run["id"],
            )
        ).all()

    assert persisted_cloud_run is not None
    assert local_run is not None
    assert task_after_enqueue is not None
    assert persisted_cloud_run.status == "queued"
    assert persisted_cloud_run.patch_artifact_id is None
    assert local_run.status == "queued"
    assert local_run.runner_kind == "fake"
    assert local_run.patch_artifact_id is None
    assert artifacts == []
    assert task_after_enqueue.status == TaskStatus.CREATED
    assert [entry.event for entry in log_entries] == ["queued"]


def test_start_cloud_run_accepts_phase_10b_provider_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "queue_provider": "local_db",
            "runtime_provider": "remote_stub",
            "storage_provider": "local_inline",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    cloud_run = payload["cloud_run"]
    assert cloud_run["queue_provider"] == "local_db"
    assert cloud_run["runtime_provider"] == "remote_stub"
    assert cloud_run["storage_provider"] == "local_inline"
    assert cloud_run["queue_message_id"] is None
    assert cloud_run["runtime_job_id"] is None
    assert cloud_run["artifact_manifest_uri"] is None
    assert cloud_run["log_stream_uri"] is None
    assert cloud_run["external_status"] is None
    assert cloud_run["external_error"] is None
    assert "queue_receipt" not in cloud_run

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted_cloud_run = session.get(CloudRun, cloud_run["id"])

    assert persisted_cloud_run is not None
    assert persisted_cloud_run.queue_provider == "local_db"
    assert persisted_cloud_run.runtime_provider == "remote_stub"
    assert persisted_cloud_run.storage_provider == "local_inline"
    assert persisted_cloud_run.queue_receipt is None


def _post_fake_cloud_run_with_provider_selection(
    tmp_path: Path,
    monkeypatch,
    body: dict,
):
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)

    return client.post(
        f"/tasks/{task.id}/cloud-runs",
        json={"repo_id": repository.id, **body},
    )


def test_start_cloud_run_rejects_unknown_provider_queue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = _post_fake_cloud_run_with_provider_selection(
        tmp_path,
        monkeypatch,
        {"queue_provider": "aws_sqs"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown cloud queue provider: aws_sqs"


def test_start_cloud_run_rejects_unknown_provider_storage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = _post_fake_cloud_run_with_provider_selection(
        tmp_path,
        monkeypatch,
        {"storage_provider": "s3"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Unknown object storage provider: s3"


def test_start_cloud_run_rejects_unknown_provider_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    response = _post_fake_cloud_run_with_provider_selection(
        tmp_path,
        monkeypatch,
        {"runtime_provider": "cloud_run_jobs"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unknown remote runtime provider: cloud_run_jobs"
    )


def test_docker_cloud_run_enqueue_stores_metadata_without_opening_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class VaultShouldNotOpen:
        def open(self, _encrypted_secret: str) -> str:
            raise AssertionError("vault should not open token during enqueue")

    class DockerExecutorShouldNotRun:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run during enqueue")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutorShouldNotRun(),
    )
    monkeypatch.setattr(cloud_runner, "DevSecretVault", VaultShouldNotOpen)
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
            "test_command_keys": ["test"],
        },
    )

    assert response.status_code == 201
    result = response.json()
    cloud_run = result["cloud_run"]
    assert cloud_run["status"] == "queued"
    assert cloud_run["sandbox_kind"] == "docker_local"
    assert cloud_run["sandbox_profile_id"] == profile_id
    assert cloud_run["patch_command_key"] == "patch"
    assert cloud_run["test_command_keys"] == ["test"]
    assert cloud_run["failure_reason"] is None
    assert result["patch_artifact"] is None
    assert "ghp_cloud_runner_secret1234" not in str(result)


def test_process_next_queued_fake_cloud_run_creates_patch_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued_response = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    )
    queued = queued_response.json()["cloud_run"]

    response = client.post(
        "/cloud-run-worker/process-next",
        params={"worker_id": "local-test-worker"},
    )

    assert response.status_code == 200
    result = response.json()
    cloud_run = result["cloud_run"]
    assert cloud_run["id"] == queued["id"]
    assert cloud_run["status"] == "patch_ready"
    assert cloud_run["worker_id"] == "local-test-worker"
    assert cloud_run["claimed_at"] is not None
    assert cloud_run["completed_at"] is not None
    assert result["patch_artifact"]["files_changed"] == ["AI_SCDC_CLOUD_RUN.md"]
    assert cloud_run["patch_artifact_id"] == result["patch_artifact"]["id"]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, cloud_run["local_run_id"])
        task_after_process = session.get(Task, task_id)
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]

    assert local_run is not None
    assert task_after_process is not None
    assert local_run.status == "patch_ready"
    assert task_after_process.status == TaskStatus.PATCH_READY
    assert log_events == ["queued", "claimed", "started", "patch_ready", "completed"]


def test_process_next_returns_no_content_when_queue_is_empty(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)

    response = client.post("/cloud-run-worker/process-next")

    assert response.status_code == 204
    assert response.content == b""


def test_claim_next_cloud_run_lease_marks_run_running(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["cloud_run"]["id"] == queued["id"]
    assert body["cloud_run"]["status"] == "running"
    assert body["cloud_run"]["worker_id"] == "remote-worker-1"
    assert body["cloud_run"]["remote_worker_kind"] == "remote_stub"
    assert body["cloud_run"]["queue_provider"] == "local_db"
    assert body["cloud_run"]["attempt_count"] == 1
    assert body["lease_id"] == body["cloud_run"]["lease_id"]
    assert body["lease_expires_at"] == body["cloud_run"]["lease_expires_at"]
    assert body["heartbeat_at"] == body["cloud_run"]["heartbeat_at"]
    assert body["cancel_requested"] is False

    second = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-2",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )
    assert second.status_code == 204

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert local_run is not None
    assert local_run.status == "running"
    assert "lease_claimed" in log_events


def test_claim_next_cloud_run_lease_retries_after_claim_race(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, first_task = create_cloud_task(session)
        second_task = Task(
            project_id=first_task.project_id,
            title="Later queued run",
            role_required="backend",
            status=TaskStatus.CREATED,
            allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
            required_tests=[],
        )
        session.add(second_task)
        session.commit()
        first_task_id = first_task.id
        second_task_id = second_task.id
        repo_id = repository.id

    first = client.post(
        f"/tasks/{first_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    second = client.post(
        f"/tasks/{second_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    real_claim = cloud_runner._claim_cloud_run_lease
    attempted_cloud_run_ids: list[str] = []

    def miss_first_candidate(*args, **kwargs):
        attempted_cloud_run_ids.append(kwargs["cloud_run_id"])
        if kwargs["cloud_run_id"] == first["id"]:
            return False
        return real_claim(*args, **kwargs)

    monkeypatch.setattr(cloud_runner, "_claim_cloud_run_lease", miss_first_candidate)

    response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["cloud_run"]["id"] == second["id"]
    assert body["cloud_run"]["status"] == "running"
    assert attempted_cloud_run_ids == [first["id"], second["id"]]


def test_claim_next_cloud_run_lease_retry_scan_is_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    candidate_limit = cloud_runner.DEFAULT_LEASE_CLAIM_CANDIDATE_LIMIT
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, first_task = create_cloud_task(session)
        tasks = [first_task]
        for index in range(candidate_limit + 3):
            task = Task(
                project_id=first_task.project_id,
                title=f"Queued run {index}",
                role_required="backend",
                status=TaskStatus.CREATED,
                allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
                required_tests=[],
            )
            session.add(task)
            tasks.append(task)
        session.commit()
        task_ids = [task.id for task in tasks]
        repo_id = repository.id

    for task_id in task_ids:
        response = client.post(
            f"/tasks/{task_id}/cloud-runs",
            json={"repo_id": repo_id},
        )
        assert response.status_code == 201

    attempted_cloud_run_ids: list[str] = []

    def miss_candidate(*args, **kwargs):
        attempted_cloud_run_ids.append(kwargs["cloud_run_id"])
        return False

    monkeypatch.setattr(cloud_runner, "_claim_cloud_run_lease", miss_candidate)

    response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 204
    assert len(attempted_cloud_run_ids) == candidate_limit


def test_claim_next_cloud_run_lease_skips_cancelled_and_exhausted_runs(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, first_task = create_cloud_task(session)
        second_task = Task(
            project_id=first_task.project_id,
            title="Exhausted queued run",
            role_required="backend",
            status=TaskStatus.CREATED,
            allowed_paths=["AI_SCDC_CLOUD_RUN.md"],
            required_tests=[],
        )
        session.add(second_task)
        session.commit()
        first_task_id = first_task.id
        second_task_id = second_task.id
        repo_id = repository.id

    cancelled = client.post(
        f"/tasks/{first_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    exhausted = client.post(
        f"/tasks/{second_task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    client.post(f"/cloud-runs/{cancelled['id']}/cancel")

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        exhausted_run = session.get(CloudRun, exhausted["id"])
        assert exhausted_run is not None
        exhausted_run.attempt_count = exhausted_run.max_attempts
        session.add(exhausted_run)
        session.commit()

    response = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    assert response.status_code == 204


def test_cloud_run_lease_heartbeat_extends_current_lease(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    client.post(f"/tasks/{task_id}/cloud-runs", json={"repo_id": repo_id})
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 30,
        },
    ).json()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "remote-worker-1", "lease_seconds": 120},
    )

    assert response.status_code == 200
    heartbeat = response.json()
    assert heartbeat["lease_id"] == lease["lease_id"]
    assert heartbeat["cloud_run"]["status"] == "running"
    assert heartbeat["cloud_run"]["worker_id"] == "remote-worker-1"
    assert heartbeat["lease_expires_at"] > lease["lease_expires_at"]
    assert heartbeat["heartbeat_at"] >= lease["heartbeat_at"]


def test_heartbeat_reports_running_cancel_request(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    cancel = client.post(f"/cloud-runs/{queued['id']}/cancel")

    heartbeat = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "remote-worker-1", "lease_seconds": 60},
    )

    assert cancel.status_code == 200
    assert cancel.json()["cancel_requested"] is True
    assert heartbeat.status_code == 200
    assert heartbeat.json()["cancel_requested"] is True
    assert heartbeat.json()["cloud_run"]["cancel_requested"] is True


def test_cloud_run_lease_heartbeat_rejects_stale_or_wrong_worker(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    client.post(f"/tasks/{task_id}/cloud-runs", json={"repo_id": repo_id})
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 30,
        },
    ).json()

    wrong_worker = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/heartbeat",
        json={"worker_id": "remote-worker-2", "lease_seconds": 120},
    )
    stale_lease = client.post(
        "/cloud-run-worker/leases/not-current/heartbeat",
        json={"worker_id": "remote-worker-1", "lease_seconds": 120},
    )

    assert wrong_worker.status_code == 409
    assert wrong_worker.json()["detail"] == "Cloud run lease is not current"
    assert stale_lease.status_code == 409
    assert stale_lease.json()["detail"] == "Cloud run lease is not current"


def test_requeue_expired_cloud_run_lease_returns_run_to_queue(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        cloud_run.lease_expires_at = datetime(2026, 6, 2, tzinfo=timezone.utc)
        session.add(cloud_run)
        session.commit()

    response = client.post(
        "/cloud-run-worker/leases/requeue-expired",
        json={"limit": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [queued["id"]]
    assert body[0]["status"] == "queued"
    assert body[0]["lease_id"] is None
    assert body[0]["worker_id"] is None
    assert body[0]["attempt_count"] == 1

    stale_completion = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )
    assert stale_completion.status_code == 409

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert local_run is not None
    assert local_run.status == "queued"
    assert "lease_expired" in log_events
    assert "run_requeued" in log_events


def test_requeue_expired_cloud_run_lease_fails_at_max_attempts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    )

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        cloud_run.attempt_count = cloud_run.max_attempts
        cloud_run.lease_expires_at = datetime(2026, 6, 2, tzinfo=timezone.utc)
        session.add(cloud_run)
        session.commit()

    response = client.post(
        "/cloud-run-worker/leases/requeue-expired",
        json={"limit": 25},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [queued["id"]]
    assert body[0]["status"] == "failed"
    assert body[0]["failure_reason"] == "lease_attempts_exhausted"
    assert body[0]["last_queue_error"] == "lease_attempts_exhausted"


def remote_stub_completion_payload(cloud_run_id: str) -> dict:
    return {
        "worker_id": "remote-worker-1",
        "result": {
            "status": "patch_ready",
            "runner_kind": "remote_stub",
            "base_sha": "base123",
            "head_sha": "head456",
            "worktree_ref": f"remote-stub://{cloud_run_id}",
            "summary": "Remote stub produced a patch artifact.",
            "files_changed": ["AI_SCDC_REMOTE_STUB.md"],
            "tests_run": [],
            "test_result": "not_run",
            "risks": [],
            "diff_text": "diff --git a/AI_SCDC_REMOTE_STUB.md b/AI_SCDC_REMOTE_STUB.md\n+remote\n",
            "command_results": [],
            "test_command_results": [],
            "failure_reason": None,
        },
    }


def test_complete_current_cloud_run_lease_creates_patch_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cloud_run"]["status"] == "patch_ready"
    assert body["cloud_run"]["lease_id"] == lease["lease_id"]
    assert body["cloud_run"]["remote_worker_kind"] == "remote_stub"
    assert body["patch_artifact"]["files_changed"] == ["AI_SCDC_REMOTE_STUB.md"]
    assert body["patch_artifact"]["test_result"] == "not_run"

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        task_after_completion = session.get(Task, task_id)
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert task_after_completion is not None
    assert local_run is not None
    assert task_after_completion.status == TaskStatus.PATCH_READY
    assert local_run.status == "patch_ready"
    assert "worker_completed" in log_events
    assert "patch_ready" in log_events


def test_complete_cloud_run_lease_uses_diff_artifact_ref(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "storage_provider": "local_inline"},
    ).json()["cloud_run"]
    diff_text = "diff --git a/app.py b/app.py\n+print('artifact')\n"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        ref = get_object_storage_provider("local_inline").put_text(
            session,
            ObjectStorageWrite(
                workspace_id=queued["workspace_id"],
                cloud_run_id=queued["id"],
                kind="diff",
                content=diff_text,
                content_type="text/x-diff",
            ),
        )
        session.commit()

    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    payload = remote_stub_completion_payload(queued["id"])
    payload["result"]["diff_text"] = "diff --git a/ignored.py b/ignored.py\n+ignored\n"
    payload["result"]["artifact_refs"] = [
        {
            "kind": ref.kind,
            "uri": ref.uri,
            "sha256": ref.sha256,
            "size_bytes": ref.size_bytes,
            "content_type": ref.content_type,
        }
    ]

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cloud_run"]["status"] == "patch_ready"
    assert body["patch_artifact"]["diff_text"] == diff_text

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        log_messages = [
            entry.message
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert any("artifact_ref" in message for message in log_messages)


def test_complete_cloud_run_lease_rejects_invalid_artifact_ref_without_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "storage_provider": "local_inline"},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    payload = remote_stub_completion_payload(queued["id"])
    payload["result"]["diff_text"] = ""
    payload["result"]["artifact_refs"] = [
        {
            "kind": "diff",
            "uri": "local-inline://cloud-run-objects/00000000000000000000000000000000",
            "sha256": "0" * 64,
            "size_bytes": 10,
            "content_type": "text/x-diff",
        }
    ]

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=payload,
    )

    assert response.status_code == 400

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        artifacts = session.exec(select(PatchArtifact)).all()
        cloud_run = session.get(CloudRun, queued["id"])
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
    assert artifacts == []
    assert cloud_run is not None
    assert cloud_run.patch_artifact_id is None
    assert local_run is not None
    assert local_run.patch_artifact_id is None


def test_complete_lease_after_cancel_request_finishes_cancelled_without_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    client.post(f"/cloud-runs/{queued['id']}/cancel")

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["cloud_run"]["status"] == "cancelled"
    assert body["cloud_run"]["cancel_requested"] is True
    assert body["cloud_run"]["cancelled_at"] is not None
    assert body["patch_artifact"] is None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        artifacts = session.exec(select(PatchArtifact)).all()
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
    assert artifacts == []
    assert local_run is not None
    assert local_run.status == "cancelled"


def test_complete_expired_cloud_run_lease_is_rejected_without_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        cloud_run.lease_expires_at = utc_now() - timedelta(seconds=1)
        session.add(cloud_run)
        session.commit()

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Cloud run lease is not current"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        artifacts = session.exec(select(PatchArtifact)).all()
        cloud_run = session.get(CloudRun, queued["id"])
        local_run = session.get(LocalTaskRun, queued["local_run_id"])

    assert artifacts == []
    assert cloud_run is not None
    assert cloud_run.status == "running"
    assert cloud_run.patch_artifact_id is None
    assert local_run is not None
    assert local_run.status == "running"
    assert local_run.patch_artifact_id is None


def test_complete_stale_cloud_run_lease_is_rejected_without_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    response = client.post(
        "/cloud-run-worker/leases/not-current/complete",
        json=remote_stub_completion_payload(queued["id"]),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Cloud run lease is not current"
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        artifacts = session.exec(select(PatchArtifact)).all()
    assert artifacts == []


def test_invalid_remote_completion_status_is_rejected_without_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    lease = client.post(
        "/cloud-run-worker/leases",
        json={
            "worker_id": "remote-worker-1",
            "worker_kind": "remote_stub",
            "lease_seconds": 60,
        },
    ).json()
    payload = remote_stub_completion_payload(queued["id"])
    payload["result"]["status"] = "running"

    response = client.post(
        f"/cloud-run-worker/leases/{lease['lease_id']}/complete",
        json=payload,
    )

    assert response.status_code == 422
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
    assert cloud_run is not None
    assert local_run is not None
    assert cloud_run.status == "running"
    assert local_run.status == "running"
    assert artifacts == []


def test_process_specific_docker_cloud_run_preserves_artifact_semantics(
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
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "patch_command_key": "patch",
            "test_command_keys": ["test"],
        },
    ).json()["cloud_run"]

    response = client.post(
        f"/cloud-runs/{queued['id']}/process",
        params={"worker_id": "docker-test-worker"},
    )

    assert response.status_code == 200
    result = response.json()
    cloud_run = result["cloud_run"]
    assert cloud_run["id"] == queued["id"]
    assert cloud_run["status"] == "patch_ready"
    assert cloud_run["sandbox_kind"] == "docker_local"
    assert cloud_run["worker_id"] == "docker-test-worker"
    assert cloud_run["patch_artifact_id"] == result["patch_artifact"]["id"]
    assert captured_requests[0].patch_command is not None
    assert captured_requests[0].patch_command.key == "patch"
    assert [command.key for command in captured_requests[0].test_commands] == ["test"]
    assert getattr(captured_requests[0], "github_token", None) == "ghp_cloud_runner_secret1234"


def test_processing_non_queued_cloud_run_returns_conflict(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    first = client.post(f"/cloud-runs/{queued['id']}/process")
    second = client.post(f"/cloud-runs/{queued['id']}/process")

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"] == "Cloud run is not queued"


def test_processing_claim_conflict_returns_conflict_without_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class FakeExecutorShouldNotRun:
        sandbox_kind = "fake"

        def run(self, _request):
            raise AssertionError("executor should not run after claim conflict")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: FakeExecutorShouldNotRun(),
    )
    monkeypatch.setattr(cloud_runner, "_claim_cloud_run", lambda *args, **kwargs: False)
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    response = client.post(f"/cloud-runs/{queued['id']}/process")

    assert response.status_code == 409
    assert response.json()["detail"] == "Cloud run is not queued"


def test_processing_docker_preflight_failure_marks_run_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutorShouldNotRun:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise AssertionError("executor should not run when profile is invalid")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutorShouldNotRun(),
    )
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id, "sandbox_profile_id": profile_id},
    ).json()["cloud_run"]

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        profile = session.get(SandboxProfile, profile_id)
        assert profile is not None
        profile.status = "deleted"
        session.add(profile)
        session.commit()

    response = client.post(f"/cloud-runs/{queued['id']}/process")

    assert response.status_code == 200
    result = response.json()
    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "failed"
    assert result["cloud_run"]["failure_reason"] == "cloud_run_preflight_failed"
    assert result["cloud_run"]["completed_at"] is not None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted = session.get(CloudRun, queued["id"])
        local_run = session.get(LocalTaskRun, queued["local_run_id"])

    assert persisted is not None
    assert local_run is not None
    assert persisted.status == "failed"
    assert local_run.status == "failed"


def test_cancel_queued_cloud_run_prevents_processing(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    response = client.post(f"/cloud-runs/{queued['id']}/cancel")

    assert response.status_code == 200
    cancelled = response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    assert cancelled["cancel_requested_at"] is not None
    assert cancelled["cancelled_at"] is not None
    assert cancelled["completed_at"] is not None

    process = client.post(f"/cloud-runs/{queued['id']}/process")
    assert process.status_code == 409

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert local_run is not None
    assert local_run.status == "cancelled"
    assert "cancelled" in log_events


def test_cancel_running_cloud_run_records_cancel_request(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        assert cloud_run is not None
        assert local_run is not None
        cloud_run.status = "running"
        local_run.status = "running"
        session.add(cloud_run)
        session.add(local_run)
        session.commit()

    response = client.post(f"/cloud-runs/{queued['id']}/cancel")

    assert response.status_code == 200
    running = response.json()
    assert running["status"] == "running"
    assert running["cancel_requested"] is True
    assert running["cancel_requested_at"] is not None
    assert running["cancelled_at"] is None
    assert running["completed_at"] is None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert "cancel_requested" in log_events


def test_running_cancel_request_prevents_artifact_when_worker_finishes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    client = build_client(database_path)

    class CancellingExecutor:
        sandbox_kind = "fake"

        def run(self, request):
            with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
                cancelled = cloud_runner.cancel_cloud_run(
                    session,
                    cloud_run_id=request.cloud_run_id,
                )
            assert cancelled.status == "running"
            assert cancelled.cancel_requested is True
            return SandboxExecutionResult(
                status="patch_ready",
                runner_kind="cloud_fake",
                base_sha="base123",
                head_sha="head456",
                worktree_ref=f"cloud://fake/{request.cloud_run_id}",
                summary="Executor finished after cancellation was requested.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["python -V"],
                test_result="passed",
                risks=[],
                diff_text="diff --git a/AI_SCDC_CLOUD_RUN.md b/AI_SCDC_CLOUD_RUN.md\n+patch\n",
                command_results=[
                    CommandResult(
                        command="apply patch",
                        exit_code=0,
                        stdout="patched",
                        stderr="",
                        duration_ms=10,
                    )
                ],
                test_command_results=[],
                failure_reason=None,
            )

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: CancellingExecutor(),
    )
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    response = client.post(f"/cloud-runs/{queued['id']}/process")

    assert response.status_code == 200
    result = response.json()
    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "cancelled"
    assert result["cloud_run"]["patch_artifact_id"] is None
    assert result["cloud_run"]["cancel_requested"] is True
    assert result["cloud_run"]["cancelled_at"] is not None
    assert result["cloud_run"]["completed_at"] is not None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted = session.get(CloudRun, queued["id"])
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert persisted is not None
    assert local_run is not None
    assert persisted.status == "cancelled"
    assert persisted.patch_artifact_id is None
    assert local_run.status == "cancelled"
    assert local_run.patch_artifact_id is None
    assert artifacts == []
    assert "cancel_requested" in log_events
    assert "cancelled" in log_events
    assert "patch_ready" not in log_events
    assert log_events.index("cancel_requested") < log_events.index("cancelled")
    assert log_events.index("cancelled") < log_events.index("completed")


def test_cancel_during_finalization_prevents_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    client = build_client(database_path)

    class PatchReadyExecutor:
        sandbox_kind = "fake"

        def run(self, request):
            return SandboxExecutionResult(
                status="patch_ready",
                runner_kind="cloud_fake",
                base_sha="base123",
                head_sha="head456",
                worktree_ref=f"cloud://fake/{request.cloud_run_id}",
                summary="Executor finished before cancellation was requested.",
                files_changed=["AI_SCDC_CLOUD_RUN.md"],
                tests_run=["python -V"],
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
        lambda: PatchReadyExecutor(),
    )
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    original_should_create_patch_artifact = cloud_runner._should_create_patch_artifact

    def cancel_before_artifact_creation(execution_result):
        with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
            cancelled = cloud_runner.cancel_cloud_run(
                session,
                cloud_run_id=queued["id"],
            )
        assert cancelled.status == "running"
        assert cancelled.cancel_requested is True
        return original_should_create_patch_artifact(execution_result)

    monkeypatch.setattr(
        cloud_runner,
        "_should_create_patch_artifact",
        cancel_before_artifact_creation,
    )

    response = client.post(f"/cloud-runs/{queued['id']}/process")

    assert response.status_code == 200
    result = response.json()
    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "cancelled"
    assert result["cloud_run"]["patch_artifact_id"] is None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
        log_events = [
            entry.event
            for entry in session.exec(
                select(CloudRunLogEntry)
                .where(CloudRunLogEntry.cloud_run_id == queued["id"])
                .order_by(CloudRunLogEntry.created_at, CloudRunLogEntry.id)
            ).all()
        ]
    assert local_run is not None
    assert local_run.status == "cancelled"
    assert local_run.patch_artifact_id is None
    assert artifacts == []
    assert "cancelled" in log_events
    assert "patch_ready" not in log_events


def test_cancel_after_finalization_lock_does_not_mutate_terminal_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    original_flush = Session.flush
    original_get_cloud_run_or_404 = cloud_runner._get_cloud_run_or_404
    worker_thread_id = None
    cancel_observed_running = threading.Event()
    cancel_finished = threading.Event()
    cancel_results = []
    cancel_errors = []
    cancel_thread_started = False

    def get_cloud_run_with_cancel_signal(session, cloud_run_id):
        cloud_run = original_get_cloud_run_or_404(session, cloud_run_id)
        if (
            worker_thread_id is not None
            and threading.get_ident() != worker_thread_id
            and cloud_run_id == queued["id"]
            and cloud_run.status == "running"
        ):
            cancel_observed_running.set()
        return cloud_run

    def request_cancel_after_worker_flush():
        try:
            with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
                cancel_results.append(
                    cloud_runner.cancel_cloud_run(session, cloud_run_id=queued["id"])
                )
        except Exception as exc:  # pragma: no cover - surfaced by assertions below
            cancel_errors.append(exc)
        finally:
            cancel_finished.set()

    def flush_with_cancel_race(self, *args, **kwargs):
        nonlocal cancel_thread_started, worker_thread_id
        should_start_cancel = (
            not cancel_thread_started
            and any(
                isinstance(item, LocalTaskRun)
                and item.id == queued["local_run_id"]
                and item.runner_kind == "cloud_fake"
                for item in self.dirty
            )
            and not any(isinstance(item, PatchArtifact) for item in self.new)
        )
        result = original_flush(self, *args, **kwargs)
        if should_start_cancel:
            cancel_thread_started = True
            worker_thread_id = threading.get_ident()
            thread = threading.Thread(target=request_cancel_after_worker_flush)
            thread.start()
            assert cancel_observed_running.wait(timeout=2)
        return result

    monkeypatch.setattr(cloud_runner, "_get_cloud_run_or_404", get_cloud_run_with_cancel_signal)
    monkeypatch.setattr(Session, "flush", flush_with_cancel_race)

    response = client.post(f"/cloud-runs/{queued['id']}/process")

    assert response.status_code == 200
    assert cancel_thread_started is True
    assert cancel_finished.wait(timeout=5)
    assert cancel_errors == []
    result = response.json()
    assert result["cloud_run"]["status"] == "patch_ready"
    assert result["patch_artifact"] is not None
    assert cancel_results
    assert cancel_results[0].status == "patch_ready"
    assert cancel_results[0].cancel_requested is False

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted = session.get(CloudRun, queued["id"])
    assert persisted is not None
    assert persisted.status == "patch_ready"
    assert persisted.cancel_requested is False


def test_cancel_between_recheck_and_finalization_claim_prevents_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    original_reload_claimed_cloud_run = cloud_runner._reload_claimed_cloud_run
    reload_count = 0

    def reload_then_cancel_on_second_check(*args, **kwargs):
        nonlocal reload_count
        result = original_reload_claimed_cloud_run(*args, **kwargs)
        reload_count += 1
        if reload_count == 2:
            with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
                cancelled = cloud_runner.cancel_cloud_run(
                    session,
                    cloud_run_id=queued["id"],
                )
            assert cancelled.status == "running"
            assert cancelled.cancel_requested is True
        return result

    monkeypatch.setattr(
        cloud_runner,
        "_reload_claimed_cloud_run",
        reload_then_cancel_on_second_check,
    )

    response = client.post(f"/cloud-runs/{queued['id']}/process")

    assert response.status_code == 200
    assert reload_count >= 2
    result = response.json()
    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "cancelled"
    assert result["cloud_run"]["patch_artifact_id"] is None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        local_run = session.get(LocalTaskRun, queued["local_run_id"])
        artifacts = session.exec(select(PatchArtifact)).all()
    assert local_run is not None
    assert local_run.status == "cancelled"
    assert local_run.patch_artifact_id is None
    assert artifacts == []


def test_cancel_queued_cloud_run_claim_race_records_cancel_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]

    def simulate_worker_claim(session: Session, *, cloud_run_id: str, now):
        with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as observer:
            cloud_run = observer.get(CloudRun, cloud_run_id)
            assert cloud_run is not None
            cloud_run.status = "running"
            cloud_run.worker_id = "racing-worker"
            cloud_run.claimed_at = now
            observer.add(cloud_run)
            observer.commit()
        return False

    monkeypatch.setattr(cloud_runner, "_cancel_queued_cloud_run", simulate_worker_claim)
    response = client.post(f"/cloud-runs/{queued['id']}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["worker_id"] == "racing-worker"
    assert body["cancel_requested"] is True
    assert body["cancel_requested_at"] is not None
    assert body["cancelled_at"] is None


def test_cloud_run_logs_are_ordered_and_redacted(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    client = build_client(database_path)
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        _project, repository, task = create_cloud_task(session)
        task_id = task.id
        repo_id = repository.id

    queued = client.post(
        f"/tasks/{task_id}/cloud-runs",
        json={"repo_id": repo_id},
    ).json()["cloud_run"]
    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        cloud_run = session.get(CloudRun, queued["id"])
        assert cloud_run is not None
        from ai_company_api.services.cloud_runner import _append_cloud_run_log

        _append_cloud_run_log(
            session,
            cloud_run=cloud_run,
            event="secret_payload",
            message="Secret payload captured.",
            payload={"githubToken": "ghp_should_not_leak"},
        )
        same_timestamp = datetime(2026, 6, 2, tzinfo=timezone.utc)
        session.add(
            CloudRunLogEntry(
                id="log_tie_b",
                cloud_run_id=cloud_run.id,
                workspace_id=cloud_run.workspace_id,
                event="same_time_b",
                message="Second by id.",
                created_at=same_timestamp,
            )
        )
        session.add(
            CloudRunLogEntry(
                id="log_tie_a",
                cloud_run_id=cloud_run.id,
                workspace_id=cloud_run.workspace_id,
                event="same_time_a",
                message="First by id.",
                created_at=same_timestamp,
            )
        )
        session.commit()

    client.post(f"/cloud-runs/{queued['id']}/process")
    response = client.get(f"/cloud-runs/{queued['id']}/logs")

    assert response.status_code == 200
    body = response.json()
    assert [entry["created_at"] for entry in body] == sorted(
        entry["created_at"] for entry in body
    )
    assert "queued" in {entry["event"] for entry in body}
    assert "completed" in {entry["event"] for entry in body}
    assert "ghp_should_not_leak" not in str(body)
    secret_entry = next(entry for entry in body if entry["event"] == "secret_payload")
    assert secret_entry["payload"]["githubToken"] == "***REDACTED***"
    same_time_events = [
        entry["event"]
        for entry in body
        if entry["event"] in {"same_time_a", "same_time_b"}
    ]
    assert same_time_events == ["same_time_a", "same_time_b"]


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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    payload = result["cloud_run"]["command_results"][0]
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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    assert captured_requests[0].patch_command is not None
    assert captured_requests[0].patch_command.key == "patch_default"
    assert [command.key for command in captured_requests[0].test_commands] == ["unit"]
    assert getattr(captured_requests[0], "github_token", None) == "ghp_cloud_runner_secret1234"
    assert result["cloud_run"]["patch_command_key"] == "patch_default"
    assert result["cloud_run"]["test_command_keys"] == ["unit"]


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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {
            "repo_id": repo_id,
            "sandbox_profile_id": profile_id,
            "patch_command_key": "patch",
            "test_command_keys": ["test"],
        },
    )
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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert result["cloud_run"]["status"] == "failed"
    assert observed["cloud_run_status"] == "running"
    assert observed["local_run_id"] is not None
    assert observed["local_run_status"] == "running"
    assert observed["event_type"] == "cloud_run_started"


def test_docker_cloud_run_executor_exception_marks_persisted_run_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_company_api.services import cloud_runner

    class DockerExecutor:
        sandbox_kind = "docker_local"

        def run(self, _request):
            raise RuntimeError("raw secret detail should not be exposed")

    monkeypatch.setattr(
        cloud_runner,
        "select_cloud_sandbox_executor",
        lambda: DockerExecutor(),
    )
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    init_db(build_engine(database_url))
    client = TestClient(
        create_app(database_url=database_url),
        raise_server_exceptions=False,
    )
    with Session(build_engine(database_url)) as session:
        project, repository, task = create_cloud_task(session)
        profile = create_profile_entity(session, project, repository)
        task_id = task.id
        repo_id = repository.id
        profile_id = profile.id

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "failed"
    assert result["cloud_run"]["failure_reason"] == "executor_failed"
    assert "raw secret detail" not in str(result)

    with Session(build_engine(database_url)) as session:
        cloud_run = session.get(CloudRun, result["cloud_run"]["id"])
        local_run = session.get(LocalTaskRun, result["cloud_run"]["local_run_id"])
        failed_event = session.exec(
            select(TaskEvent).where(
                TaskEvent.task_id == task_id,
                TaskEvent.event_type == "cloud_run_failed",
            )
        ).first()

    assert cloud_run is not None
    assert cloud_run.status == "failed"
    assert cloud_run.failure_reason == "executor_failed"
    assert local_run is not None
    assert local_run.status == "failed"
    assert local_run.failure_reason == "executor_failed"
    assert failed_event is not None
    assert failed_event.payload["failure_reason"] == "executor_failed"


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

    first_result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    second_result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert first_result["patch_artifact"] is None
    assert first_result["cloud_run"]["status"] == "failed"
    assert second_result["cloud_run"]["status"] == "patch_ready"
    assert second_result["patch_artifact"] is not None

    with Session(build_engine(f"sqlite:///{database_path.as_posix()}")) as session:
        persisted_task = session.get(Task, task_id)

    assert persisted_task is not None
    assert persisted_task.status == TaskStatus.PATCH_READY
    assert persisted_task.branch_name == (
        f"ai-scdc/task-{task_id}-{second_result['cloud_run']['id']}"
    )
    assert persisted_task.worktree_ref == (
        f"cloud://docker-local/{second_result['cloud_run']['id']}"
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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert result["patch_artifact"] is None
    assert result["cloud_run"]["status"] == "failed"
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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

    assert captured_requests[0].env == {"SANDBOX_TOKEN": "secret-token-value"}
    command_result = result["cloud_run"]["command_results"][0]
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

    result = enqueue_and_process_cloud_run(
        client,
        task.id,
        {"repo_id": repository.id},
    )

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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

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

    cloud_result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    patch_artifact_id = cloud_result["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

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

    cloud_result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    patch_artifact_id = cloud_result["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

    assert cloud_result["patch_artifact"]["test_result"] == "not_run"
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

    result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )

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

    cloud_result = enqueue_and_process_cloud_run(
        client,
        task_id,
        {"repo_id": repo_id, "sandbox_profile_id": profile_id},
    )
    patch_artifact_id = cloud_result["patch_artifact"]["id"]
    test_response = client.post(f"/patch-artifacts/{patch_artifact_id}/test-runs")
    review_response = client.post(f"/patch-artifacts/{patch_artifact_id}/reviews")

    assert test_response.status_code == 201
    assert test_response.json()["task"]["status"] == "FIX_REQUESTED"
    assert test_response.json()["test_run"]["status"] == "failed"
    assert test_response.json()["debug_attempt"]["status"] == "requested"
    assert "Test command failed" in test_response.json()["debug_attempt"]["root_cause"]
    assert review_response.status_code == 400
    assert review_response.json()["detail"]["current_status"] == "FIX_REQUESTED"


def test_init_db_adds_phase_9_cloud_run_columns_and_log_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase9.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    SQLModel.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE cloud_run RENAME TO cloud_run_old")
        connection.exec_driver_sql(
            """
            CREATE TABLE cloud_run (
                id VARCHAR NOT NULL PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                project_id VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                repo_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                sandbox_kind VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO cloud_run (
                id, workspace_id, project_id, task_id, repo_id, status, sandbox_kind, created_at, updated_at
            )
            SELECT id, workspace_id, project_id, task_id, repo_id, status, sandbox_kind, created_at, updated_at
            FROM cloud_run_old
            """
        )
        connection.exec_driver_sql("DROP TABLE cloud_run_old")
        connection.exec_driver_sql("DROP TABLE IF EXISTS cloud_run_log_entry")

    init_db(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cloud_run")}
    assert {
        "cancel_requested",
        "cancel_requested_at",
        "cancelled_at",
        "worker_id",
        "claimed_at",
        "completed_at",
    }.issubset(columns)
    assert "cloud_run_log_entry" in inspector.get_table_names()
    log_columns = {
        column["name"] for column in inspector.get_columns("cloud_run_log_entry")
    }
    assert {
        "id",
        "cloud_run_id",
        "workspace_id",
        "level",
        "event",
        "message",
        "payload",
        "created_at",
    }.issubset(log_columns)
    log_foreign_keys = inspector.get_foreign_keys("cloud_run_log_entry")
    assert any(
        foreign_key["constrained_columns"] == ["cloud_run_id"]
        and foreign_key["referred_table"] == "cloud_run"
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in log_foreign_keys
    )
    log_index_columns = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("cloud_run_log_entry")
    }
    assert ("cloud_run_id",) in log_index_columns
    assert ("workspace_id",) in log_index_columns
    assert ("level",) in log_index_columns
    assert ("event",) in log_index_columns
    assert ("created_at",) in log_index_columns


def test_init_db_adds_phase_10a_cloud_run_lease_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase10a.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    SQLModel.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE cloud_run RENAME TO cloud_run_old")
        connection.exec_driver_sql(
            """
            CREATE TABLE cloud_run (
                id VARCHAR NOT NULL PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                project_id VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                repo_id VARCHAR NOT NULL,
                local_run_id VARCHAR,
                base_branch VARCHAR NOT NULL,
                head_branch VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                sandbox_kind VARCHAR NOT NULL,
                cancel_requested BOOLEAN NOT NULL DEFAULT 0,
                worker_id VARCHAR,
                claimed_at DATETIME,
                completed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO cloud_run (
                id, workspace_id, project_id, task_id, repo_id, local_run_id,
                base_branch, head_branch, status, sandbox_kind, cancel_requested,
                worker_id, claimed_at, completed_at, created_at, updated_at
            )
            SELECT id, workspace_id, project_id, task_id, repo_id, local_run_id,
                base_branch, head_branch, status, sandbox_kind, cancel_requested,
                worker_id, claimed_at, completed_at, created_at, updated_at
            FROM cloud_run_old
            """
        )
        connection.exec_driver_sql("DROP TABLE cloud_run_old")

    init_db(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cloud_run")}
    assert {
        "queue_provider",
        "remote_worker_kind",
        "lease_id",
        "lease_expires_at",
        "heartbeat_at",
        "attempt_count",
        "max_attempts",
        "last_queue_error",
    }.issubset(columns)
    indexes = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("cloud_run")
    }
    assert ("queue_provider",) in indexes
    assert ("remote_worker_kind",) in indexes
    assert ("lease_id",) in indexes
    assert ("lease_expires_at",) in indexes


def test_init_db_adds_phase_10b_cloud_run_provider_columns(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase10b.db"
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    SQLModel.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE cloud_run RENAME TO cloud_run_old")
        connection.exec_driver_sql(
            """
            CREATE TABLE cloud_run (
                id VARCHAR NOT NULL PRIMARY KEY,
                workspace_id VARCHAR NOT NULL,
                project_id VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                repo_id VARCHAR NOT NULL,
                local_run_id VARCHAR,
                base_branch VARCHAR NOT NULL,
                head_branch VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                sandbox_kind VARCHAR NOT NULL,
                cancel_requested BOOLEAN NOT NULL DEFAULT 0,
                worker_id VARCHAR,
                claimed_at DATETIME,
                completed_at DATETIME,
                queue_provider VARCHAR NOT NULL DEFAULT 'local_db',
                remote_worker_kind VARCHAR,
                lease_id VARCHAR,
                lease_expires_at DATETIME,
                heartbeat_at DATETIME,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                last_queue_error VARCHAR,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO cloud_run (
                id, workspace_id, project_id, task_id, repo_id, local_run_id,
                base_branch, head_branch, status, sandbox_kind, cancel_requested,
                worker_id, claimed_at, completed_at, queue_provider,
                remote_worker_kind, lease_id, lease_expires_at, heartbeat_at,
                attempt_count, max_attempts, last_queue_error, created_at, updated_at
            )
            SELECT id, workspace_id, project_id, task_id, repo_id, local_run_id,
                base_branch, head_branch, status, sandbox_kind, cancel_requested,
                worker_id, claimed_at, completed_at, queue_provider,
                remote_worker_kind, lease_id, lease_expires_at, heartbeat_at,
                attempt_count, max_attempts, last_queue_error, created_at, updated_at
            FROM cloud_run_old
            """
        )
        connection.exec_driver_sql("DROP TABLE cloud_run_old")

    init_db(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cloud_run")}
    assert {
        "queue_message_id",
        "queue_receipt",
        "runtime_provider",
        "runtime_job_id",
        "storage_provider",
        "artifact_manifest_uri",
        "log_stream_uri",
        "external_status",
        "external_error",
    }.issubset(columns)
    indexes = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("cloud_run")
    }
    assert ("queue_message_id",) in indexes
    assert ("runtime_provider",) in indexes
    assert ("runtime_job_id",) in indexes
    assert ("storage_provider",) in indexes
    assert ("external_status",) in indexes


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

    cloud_result = enqueue_and_process_cloud_run(
        client,
        task.id,
        {"repo_id": repository.id},
    )
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

    cloud_result = enqueue_and_process_cloud_run(
        client,
        task.id,
        {"repo_id": repository.id},
    )
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

    cloud_result = enqueue_and_process_cloud_run(
        client,
        task.id,
        {"repo_id": repository.id},
    )
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
