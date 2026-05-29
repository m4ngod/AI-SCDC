import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlmodel import Session
from fastapi.testclient import TestClient

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    Approval,
    ApprovalStatus,
    PlannerRun,
    Project,
)


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def build_session() -> Session:
    engine = build_engine("sqlite://")
    init_db(engine)
    return Session(engine)


def test_approval_status_persists_lowercase_enum_value() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        planner_run = PlannerRun(project_id=project.id, goal="Build model route settings")
        approval = Approval(
            project_id=project.id,
            planner_run_id=planner_run.id,
            status=ApprovalStatus.APPROVED,
        )
        session.add(project)
        session.add(planner_run)
        session.add(approval)
        session.commit()

        raw_status = session.connection().execute(
            text("select status from approval where id = :id"),
            {"id": approval.id},
        ).scalar_one()

    assert raw_status == "approved"


def test_approval_planner_run_id_is_unique() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        planner_run = PlannerRun(project_id=project.id, goal="Build model route settings")
        first = Approval(
            project_id=project.id,
            planner_run_id=planner_run.id,
            status=ApprovalStatus.APPROVED,
        )
        second = Approval(
            project_id=project.id,
            planner_run_id=planner_run.id,
            status=ApprovalStatus.REJECTED,
        )
        session.add(project)
        session.add(planner_run)
        session.add(first)
        session.add(second)

        with pytest.raises(IntegrityError):
            session.commit()


def test_planner_run_status_rejects_invalid_raw_string() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        planner_run = PlannerRun(
            project_id=project.id,
            goal="Build model route settings",
            status="INVALID_STATUS",
        )
        session.add(project)
        session.add(planner_run)

        with pytest.raises((IntegrityError, StatementError)):
            session.commit()


def test_approval_status_rejects_invalid_raw_string() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        planner_run = PlannerRun(project_id=project.id, goal="Build model route settings")
        approval = Approval(
            project_id=project.id,
            planner_run_id=planner_run.id,
            status="invalid_status",
        )
        session.add(project)
        session.add(planner_run)
        session.add(approval)

        with pytest.raises((IntegrityError, StatementError)):
            session.commit()


def test_create_planner_run_creates_ordered_drafts_and_no_tasks() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()

        response = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        )

        assert response.status_code == 201
        planner_run = response.json()
        assert planner_run["project_id"] == project["id"]
        assert planner_run["goal"] == "Build model route settings"
        assert planner_run["status"] == "DRAFTED"
        assert planner_run["planner_kind"] == "fake"
        assert planner_run["draft_count"] == 2
        assert [draft["sequence"] for draft in planner_run["drafts"]] == [1, 2]
        assert [draft["role_required"] for draft in planner_run["drafts"]] == [
            "frontend",
            "backend",
        ]

        tasks_response = client.get(f"/projects/{project['id']}/tasks")
        assert tasks_response.status_code == 200
        assert tasks_response.json() == []


def test_create_planner_run_rejects_cross_project_conversation() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        conversation = client.post(
            f"/projects/{other_project['id']}/conversations",
            json={"title": "Other planning"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={
                "goal": "Build model route settings",
                "conversation_id": conversation["id"],
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Conversation does not belong to project"


def test_get_planner_run_returns_ordered_drafts() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        created = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.get(f"/planner-runs/{created['id']}")

        assert response.status_code == 200
        planner_run = response.json()
        assert planner_run["id"] == created["id"]
        assert [draft["sequence"] for draft in planner_run["drafts"]] == [1, 2]


def test_create_planner_run_with_missing_project_returns_404() -> None:
    with build_client() as client:
        response = client.post(
            "/projects/project_missing/planner-runs",
            json={"goal": "Build model route settings"},
        )

        assert response.status_code == 404
