import pytest
from fastapi import HTTPException
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
from ai_company_api.schemas.api import PlannerRunCreate
from ai_company_api.services.repository import (
    approve_planner_run,
    create_planner_run,
    reject_planner_run,
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
        assert planner_run["model_route_id"] is None
        assert planner_run["model_provider_name"] is None
        assert planner_run["model_name"] is None
        assert planner_run["fallback_reason"] is None
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


def test_approve_planner_run_creates_tasks_and_task_events() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.post(f"/planner-runs/{planner_run['id']}/approve")

        assert response.status_code == 200
        approval = response.json()
        assert approval["planner_run_id"] == planner_run["id"]
        assert approval["status"] == "APPROVED"
        assert len(approval["created_tasks"]) == 2
        assert [task["role_required"] for task in approval["created_tasks"]] == [
            "frontend",
            "backend",
        ]

        tasks = client.get(f"/projects/{project['id']}/tasks").json()
        assert len(tasks) == 2
        assert all(task["status"] == "CREATED" for task in tasks)

        for task, draft in zip(tasks, planner_run["drafts"], strict=True):
            events = client.get(f"/tasks/{task['id']}/events").json()
            assert [event["event_type"] for event in events] == ["task_created"]
            assert events[0]["payload"] == {
                "status": "CREATED",
                "planner_run_id": planner_run["id"],
                "planner_task_draft_id": draft["id"],
            }

        updated_run = client.get(f"/planner-runs/{planner_run['id']}").json()
        assert updated_run["status"] == "APPROVED"


def test_reject_planner_run_creates_no_tasks() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.post(
            f"/planner-runs/{planner_run['id']}/reject",
            json={"reason": "Too broad for this project."},
        )

        assert response.status_code == 200
        rejection = response.json()
        assert rejection["planner_run_id"] == planner_run["id"]
        assert rejection["status"] == "REJECTED"
        assert rejection["created_tasks"] == []
        assert client.get(f"/projects/{project['id']}/tasks").json() == []
        assert client.get(f"/planner-runs/{planner_run['id']}").json()["status"] == "REJECTED"


def test_reject_planner_run_keeps_approval_action_type() -> None:
    with build_session() as session:
        project = Project(name="Demo Project")
        session.add(project)
        session.commit()
        planner_run = create_planner_run(
            session,
            project.id,
            PlannerRunCreate(goal="Build model route settings"),
        )

        reject_planner_run(session, planner_run.id, reason="Too broad.")

        raw_approval = session.connection().execute(
            text(
                "select action_type, reason, status from approval "
                "where planner_run_id = :planner_run_id"
            ),
            {"planner_run_id": planner_run.id},
        ).mappings().one()

    assert raw_approval["action_type"] == "approve_planner_run"
    assert raw_approval["reason"] == "Too broad."
    assert raw_approval["status"] == "rejected"


def test_planner_run_can_only_be_decided_once() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        first = client.post(f"/planner-runs/{planner_run['id']}/approve")
        second = client.post(f"/planner-runs/{planner_run['id']}/approve")
        third = client.post(
            f"/planner-runs/{planner_run['id']}/reject",
            json={"reason": "Changed my mind."},
        )

        assert first.status_code == 200
        assert second.status_code == 400
        assert second.json()["detail"] == "Planner run has already been decided"
        assert third.status_code == 400
        assert third.json()["detail"] == "Planner run has already been decided"


def test_stale_approve_duplicate_integrity_error_returns_400() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)

    with Session(engine) as setup_session:
        project = Project(name="Demo Project")
        setup_session.add(project)
        setup_session.commit()
        planner_run = create_planner_run(
            setup_session,
            project.id,
            PlannerRunCreate(goal="Build model route settings"),
        )

    with Session(engine) as stale_session, Session(engine) as current_session:
        stale_planner_run = stale_session.get(PlannerRun, planner_run.id)
        assert stale_planner_run is not None
        assert stale_planner_run.status == "DRAFTED"
        approve_planner_run(current_session, planner_run.id)

        with pytest.raises(HTTPException) as exc_info:
            approve_planner_run(stale_session, planner_run.id)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Planner run has already been decided"


def test_planner_decision_routes_have_stable_openapi_response_schema() -> None:
    with build_client() as client:
        schema = client.get("/openapi.json").json()

    approve_schema = schema["paths"]["/planner-runs/{planner_run_id}/approve"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    reject_schema = schema["paths"]["/planner-runs/{planner_run_id}/reject"]["post"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]

    assert approve_schema == {"$ref": "#/components/schemas/PlannerRunDecisionRead"}
    assert reject_schema == {"$ref": "#/components/schemas/PlannerRunDecisionRead"}
    decision_schema = schema["components"]["schemas"]["PlannerRunDecisionRead"]
    created_tasks = decision_schema["properties"]["created_tasks"]
    assert created_tasks["items"] == {"$ref": "#/components/schemas/TaskRead"}


def test_planner_run_routes_have_stable_openapi_response_schema() -> None:
    with build_client() as client:
        schema = client.get("/openapi.json").json()

    create_schema = schema["paths"]["/projects/{project_id}/planner-runs"]["post"][
        "responses"
    ]["201"]["content"]["application/json"]["schema"]
    read_schema = schema["paths"]["/planner-runs/{planner_run_id}"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]

    assert create_schema == {"$ref": "#/components/schemas/PlannerRunRead"}
    assert read_schema == {"$ref": "#/components/schemas/PlannerRunRead"}
    run_schema = schema["components"]["schemas"]["PlannerRunRead"]
    assert "model_route_id" in run_schema["properties"]
    assert "model_provider_name" in run_schema["properties"]
    assert "model_name" in run_schema["properties"]
    assert "fallback_reason" in run_schema["properties"]
