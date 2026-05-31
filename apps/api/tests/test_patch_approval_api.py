from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    LocalTaskRun,
    LocalTestRun,
    PatchApproval,
    PatchArtifact,
    PatchReview,
    Project,
    Repository,
    Task,
)
from ai_company_api.services.task_state import TaskStatus


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def build_database_session(database_path: Path) -> Session:
    engine = build_engine(f"sqlite:///{database_path.as_posix()}")
    init_db(engine)
    return Session(engine)


def count_events(events: list[dict], event_type: str) -> int:
    return sum(1 for event in events if event["event_type"] == event_type)


def create_reviewed_patch(
    session: Session,
    *,
    task_status: TaskStatus = TaskStatus.APPROVED,
    review_verdict: str = "approved",
) -> tuple[Project, Task, LocalTaskRun, PatchArtifact, PatchReview]:
    project = Project(name="Demo")
    session.add(project)
    session.flush()
    repository = Repository(
        project_id=project.id,
        name="Demo repo",
        local_path=".",
        default_branch="main",
    )
    session.add(repository)
    session.flush()
    task = Task(
        project_id=project.id,
        title="Approve reviewed patch",
        role_required="backend",
        status=task_status,
        allowed_paths=["README.md"],
        required_tests=["python -V"],
        repo_id=repository.id,
        branch_name="main",
        worktree_ref=".worktrees/task-local_run",
    )
    session.add(task)
    session.flush()
    local_run = LocalTaskRun(
        project_id=project.id,
        task_id=task.id,
        repo_id=repository.id,
        status="patch_ready",
        base_branch="main",
        worktree_path=".worktrees/task-local_run",
    )
    session.add(local_run)
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
        verdict=review_verdict,
        issues=[] if review_verdict == "approved" else [{"code": "needs_changes"}],
        required_changes=[] if review_verdict == "approved" else ["Fix review issue."],
    )
    session.add(review)
    session.commit()
    session.refresh(project)
    session.refresh(task)
    session.refresh(local_run)
    session.refresh(artifact)
    session.refresh(review)
    return project, task, local_run, artifact, review


def test_patch_approval_record_persists() -> None:
    with build_session() as session:
        _project, task, local_run, artifact, review = create_reviewed_patch(session)
        artifact_id = artifact.id
        review_id = review.id
        approval = PatchApproval(
            project_id=task.project_id,
            task_id=task.id,
            local_run_id=local_run.id,
            patch_artifact_id=artifact.id,
            review_id=review.id,
            status="approved",
            approved_by="dev_user",
            merge_instructions="Inspect the worktree before merging.",
        )
        session.add(approval)
        session.commit()

        persisted = session.get(PatchApproval, approval.id)

    assert persisted is not None
    assert persisted.patch_artifact_id == artifact_id
    assert persisted.review_id == review_id
    assert persisted.status == "approved"
    assert persisted.merge_instructions == "Inspect the worktree before merging."


def test_approved_reviewed_patch_can_be_approved(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, task, _local_run, artifact, review = create_reviewed_patch(session)
        task_id = task.id
        artifact_id = artifact.id
        review_id = review.id

    with build_client(database_path) as client:
        response = client.post(f"/patch-artifacts/{artifact_id}/approvals")

        assert response.status_code == 201
        result = response.json()
        assert result["task"]["status"] == "MERGE_READY"
        assert result["approval"]["patch_artifact_id"] == artifact_id
        assert result["approval"]["review_id"] == review_id
        assert result["approval"]["status"] == "approved"
        assert "does not run git merge" in result["approval"]["merge_instructions"]

        list_response = client.get(f"/patch-artifacts/{artifact_id}/approvals")
        get_response = client.get(f"/patch-approvals/{result['approval']['id']}")
        events = client.get(f"/tasks/{task_id}/events").json()

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [
        result["approval"]["id"]
    ]
    assert get_response.status_code == 200
    assert get_response.json()["id"] == result["approval"]["id"]
    assert count_events(events, "patch_approval_created") == 1
    assert count_events(events, "task_transitioned") == 1


def test_patch_approval_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, task, _local_run, artifact, _review = create_reviewed_patch(session)
        task_id = task.id
        artifact_id = artifact.id

    with build_client(database_path) as client:
        first = client.post(f"/patch-artifacts/{artifact_id}/approvals")
        second = client.post(f"/patch-artifacts/{artifact_id}/approvals")
        list_response = client.get(f"/patch-artifacts/{artifact_id}/approvals")
        events = client.get(f"/tasks/{task_id}/events").json()

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["approval"]["id"] == first.json()["approval"]["id"]
    assert [item["id"] for item in list_response.json()] == [
        first.json()["approval"]["id"]
    ]
    assert count_events(events, "patch_approval_created") == 1


def test_patch_approval_requires_approved_task(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _task, _local_run, artifact, _review = create_reviewed_patch(
            session,
            task_status=TaskStatus.REVIEWING,
        )
        artifact_id = artifact.id

    with build_client(database_path) as client:
        response = client.post(f"/patch-artifacts/{artifact_id}/approvals")

    assert response.status_code == 400
    assert response.json()["detail"]["current_status"] == "REVIEWING"
    assert response.json()["detail"]["expected_status"] == "APPROVED"


def test_patch_approval_requires_latest_approved_review(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _task, _local_run, artifact, review = create_reviewed_patch(session)
        artifact_id = artifact.id
        newer_review = PatchReview(
            project_id=review.project_id,
            task_id=review.task_id,
            local_run_id=review.local_run_id,
            patch_artifact_id=review.patch_artifact_id,
            test_run_id=review.test_run_id,
            reviewer_kind="manual",
            verdict="changes_requested",
            issues=[{"code": "needs_changes"}],
            required_changes=["Fix review issue."],
        )
        session.add(newer_review)
        session.commit()

    with build_client(database_path) as client:
        response = client.post(f"/patch-artifacts/{artifact_id}/approvals")

    assert response.status_code == 400
    assert "approved review" in response.json()["detail"]["message"]


def test_patch_approval_can_request_human_approval(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, task, _local_run, artifact, _review = create_reviewed_patch(session)
        task_id = task.id
        artifact_id = artifact.id

    with build_client(database_path) as client:
        approval_response = client.post(f"/patch-artifacts/{artifact_id}/approvals")
        approval_id = approval_response.json()["approval"]["id"]
        response = client.post(
            f"/patch-approvals/{approval_id}/request-human-approval"
        )
        events = client.get(f"/tasks/{task_id}/events").json()

    assert response.status_code == 200
    assert response.json()["task"]["status"] == "HUMAN_APPROVAL"
    assert count_events(events, "human_approval_requested") == 1
    assert count_events(events, "task_transitioned") == 2


def test_request_human_approval_requires_merge_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    with build_database_session(database_path) as session:
        _project, _task, local_run, artifact, review = create_reviewed_patch(session)
        approval = PatchApproval(
            project_id=artifact.project_id,
            task_id=artifact.task_id,
            local_run_id=local_run.id,
            patch_artifact_id=artifact.id,
            review_id=review.id,
            status="approved",
            approved_by="dev_user",
            merge_instructions="Inspect the worktree before merging.",
        )
        session.add(approval)
        session.commit()
        session.refresh(approval)
        approval_id = approval.id

    with build_client(database_path) as client:
        response = client.post(f"/patch-approvals/{approval_id}/request-human-approval")

    assert response.status_code == 400
    assert response.json()["detail"]["expected_status"] == "MERGE_READY"
