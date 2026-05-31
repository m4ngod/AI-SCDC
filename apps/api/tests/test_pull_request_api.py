from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

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
    Repository,
    Task,
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
