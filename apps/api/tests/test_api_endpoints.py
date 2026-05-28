import json
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def test_health_returns_ok() -> None:
    with build_client() as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_returns_dev_identity() -> None:
    with build_client() as client:
        response = client.get("/me")

    assert response.status_code == 200
    assert response.json()["user_id"] == "dev_user"
    assert response.json()["workspace_id"] == "dev_workspace"
    assert response.json()["organization_id"] == "dev_organization"


def test_dev_cors_preflight_allows_vite_desktop_origin() -> None:
    with build_client() as client:
        response = client.options(
            "/projects",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_project_conversation_message_task_flow_records_created_event() -> None:
    with build_client() as client:
        project_response = client.post(
            "/projects",
            json={"name": "Demo Project", "description": "A demo workspace project."},
        )
        assert project_response.status_code == 201
        project = project_response.json()
        assert project["description"] == "A demo workspace project."
        assert project["created_by"] == "dev_user"

        conversation_response = client.post(
            f"/projects/{project['id']}/conversations",
            json={"title": "Planning", "conversation_type": "implementation"},
        )
        assert conversation_response.status_code == 201
        conversation = conversation_response.json()
        assert conversation["user_id"] == "dev_user"
        assert conversation["conversation_type"] == "implementation"

        message_response = client.post(
            f"/conversations/{conversation['id']}/messages",
            json={
                "sender_type": "user",
                "content": "Please build the demo.",
                "structured_payload": {"intent": "build"},
            },
        )
        assert message_response.status_code == 201
        message = message_response.json()
        assert message["sender_type"] == "user"
        assert message["sender_id"] == "dev_user"
        assert message["structured_payload"] == {"intent": "build"}

        parent_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Parent task", "role_required": "planner"},
        )
        assert parent_response.status_code == 201
        parent_task = parent_response.json()

        task_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Build demo",
                "description": "Create a demo task.",
                "conversation_id": conversation["id"],
                "parent_task_id": parent_task["id"],
                "role_required": "frontend",
                "priority": 7,
                "risk_level": "high",
                "acceptance_criteria": ["Endpoint flow works"],
                "assigned_agent_profile_id": "agent_profile_backend",
                "repo_id": "repo_api",
                "branch_name": "codex/task-4",
                "worktree_ref": "worktree_task_4",
                "budget_limit": 120,
            },
        )
        assert task_response.status_code == 201
        task = task_response.json()
        assert task["status"] == "CREATED"
        assert task["conversation_id"] == conversation["id"]
        assert task["parent_task_id"] == parent_task["id"]
        assert task["role_required"] == "frontend"
        assert task["priority"] == 7
        assert task["risk_level"] == "high"
        assert task["acceptance_criteria"] == ["Endpoint flow works"]
        assert task["assigned_agent_profile_id"] == "agent_profile_backend"
        assert task["repo_id"] == "repo_api"
        assert task["branch_name"] == "codex/task-4"
        assert task["worktree_ref"] == "worktree_task_4"
        assert task["budget_limit"] == 120

        events_response = client.get(f"/tasks/{task['id']}/events")
        assert events_response.status_code == 200
        assert events_response.json()[0]["event_type"] == "task_created"


def test_run_task_transitions_created_task_to_assigned() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Build demo", "role_required": "backend"},
        ).json()

        response = client.post(f"/tasks/{task['id']}/run")

        assert response.status_code == 200
        assert response.json()["status"] == "ASSIGNED"
        events = client.get(f"/tasks/{task['id']}/events").json()
        assert [event["event_type"] for event in events] == [
            "task_created",
            "task_transitioned",
        ]


def test_invalid_patch_transition_returns_current_requested_and_allowed_statuses() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Build demo", "role_required": "backend"},
        ).json()

        response = client.patch(f"/tasks/{task['id']}", json={"status": "MERGED"})

        assert response.status_code == 400
        assert response.json()["detail"]["current_status"] == "CREATED"
        assert response.json()["detail"]["requested_status"] == "MERGED"
        assert response.json()["detail"]["allowed_next_statuses"] == [
            "ASSIGNED",
            "CANCELLED",
            "SPEC_DRAFTED",
        ]


def test_task_create_with_nonexistent_conversation_id_returns_404() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()

        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Build demo",
                "role_required": "backend",
                "conversation_id": "conversation_missing",
            },
        )

        assert response.status_code == 404


def test_task_create_with_cross_project_conversation_id_returns_400() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        conversation = client.post(
            f"/projects/{other_project['id']}/conversations",
            json={"title": "Other planning"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Build demo",
                "role_required": "backend",
                "conversation_id": conversation["id"],
            },
        )

        assert response.status_code == 400


def test_task_create_with_nonexistent_parent_task_id_returns_404() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()

        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Build demo",
                "role_required": "backend",
                "parent_task_id": "task_missing",
            },
        )

        assert response.status_code == 404


def test_task_create_with_cross_project_parent_task_id_returns_400() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        parent_task = client.post(
            f"/projects/{other_project['id']}/tasks",
            json={"title": "Other task", "role_required": "backend"},
        ).json()

        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Build demo",
                "role_required": "backend",
                "parent_task_id": parent_task["id"],
            },
        )

        assert response.status_code == 400


def test_task_create_rejects_values_outside_agent_protocol_enums() -> None:
    role_schema = json.loads(
        Path("packages/agent-protocol/schemas/agent-role.schema.json").read_text()
    )
    task_spec_schema = json.loads(
        Path("packages/agent-protocol/schemas/task-spec.schema.json").read_text()
    )
    assert "engineer" not in role_schema["enum"]
    assert "critical" not in task_spec_schema["properties"]["risk_level"]["enum"]

    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()

        invalid_role_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Build demo", "role_required": "engineer"},
        )
        invalid_risk_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={
                "title": "Build demo",
                "role_required": "backend",
                "risk_level": "critical",
            },
        )

    assert invalid_role_response.status_code == 422
    assert invalid_risk_response.status_code == 422


def test_importing_main_does_not_create_default_dev_db(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    import ai_company_api.main as main_module

    importlib.reload(main_module)

    assert not (tmp_path / "dev.db").exists()
