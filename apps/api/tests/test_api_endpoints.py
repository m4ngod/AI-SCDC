from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def test_health_returns_ok() -> None:
    client = build_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_me_returns_dev_identity() -> None:
    client = build_client()

    response = client.get("/me")

    assert response.status_code == 200
    assert response.json()["user_id"] == "dev_user"
    assert response.json()["workspace_id"] == "dev_workspace"


def test_project_conversation_message_task_flow_records_created_event() -> None:
    client = build_client()

    project_response = client.post("/projects", json={"name": "Demo Project"})
    assert project_response.status_code == 201
    project = project_response.json()

    conversation_response = client.post(
        f"/projects/{project['id']}/conversations",
        json={"title": "Planning"},
    )
    assert conversation_response.status_code == 201
    conversation = conversation_response.json()

    message_response = client.post(
        f"/conversations/{conversation['id']}/messages",
        json={"role": "user", "content": "Please build the demo."},
    )
    assert message_response.status_code == 201

    task_response = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Build demo", "description": "Create a demo task."},
    )
    assert task_response.status_code == 201
    task = task_response.json()
    assert task["status"] == "CREATED"

    events_response = client.get(f"/tasks/{task['id']}/events")
    assert events_response.status_code == 200
    assert events_response.json()[0]["event_type"] == "task_created"


def test_run_task_transitions_created_task_to_assigned() -> None:
    client = build_client()
    project = client.post("/projects", json={"name": "Demo Project"}).json()
    task = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Build demo"},
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
    client = build_client()
    project = client.post("/projects", json={"name": "Demo Project"}).json()
    task = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Build demo"},
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
