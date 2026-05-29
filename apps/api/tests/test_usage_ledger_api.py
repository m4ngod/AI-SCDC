from fastapi.testclient import TestClient

from ai_company_api.main import create_app


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def create_project_task_and_planner_run(client: TestClient) -> tuple[dict, dict, dict]:
    project = client.post("/projects", json={"name": "Demo Project"}).json()
    task = client.post(
        f"/projects/{project['id']}/tasks",
        json={"title": "Backend task", "role_required": "backend"},
    ).json()
    planner_run = client.post(
        f"/projects/{project['id']}/planner-runs",
        json={"goal": "Build model route settings"},
    ).json()
    return project, task, planner_run


def test_append_usage_ledger_entry_computes_total_tokens() -> None:
    with build_client() as client:
        project, task, planner_run = create_project_task_and_planner_run(client)

        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "planner_run_id": planner_run["id"],
                "usage_type": "model_tokens",
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "unit_price_cents": 0,
                "amount_cents": 0,
                "raw_usage_json": {"source": "manual_phase_2_test"},
            },
        )

    assert response.status_code == 201
    usage = response.json()
    assert usage["workspace_id"] == "dev_workspace"
    assert usage["organization_id"] == "dev_organization"
    assert usage["user_id"] == "dev_user"
    assert usage["project_id"] == project["id"]
    assert usage["task_id"] == task["id"]
    assert usage["planner_run_id"] == planner_run["id"]
    assert usage["total_tokens"] == 1500
    assert usage["raw_usage_json"] == {"source": "manual_phase_2_test"}


def test_list_usage_ledger_filters_by_project_planner_run_and_task() -> None:
    with build_client() as client:
        project, task, planner_run = create_project_task_and_planner_run(client)
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        other_task = client.post(
            f"/projects/{other_project['id']}/tasks",
            json={"title": "Other task", "role_required": "backend"},
        ).json()
        first = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "planner_run_id": planner_run["id"],
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        ).json()
        client.post(
            "/usage-ledger",
            json={
                "project_id": other_project["id"],
                "task_id": other_task["id"],
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 1,
                "completion_tokens": 1,
            },
        )

        by_project = client.get("/usage-ledger", params={"project_id": project["id"]})
        by_task = client.get("/usage-ledger", params={"task_id": task["id"]})
        by_planner = client.get(
            "/usage-ledger",
            params={"planner_run_id": planner_run["id"]},
        )

    assert [item["id"] for item in by_project.json()] == [first["id"]]
    assert [item["id"] for item in by_task.json()] == [first["id"]]
    assert [item["id"] for item in by_planner.json()] == [first["id"]]


def test_list_usage_ledger_rejects_missing_project_filter() -> None:
    with build_client() as client:
        response = client.get(
            "/usage-ledger",
            params={"project_id": "project_missing"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_list_usage_ledger_rejects_missing_task_filter() -> None:
    with build_client() as client:
        response = client.get(
            "/usage-ledger",
            params={"task_id": "task_missing"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


def test_list_usage_ledger_rejects_missing_planner_run_filter() -> None:
    with build_client() as client:
        response = client.get(
            "/usage-ledger",
            params={"planner_run_id": "planner_run_missing"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Planner run not found"


def test_list_usage_ledger_rejects_cross_project_task_filter() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        other_task = client.post(
            f"/projects/{other_project['id']}/tasks",
            json={"title": "Other task", "role_required": "backend"},
        ).json()

        response = client.get(
            "/usage-ledger",
            params={"project_id": project["id"], "task_id": other_task["id"]},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Task does not belong to project"


def test_list_usage_ledger_rejects_cross_project_planner_run_filter() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        other_planner_run = client.post(
            f"/projects/{other_project['id']}/planner-runs",
            json={"goal": "Build model route settings"},
        ).json()

        response = client.get(
            "/usage-ledger",
            params={
                "project_id": project["id"],
                "planner_run_id": other_planner_run["id"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Planner run does not belong to project"


def test_usage_ledger_rejects_cross_project_task_reference() -> None:
    with build_client() as client:
        project = client.post("/projects", json={"name": "Demo Project"}).json()
        other_project = client.post("/projects", json={"name": "Other Project"}).json()
        other_task = client.post(
            f"/projects/{other_project['id']}/tasks",
            json={"title": "Other task", "role_required": "backend"},
        ).json()

        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": other_task["id"],
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Task does not belong to project"


def test_usage_ledger_rejects_negative_token_counts() -> None:
    with build_client() as client:
        response = client.post(
            "/usage-ledger",
            json={
                "provider_name": "deepseek-dev",
                "model_name": "deepseek-chat",
                "prompt_tokens": -1,
                "completion_tokens": 5,
            },
        )

    assert response.status_code == 422


def test_usage_ledger_has_no_update_or_delete_openapi_paths() -> None:
    with build_client() as client:
        schema = client.get("/openapi.json").json()

    assert "/usage-ledger/{usage_id}" not in schema["paths"]
    assert set(schema["paths"]["/usage-ledger"].keys()) == {"get", "post"}
