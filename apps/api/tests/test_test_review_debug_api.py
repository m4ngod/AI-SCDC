import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

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
from ai_company_api.services.task_state import TaskStatus
from ai_company_worker.test_runner import CommandResult, TestRunnerResult


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def build_client(database_path: Path) -> TestClient:
    return TestClient(create_app(database_url=f"sqlite:///{database_path.as_posix()}"))


def database_url(database_path: Path) -> str:
    return f"sqlite:///{database_path.as_posix()}"


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


def count_events(events: list[dict], event_type: str) -> int:
    return sum(1 for event in events if event["event_type"] == event_type)


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


def test_patch_review_is_unique_per_artifact_and_reviewer_kind() -> None:
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

        first_review = PatchReview(
            project_id=project.id,
            task_id=task.id,
            local_run_id=local_run.id,
            patch_artifact_id=patch_artifact.id,
            verdict="approved",
            issues=[],
            required_changes=[],
        )
        session.add(first_review)
        session.commit()

        duplicate_review = PatchReview(
            project_id=project.id,
            task_id=task.id,
            local_run_id=local_run.id,
            patch_artifact_id=patch_artifact.id,
            verdict="approved",
            issues=[],
            required_changes=[],
        )
        session.add(duplicate_review)
        with pytest.raises(IntegrityError):
            session.commit()


def test_init_db_adds_patch_review_unique_index_to_existing_sqlite_db(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "old-patch-review.db"
    engine = build_engine(database_url(database_path))
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table patch_review (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null,
                    task_id varchar not null,
                    local_run_id varchar not null,
                    patch_artifact_id varchar not null,
                    test_run_id varchar,
                    reviewer_kind varchar not null,
                    verdict varchar not null,
                    issues json not null,
                    required_changes json not null,
                    created_at datetime not null
                )
                """
            )
        )

    init_db(engine)

    with engine.begin() as connection:
        indexes = {
            row["name"]: row
            for row in connection.execute(
                text("PRAGMA index_list(patch_review)")
            ).mappings()
        }
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
                    reviewer_kind,
                    verdict,
                    issues,
                    required_changes,
                    created_at
                )
                values (
                    'review_one',
                    'dev_workspace',
                    'project_one',
                    'task_one',
                    'local_run_one',
                    'patch_one',
                    'deterministic',
                    'approved',
                    '[]',
                    '[]',
                    '2026-05-31 00:00:00'
                )
                """
            )
        )
        with pytest.raises(IntegrityError):
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
                        reviewer_kind,
                        verdict,
                        issues,
                        required_changes,
                        created_at
                    )
                    values (
                        'review_two',
                        'dev_workspace',
                        'project_one',
                        'task_one',
                        'local_run_one',
                        'patch_one',
                        'deterministic',
                        'approved',
                        '[]',
                        '[]',
                        '2026-05-31 00:00:01'
                    )
                    """
                )
            )

    index = indexes["uq_patch_review_artifact_reviewer_kind"]
    assert index["unique"] == 1


def test_init_db_reclassifies_duplicate_legacy_patch_reviews_before_unique_index(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "duplicate-patch-review.db"
    engine = build_engine(database_url(database_path))
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table patch_review (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    project_id varchar not null,
                    task_id varchar not null,
                    local_run_id varchar not null,
                    patch_artifact_id varchar not null,
                    test_run_id varchar,
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
                insert into patch_review (
                    id,
                    workspace_id,
                    project_id,
                    task_id,
                    local_run_id,
                    patch_artifact_id,
                    reviewer_kind,
                    verdict,
                    issues,
                    required_changes,
                    created_at
                )
                values (
                    :id,
                    'dev_workspace',
                    'project_one',
                    'task_one',
                    'local_run_one',
                    'patch_one',
                    'deterministic',
                    'approved',
                    '[]',
                    '[]',
                    :created_at
                )
                """
            ),
            [
                {
                    "id": "review_earliest",
                    "created_at": "2026-05-31 00:00:00",
                },
                {
                    "id": "review_duplicate",
                    "created_at": "2026-05-31 00:00:01",
                },
            ],
        )

    init_db(engine)
    init_db(engine)

    with engine.begin() as connection:
        rows = list(
            connection.execute(
                text(
                    """
                    select id, reviewer_kind
                    from patch_review
                    order by created_at, id
                    """
                )
            ).mappings()
        )
        indexes = {
            row["name"]: row
            for row in connection.execute(
                text("PRAGMA index_list(patch_review)")
            ).mappings()
        }
        with pytest.raises(IntegrityError):
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
                        reviewer_kind,
                        verdict,
                        issues,
                        required_changes,
                        created_at
                    )
                    values (
                        'review_new_duplicate',
                        'dev_workspace',
                        'project_one',
                        'task_one',
                        'local_run_one',
                        'patch_one',
                        'deterministic',
                        'approved',
                        '[]',
                        '[]',
                        '2026-05-31 00:00:02'
                    )
                    """
                )
            )

    assert rows == [
        {"id": "review_earliest", "reviewer_kind": "deterministic"},
        {
            "id": "review_duplicate",
            "reviewer_kind": "legacy_duplicate:deterministic:review_duplicate",
        },
    ]
    assert indexes["uq_patch_review_artifact_reviewer_kind"]["unique"] == 1


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


def test_review_approves_patch_after_passing_tests(tmp_path: Path) -> None:
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

        test_response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")
        review_response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")
        assert test_response.status_code == 201
        assert test_response.json()["task"]["status"] == "REVIEWING"
        assert review_response.status_code == 201
        result = review_response.json()
        review = result["review"]
        assert result["review"]["verdict"] == "approved"
        assert result["review"]["test_run_id"] == test_response.json()["test_run"]["id"]
        assert result["task"]["status"] == "APPROVED"
        assert result["debug_attempt"] is None

        list_response = client.get(f"/patch-artifacts/{artifact['id']}/reviews")
        get_response = client.get(f"/patch-reviews/{review['id']}")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [review["id"]]
    assert get_response.status_code == 200
    assert get_response.json()["id"] == review["id"]

    event_types = [event["event_type"] for event in events_response.json()]
    assert "patch_review_created" in event_types


def test_review_requests_changes_when_diff_is_missing(tmp_path: Path) -> None:
    repo_path = create_git_repo(tmp_path)
    database_path = tmp_path / "api.db"
    url = database_url(database_path)

    with build_client(database_path) as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            [
                "python -c \"from pathlib import Path; "
                "assert Path('README.md').exists()\""
            ],
        )
        test_response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")

        engine = build_engine(url)
        with Session(engine) as session:
            persisted_artifact = session.get(PatchArtifact, artifact["id"])
            assert persisted_artifact is not None
            persisted_artifact.diff_text = ""
            session.add(persisted_artifact)
            session.commit()

        review_response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert test_response.status_code == 201
    assert test_response.json()["task"]["status"] == "REVIEWING"
    assert review_response.status_code == 201
    result = review_response.json()
    assert result["review"]["verdict"] == "changes_requested"
    assert result["task"]["status"] == "FIX_REQUESTED"
    assert result["debug_attempt"]["status"] == "requested"
    assert result["debug_attempt"]["review_id"] == result["review"]["id"]
    assert "deterministic review" in result["debug_attempt"]["root_cause"]

    event_types = [event["event_type"] for event in events_response.json()]
    assert "patch_review_created" in event_types
    assert "debug_attempt_created" in event_types


def test_duplicate_review_post_returns_existing_review_without_side_effects(
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)
    database_path = tmp_path / "api.db"
    url = database_url(database_path)

    with build_client(database_path) as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            [
                "python -c \"from pathlib import Path; "
                "assert Path('README.md').exists()\""
            ],
        )
        test_response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")

        engine = build_engine(url)
        with Session(engine) as session:
            persisted_artifact = session.get(PatchArtifact, artifact["id"])
            assert persisted_artifact is not None
            persisted_artifact.diff_text = ""
            session.add(persisted_artifact)
            session.commit()

        first_response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")
        duplicate_response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")
        list_response = client.get(f"/patch-artifacts/{artifact['id']}/reviews")
        debug_response = client.get(f"/tasks/{task['id']}/debug-attempts")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert test_response.status_code == 201
    assert first_response.status_code == 201
    assert duplicate_response.status_code == 201
    first_result = first_response.json()
    duplicate_result = duplicate_response.json()
    assert duplicate_result["review"]["id"] == first_result["review"]["id"]
    assert (
        duplicate_result["debug_attempt"]["id"]
        == first_result["debug_attempt"]["id"]
    )
    assert duplicate_result["task"]["status"] == "FIX_REQUESTED"

    assert list_response.status_code == 200
    assert [review["id"] for review in list_response.json()] == [
        first_result["review"]["id"]
    ]
    assert debug_response.status_code == 200
    assert [attempt["id"] for attempt in debug_response.json()] == [
        first_result["debug_attempt"]["id"]
    ]

    events = events_response.json()
    assert count_events(events, "patch_review_created") == 1
    assert count_events(events, "debug_attempt_created") == 1


def test_review_without_existing_review_requires_reviewing_task(
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)
    with build_client(tmp_path / "api.db") as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -V"],
        )

        response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")
        list_response = client.get(f"/patch-artifacts/{artifact['id']}/reviews")
        events_response = client.get(f"/tasks/{task['id']}/events")

    assert response.status_code == 400
    assert response.json()["detail"]["current_status"] == "PATCH_READY"
    assert list_response.status_code == 200
    assert list_response.json() == []

    events = events_response.json()
    assert count_events(events, "patch_review_created") == 0
    assert count_events(events, "debug_attempt_created") == 0


@pytest.mark.parametrize(
    ("scenario", "expected_issue_code"),
    [
        ("no_changed_files", "no_changed_files"),
        ("out_of_scope_changed_files", "changed_file_outside_allowed_paths"),
        ("missing_test_run", "missing_passing_test_run"),
        ("latest_non_passed_test_run", "latest_test_run_not_passed"),
    ],
)
def test_review_requests_changes_for_deterministic_issue(
    scenario: str,
    expected_issue_code: str,
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)
    database_path = tmp_path / "api.db"
    url = database_url(database_path)
    newest_test_run_id: str | None = None

    with build_client(database_path) as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            [
                "python -c \"from pathlib import Path; "
                "assert Path('README.md').exists()\""
            ],
        )

        if scenario in {"no_changed_files", "out_of_scope_changed_files"}:
            test_response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")
            assert test_response.status_code == 201

        engine = build_engine(url)
        with Session(engine) as session:
            persisted_task = session.get(Task, task["id"])
            persisted_artifact = session.get(PatchArtifact, artifact["id"])
            assert persisted_task is not None
            assert persisted_artifact is not None

            if scenario == "no_changed_files":
                persisted_artifact.files_changed = []
                session.add(persisted_artifact)
            elif scenario == "out_of_scope_changed_files":
                persisted_artifact.files_changed = ["docs/outside.md"]
                session.add(persisted_artifact)
            elif scenario == "missing_test_run":
                persisted_task.status = TaskStatus.REVIEWING
                session.add(persisted_task)
            elif scenario == "latest_non_passed_test_run":
                persisted_task.status = TaskStatus.REVIEWING
                base_created_at = persisted_task.updated_at
                older_passed_test_run = LocalTestRun(
                    project_id=persisted_task.project_id,
                    task_id=persisted_task.id,
                    local_run_id=persisted_artifact.local_run_id,
                    patch_artifact_id=persisted_artifact.id,
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
                    completed_at=base_created_at - timedelta(seconds=1),
                    created_at=base_created_at - timedelta(seconds=1),
                )
                newer_failed_test_run = LocalTestRun(
                    project_id=persisted_task.project_id,
                    task_id=persisted_task.id,
                    local_run_id=persisted_artifact.local_run_id,
                    patch_artifact_id=persisted_artifact.id,
                    status="failed",
                    commands=["python -V"],
                    command_results=[
                        {
                            "command": "python -V",
                            "exit_code": 1,
                            "stdout": "",
                            "stderr": "failed",
                            "duration_ms": 1,
                        }
                    ],
                    failure_reason="failed",
                    completed_at=base_created_at + timedelta(seconds=1),
                    created_at=base_created_at + timedelta(seconds=1),
                )
                session.add(persisted_task)
                session.add(older_passed_test_run)
                session.add(newer_failed_test_run)
                session.flush()
                newest_test_run_id = newer_failed_test_run.id
            session.commit()

        review_response = client.post(f"/patch-artifacts/{artifact['id']}/reviews")

    assert review_response.status_code == 201
    result = review_response.json()
    assert result["review"]["verdict"] == "changes_requested"
    assert result["task"]["status"] == "FIX_REQUESTED"
    assert result["debug_attempt"]["status"] == "requested"
    assert result["debug_attempt"]["review_id"] == result["review"]["id"]
    assert expected_issue_code in [
        issue["code"] for issue in result["review"]["issues"]
    ]
    if scenario == "latest_non_passed_test_run":
        assert result["review"]["test_run_id"] == newest_test_run_id


def test_test_run_start_is_committed_before_commands_execute(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_path = create_git_repo(tmp_path)
    database_path = tmp_path / "api.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    runner_calls = 0

    def fake_run_tests(_request):
        nonlocal runner_calls
        runner_calls += 1
        if runner_calls > 1:
            raise AssertionError("Duplicate start should not invoke RUN_TESTS")

        engine = build_engine(database_url)
        with Session(engine) as second_session:
            persisted_task = second_session.get(Task, task["id"])
            persisted_test_run = second_session.exec(
                select(LocalTestRun).where(
                    LocalTestRun.patch_artifact_id == artifact["id"]
                )
            ).one()

        assert persisted_task is not None
        assert persisted_task.status == "SELF_TESTING"
        assert persisted_test_run.status == "running"

        with build_client(database_path) as second_client:
            duplicate_response = second_client.post(
                f"/patch-artifacts/{artifact['id']}/test-runs"
            )

        assert duplicate_response.status_code == 400
        assert duplicate_response.json()["detail"]["current_status"] == "SELF_TESTING"
        return TestRunnerResult(
            status="passed",
            command_results=[
                CommandResult(
                    command="python -V",
                    exit_code=0,
                    stdout="Python",
                    stderr="",
                    duration_ms=1,
                )
            ],
        )

    monkeypatch.setattr(
        "ai_company_api.services.test_review_debug.RUN_TESTS",
        fake_run_tests,
    )

    with build_client(database_path) as client:
        _project, task, _local_run, artifact = create_patch_ready_task(
            client,
            repo_path,
            ["python -V"],
        )

        response = client.post(f"/patch-artifacts/{artifact['id']}/test-runs")

    assert response.status_code == 201
    assert response.json()["task"]["status"] == "REVIEWING"
    assert runner_calls == 1


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
