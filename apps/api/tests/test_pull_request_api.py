from pathlib import Path
import json
import subprocess

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
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
    PullRequestRecord,
    Repository,
    Task,
)
from ai_company_api.services.github_pull_request import (
    CreatedPullRequest,
    GitHubPullRequestAdapter,
    create_pull_request_for_approval,
)
from ai_company_api.services.secret_vault import DevSecretVault
from ai_company_api.services.task_state import TaskStatus


def build_client(database_path: Path) -> TestClient:
    database_url = f"sqlite:///{database_path.as_posix()}"
    init_db(build_engine(database_url))
    return TestClient(create_app(database_url=database_url))


def build_database_session(database_path: Path) -> Session:
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    init_db(engine)
    return Session(engine)


def count_events(events: list[dict], event_type: str) -> int:
    return sum(1 for event in events if event["event_type"] == event_type)


class _FakeUrlOpenResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "number": 7,
                "html_url": "https://github.com/example/demo/pull/7",
            }
        ).encode("utf-8")


def create_approved_cloud_patch(
    session: Session,
    *,
    task_status: TaskStatus = TaskStatus.HUMAN_APPROVAL,
) -> tuple[Project, Repository, Task, CloudRun, PatchArtifact, PatchApproval]:
    project = Project(name="GitHub PR project")
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
        title="Create pull request",
        role_required="backend",
        status=task_status,
        allowed_paths=["README.md"],
        required_tests=["python -V"],
        repo_id=repository.id,
        branch_name="main",
        worktree_ref="cloud://fake/pending",
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
        head_branch=f"ai-scdc/task-{task.id}",
        status="patch_ready",
        sandbox_kind="fake",
    )
    session.add(cloud_run)
    session.flush()

    artifact = PatchArtifact(
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
    session.add(artifact)
    session.flush()

    local_run.patch_artifact_id = artifact.id
    cloud_run.patch_artifact_id = artifact.id
    session.add(local_run)
    session.add(cloud_run)

    test_run = LocalTestRun(
        project_id=project.id,
        task_id=task.id,
        local_run_id=local_run.id,
        patch_artifact_id=artifact.id,
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
        merge_instructions="Open a pull request for review.",
    )
    session.add(approval)
    session.commit()

    for entity in (project, repository, task, cloud_run, artifact, approval):
        session.refresh(entity)
    return project, repository, task, cloud_run, artifact, approval


def test_create_pull_request_requires_human_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _repository, _task, _cloud_run, _artifact, approval = (
            create_approved_cloud_patch(session, task_status=TaskStatus.MERGE_READY)
        )
        approval_id = approval.id

    with build_client(database_path) as client:
        response = client.post(f"/patch-approvals/{approval_id}/pull-requests")

    assert response.status_code == 400
    assert response.json()["detail"]["expected_status"] == "HUMAN_APPROVAL"


def test_real_adapter_keeps_token_out_of_git_argv_and_sets_commit_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_company_api.services import github_pull_request

    token = "ghp_raw_secret_token"
    repo_url = "https://github.com/example/demo"
    calls: list[dict] = []

    def fake_run(
        args,
        *,
        cwd=None,
        check=False,
        capture_output=False,
        text=False,
        timeout=None,
        env=None,
    ):
        calls.append(
            {
                "args": [str(item) for item in args],
                "cwd": cwd,
                "check": check,
                "capture_output": capture_output,
                "text": text,
                "timeout": timeout,
                "env": env,
            }
        )
        if args[1] == "clone":
            Path(args[3]).mkdir(parents=True)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="",
            stderr="",
        )

    def fake_urlopen(api_request, timeout):
        assert api_request.headers["Authorization"] == f"Bearer {token}"
        assert timeout == 30
        return _FakeUrlOpenResponse()

    monkeypatch.setattr(github_pull_request.subprocess, "run", fake_run)
    monkeypatch.setattr(github_pull_request.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        github_pull_request.tempfile,
        "TemporaryDirectory",
        lambda prefix: tempfile_directory(tmp_path, prefix),
    )

    created = GitHubPullRequestAdapter().create_pull_request(
        owner="example",
        repo="demo",
        repo_url=repo_url,
        token=token,
        base_branch="main",
        head_branch="ai-scdc/task-1",
        diff_text="",
        title="Create pull request",
        body="Body",
    )

    all_argv = [item for call in calls for item in call["args"]]
    clone_call = next(call for call in calls if call["args"][1] == "clone")
    commit_call = next(call for call in calls if "commit" in call["args"])

    assert created.number == 7
    assert created.url == "https://github.com/example/demo/pull/7"
    assert token not in " ".join(all_argv)
    assert clone_call["args"][2] == repo_url
    assert clone_call["env"]["AI_SCDC_GIT_TOKEN"] == token
    assert "'credential.helper='" in clone_call["env"]["GIT_CONFIG_PARAMETERS"]
    assert clone_call["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_ASKPASS" in clone_call["env"]
    assert "-c" in commit_call["args"]
    assert "user.email=ai-scdc@example.local" in commit_call["args"]
    assert "user.name=AI SCDC" in commit_call["args"]


def test_create_pull_request_uses_fake_adapter_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _repository, task, _cloud_run, _artifact, approval = (
            create_approved_cloud_patch(session)
        )
        task_id = task.id
        approval_id = approval.id

    with build_client(database_path) as client:
        first = client.post(f"/patch-approvals/{approval_id}/pull-requests")
        second = client.post(f"/patch-approvals/{approval_id}/pull-requests")
        events = client.get(f"/tasks/{task_id}/events").json()

    assert first.status_code == 201
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["task"]["status"] == "PR_CREATED"
    assert first_body["pull_request"]["github_pr_url"] == (
        "https://github.com/example/demo/pull/1"
    )
    assert second_body["pull_request"]["id"] == first_body["pull_request"]["id"]
    assert count_events(events, "pull_request_created") == 1


def test_create_pull_request_uses_real_adapter_when_env_var_is_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_company_api.services import github_pull_request

    calls: list[dict] = []

    def fake_create_pull_request(self, **kwargs) -> CreatedPullRequest:
        calls.append(kwargs)
        return CreatedPullRequest(
            number=88,
            url="https://github.com/example/demo/pull/88",
        )

    monkeypatch.setenv("AI_SCDC_GITHUB_PR_ADAPTER", "real")
    monkeypatch.setattr(
        github_pull_request.GitHubPullRequestAdapter,
        "create_pull_request",
        fake_create_pull_request,
    )

    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _repository, _task, _cloud_run, _artifact, approval = (
            create_approved_cloud_patch(session)
        )
        approval_id = approval.id

        result, status_code = create_pull_request_for_approval(session, approval_id)

    assert status_code == 201
    assert result.pull_request.github_pr_number == 88
    assert result.pull_request.github_pr_url == "https://github.com/example/demo/pull/88"
    assert len(calls) == 1
    assert calls[0]["owner"] == "example"
    assert calls[0]["repo"] == "demo"


def test_create_pull_request_rejects_tampered_repo_url_before_opening_token(
    tmp_path: Path,
) -> None:
    class RaisingVault:
        def seal(self, _secret_value: str):
            raise AssertionError("seal should not be called")

        def open(self, _encrypted_secret: str) -> str:
            raise AssertionError("vault should not open token for tampered repo URL")

    class FailingAdapter:
        def create_pull_request(self, **_kwargs):
            raise AssertionError("adapter should not be called for tampered repo URL")

    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, repository, _task, _cloud_run, _artifact, approval = (
            create_approved_cloud_patch(session)
        )
        repository.repo_url = "https://github.com/example/other"
        session.add(repository)
        session.commit()
        approval_id = approval.id

        with pytest.raises(HTTPException) as exc_info:
            create_pull_request_for_approval(
                session,
                approval_id,
                adapter=FailingAdapter(),
                vault=RaisingVault(),
            )

        records = session.exec(select(PullRequestRecord)).all()

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "GitHub repository URL must match owner/repo"
    assert records == []


def test_creating_pull_request_record_returns_conflict_without_new_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_company_api.services import github_pull_request

    class FailingAdapter:
        def create_pull_request(self, **_kwargs):
            raise AssertionError("adapter should not be called")

    monkeypatch.setattr(github_pull_request, "DEFAULT_ADAPTER", FailingAdapter())

    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, repository, task, cloud_run, artifact, approval = (
            create_approved_cloud_patch(session)
        )
        record = PullRequestRecord(
            project_id=task.project_id,
            task_id=task.id,
            repo_id=repository.id,
            patch_artifact_id=artifact.id,
            patch_approval_id=approval.id,
            cloud_run_id=cloud_run.id,
            head_branch=cloud_run.head_branch,
            base_branch=cloud_run.base_branch,
            github_pr_number=0,
            github_pr_url="",
            status="creating",
        )
        session.add(record)
        session.commit()
        task_id = task.id
        approval_id = approval.id

    with build_client(database_path) as client:
        response = client.post(f"/patch-approvals/{approval_id}/pull-requests")
        events = client.get(f"/tasks/{task_id}/events").json()

    assert response.status_code == 409
    assert "already being created" in response.json()["detail"]
    assert count_events(events, "pull_request_created") == 0


def test_adapter_failure_marks_reserved_record_failed_and_redacts_token(
    tmp_path: Path,
) -> None:
    raw_token = "ghp_example1234567890"

    class RaisingAdapter:
        def create_pull_request(self, **_kwargs):
            raise RuntimeError(f"GitHub rejected token {raw_token}")

    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _repository, _task, _cloud_run, _artifact, approval = (
            create_approved_cloud_patch(session)
        )
        approval_id = approval.id

        with pytest.raises(HTTPException) as exc_info:
            create_pull_request_for_approval(
                session,
                approval_id,
                adapter=RaisingAdapter(),
            )

        response_error = exc_info.value
        records = session.exec(select(PullRequestRecord)).all()

    assert getattr(response_error, "status_code") == 502
    assert raw_token not in str(response_error.detail)
    assert records[0].patch_approval_id == approval_id
    assert records[0].status == "failed"


def test_finalizing_pull_request_can_recover_without_calling_adapter_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_company_api.services import github_pull_request

    class RecordingAdapter:
        def __init__(self) -> None:
            self.calls = 0

        def create_pull_request(self, **_kwargs) -> CreatedPullRequest:
            self.calls += 1
            return CreatedPullRequest(
                number=42,
                url="https://github.com/example/demo/pull/42",
            )

    database_path = tmp_path / "app.db"
    adapter = RecordingAdapter()
    with build_database_session(database_path) as session:
        _project, _repository, task, _cloud_run, _artifact, approval = (
            create_approved_cloud_patch(session)
        )
        task_id = task.id
        approval_id = approval.id

    original_transition = github_pull_request._transition_task_to_pr_created

    def fail_transition_once(session: Session, task: Task) -> None:
        monkeypatch.setattr(
            github_pull_request,
            "_transition_task_to_pr_created",
            original_transition,
        )
        raise RuntimeError("simulated finalization failure")

    monkeypatch.setattr(
        github_pull_request,
        "_transition_task_to_pr_created",
        fail_transition_once,
    )

    with build_database_session(database_path) as session:
        with pytest.raises(RuntimeError, match="simulated finalization failure"):
            create_pull_request_for_approval(
                session,
                approval_id,
                adapter=adapter,
            )

    with build_database_session(database_path) as session:
        records = session.exec(select(PullRequestRecord)).all()
        persisted_task = session.get(Task, task_id)

    assert len(records) == 1
    assert records[0].status == "finalizing"
    assert records[0].github_pr_number == 42
    assert records[0].github_pr_url == "https://github.com/example/demo/pull/42"
    assert persisted_task is not None
    assert persisted_task.status == TaskStatus.HUMAN_APPROVAL
    assert adapter.calls == 1

    with build_database_session(database_path) as session:
        result, status_code = create_pull_request_for_approval(
            session,
            approval_id,
            adapter=adapter,
        )

    with build_client(database_path) as client:
        events = client.get(f"/tasks/{task_id}/events").json()

    assert status_code == 200
    assert result.task.status == TaskStatus.PR_CREATED
    assert result.pull_request.github_pr_number == 42
    assert result.pull_request.github_pr_url == "https://github.com/example/demo/pull/42"
    assert result.pull_request.status == "created"
    assert adapter.calls == 1
    assert count_events(events, "pull_request_created") == 1


def test_list_pull_requests_for_patch_artifact_returns_created_pr(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _repository, _task, _cloud_run, artifact, approval = (
            create_approved_cloud_patch(session)
        )
        artifact_id = artifact.id
        approval_id = approval.id

    with build_client(database_path) as client:
        create_response = client.post(f"/patch-approvals/{approval_id}/pull-requests")
        list_response = client.get(f"/patch-artifacts/{artifact_id}/pull-requests")

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [
        create_response.json()["pull_request"]["id"]
    ]


class tempfile_directory:
    def __init__(self, base_path: Path, prefix: str) -> None:
        self.path = base_path / prefix.rstrip("-")

    def __enter__(self) -> str:
        self.path.mkdir(parents=True)
        return str(self.path)

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None
