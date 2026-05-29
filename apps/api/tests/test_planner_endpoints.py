from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


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
