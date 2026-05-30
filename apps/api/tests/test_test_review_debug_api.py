from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
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
