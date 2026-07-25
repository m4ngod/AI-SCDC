from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import CloudRun, Project, Repository, Task


def build_client() -> TestClient:
    return TestClient(create_app(database_url="sqlite://"))


def auth_headers(
    *,
    user_id: str = "user_a",
    workspace_id: str = "workspace_a",
    organization_id: str = "org_a",
) -> dict[str, str]:
    return {
        "x-ai-scdc-user-id": user_id,
        "x-ai-scdc-workspace-id": workspace_id,
        "x-ai-scdc-organization-id": organization_id,
    }


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


def create_cloud_run_graph(database_url: str) -> None:
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project = Project(
            id="project_a",
            workspace_id="workspace_a",
            name="Workspace A project",
        )
        other_project = Project(
            id="project_b",
            workspace_id="workspace_a",
            name="Workspace A other project",
        )
        task = Task(
            id="task_a",
            project_id=project.id,
            title="Task A",
            role_required="backend",
        )
        other_task = Task(
            id="task_b",
            project_id=other_project.id,
            title="Task B",
            role_required="backend",
        )
        repository = Repository(
            id="repo_a",
            workspace_id="workspace_a",
            project_id=project.id,
            name="Repo A",
            local_path="",
        )
        cloud_run = CloudRun(
            id="cloud_run_a",
            workspace_id="workspace_a",
            project_id=project.id,
            task_id=task.id,
            repo_id=repository.id,
            base_branch="main",
            head_branch="ai-scdc/task-a",
        )
        session.add(project)
        session.add(other_project)
        session.add(task)
        session.add(other_task)
        session.add(repository)
        session.add(cloud_run)
        session.commit()


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


@pytest.mark.parametrize(
    ("usage_type", "quantity", "unit_name"),
    [
        ("cloud_run_runtime_seconds", 12, "seconds"),
        ("worker_submissions", 1, "submission"),
        ("object_storage_bytes", 4096, "bytes"),
        ("object_storage_reads", 3, "read"),
        ("log_sync_calls", 2, "call"),
        ("queue_messages", 4, "message"),
        ("pr_publish_attempts", 1, "attempt"),
    ],
)
def test_usage_ledger_accepts_execution_usage_dimensions(
    usage_type: str,
    quantity: int,
    unit_name: str,
) -> None:
    with build_client() as client:
        project, task, _planner_run = create_project_task_and_planner_run(client)

        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "usage_type": usage_type,
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": quantity,
                "unit_name": unit_name,
                "amount_cents": quantity,
                "raw_usage_json": {"cloud_run_id": "cloud_run_example"},
            },
        )

    assert response.status_code == 201
    usage = response.json()
    assert usage["usage_type"] == usage_type
    assert usage["quantity"] == quantity
    assert usage["unit_name"] == unit_name
    assert usage["total_tokens"] == 0


def test_usage_ledger_derives_project_and_task_from_cloud_run(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    create_cloud_run_graph(database_url)

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/usage-ledger",
            json={
                "cloud_run_id": "cloud_run_a",
                "usage_type": "object_storage_reads",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 3,
                "unit_name": "read",
                "amount_cents": 3,
            },
            headers=auth_headers(workspace_id="workspace_a", organization_id="org_a"),
        )

    assert response.status_code == 201
    usage = response.json()
    assert usage["cloud_run_id"] == "cloud_run_a"
    assert usage["project_id"] == "project_a"
    assert usage["task_id"] == "task_a"


@pytest.mark.parametrize(
    "usage_type",
    [
        "worker_submissions",
        "queue_messages",
        "cloud_run_runtime_seconds",
        "object_storage_bytes",
        "log_sync_calls",
    ],
)
def test_usage_ledger_rejects_settlement_owned_cloud_run_usage(
    tmp_path: Path,
    usage_type: str,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    create_cloud_run_graph(database_url)

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/usage-ledger",
            json={
                "cloud_run_id": "cloud_run_a",
                "usage_type": usage_type,
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "unit",
                "amount_cents": 1,
            },
            headers=auth_headers(workspace_id="workspace_a", organization_id="org_a"),
        )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Cloud-run settlement owns this usage type for cloud_run_id entries"
    )


def test_usage_ledger_rejects_cross_workspace_cloud_run_reference(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    create_cloud_run_graph(database_url)

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/usage-ledger",
            json={
                "cloud_run_id": "cloud_run_a",
                "usage_type": "worker_submissions",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "submission",
                "amount_cents": 25,
            },
            headers=auth_headers(
                user_id="user_b",
                workspace_id="workspace_b",
                organization_id="org_b",
            ),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Cloud run not found"


def test_usage_ledger_rejects_cloud_run_project_and_task_mismatch(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    create_cloud_run_graph(database_url)

    with TestClient(create_app(database_url=database_url)) as client:
        project_mismatch = client.post(
            "/usage-ledger",
            json={
                "cloud_run_id": "cloud_run_a",
                "project_id": "project_b",
                "usage_type": "worker_submissions",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "submission",
                "amount_cents": 25,
            },
            headers=auth_headers(workspace_id="workspace_a", organization_id="org_a"),
        )
        task_mismatch = client.post(
            "/usage-ledger",
            json={
                "cloud_run_id": "cloud_run_a",
                "task_id": "task_b",
                "usage_type": "worker_submissions",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 1,
                "unit_name": "submission",
                "amount_cents": 25,
            },
            headers=auth_headers(workspace_id="workspace_a", organization_id="org_a"),
        )

    assert project_mismatch.status_code == 400
    assert project_mismatch.json()["detail"] == "Cloud run does not belong to project"
    assert task_mismatch.status_code == 400
    assert task_mismatch.json()["detail"] == "Cloud run does not belong to task"


def test_init_db_upgrades_legacy_sqlite_usage_type_check_for_execution_usage(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-usage-ledger.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table usage_ledger_entry (
                    id varchar not null primary key,
                    workspace_id varchar not null,
                    organization_id varchar not null,
                    user_id varchar not null,
                    project_id varchar,
                    planner_run_id varchar,
                    task_id varchar,
                    usage_type varchar not null
                        check (usage_type in ('model_tokens')),
                    provider_name varchar not null,
                    model_name varchar not null,
                    prompt_tokens integer not null,
                    completion_tokens integer not null,
                    total_tokens integer not null,
                    unit_price_cents integer not null,
                    amount_cents integer not null,
                    raw_usage_json json not null,
                    created_at datetime not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                create index ix_usage_ledger_entry_workspace_id
                on usage_ledger_entry (workspace_id)
                """
            )
        )
        connection.execute(
            text(
                """
                insert into usage_ledger_entry (
                    id,
                    workspace_id,
                    organization_id,
                    user_id,
                    project_id,
                    planner_run_id,
                    task_id,
                    usage_type,
                    provider_name,
                    model_name,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    unit_price_cents,
                    amount_cents,
                    raw_usage_json,
                    created_at
                ) values (
                    'usage_legacy_model',
                    'dev_workspace',
                    'dev_organization',
                    'dev_user',
                    'project_legacy',
                    null,
                    'task_legacy',
                    'model_tokens',
                    'deepseek-dev',
                    'deepseek-chat',
                    11,
                    7,
                    18,
                    0,
                    0,
                    '{"source":"legacy"}',
                    CURRENT_TIMESTAMP
                )
                """
            )
        )

    init_db(engine)

    with engine.begin() as connection:
        legacy_row = connection.execute(
            text(
                """
                select
                    id,
                    project_id,
                    task_id,
                    usage_type,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cloud_run_id,
                    quantity,
                    unit_name
                from usage_ledger_entry
                where id = 'usage_legacy_model'
                """
            )
        ).mappings().one()

    assert legacy_row["project_id"] == "project_legacy"
    assert legacy_row["task_id"] == "task_legacy"
    assert legacy_row["usage_type"] == "model_tokens"
    assert legacy_row["prompt_tokens"] == 11
    assert legacy_row["completion_tokens"] == 7
    assert legacy_row["total_tokens"] == 18
    assert legacy_row["cloud_run_id"] is None
    assert legacy_row["quantity"] == 0
    assert legacy_row["unit_name"] == ""

    with TestClient(create_app(database_url=database_url)) as client:
        project, task, _planner_run = create_project_task_and_planner_run(client)
        response = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "usage_type": "cloud_run_runtime_seconds",
                "provider_name": "local_db",
                "model_name": "execution",
                "quantity": 9,
                "unit_name": "seconds",
                "amount_cents": 9,
            },
        )

    assert response.status_code == 201
    assert response.json()["usage_type"] == "cloud_run_runtime_seconds"


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
