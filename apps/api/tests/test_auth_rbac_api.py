from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    Organization,
    OrganizationMember,
    PlannerRun,
    Project,
    Repository,
    Task,
    User,
    Workspace,
    WorkspaceRole,
)
from ai_company_api.services.auth_context import hash_api_token


def build_client(database_path: Path | None = None, *, auth_mode: str = "dev") -> TestClient:
    database_url = "sqlite://" if database_path is None else f"sqlite:///{database_path.as_posix()}"
    return TestClient(create_app(database_url=database_url, auth_mode=auth_mode))


def auth_headers(
    *,
    user_id: str = "user_a",
    workspace_id: str = "workspace_a",
    organization_id: str = "org_a",
    roles: str = "owner",
) -> dict[str, str]:
    return {
        "x-ai-scdc-user-id": user_id,
        "x-ai-scdc-workspace-id": workspace_id,
        "x-ai-scdc-organization-id": organization_id,
        "x-ai-scdc-roles": roles,
    }


def test_me_uses_dev_auth_headers_instead_of_fixed_identity() -> None:
    with build_client() as client:
        response = client.get(
            "/me",
            headers=auth_headers(
                user_id="user_custom",
                workspace_id="workspace_custom",
                organization_id="org_custom",
                roles="developer,viewer",
            ),
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user_custom",
        "workspace_id": "workspace_custom",
        "organization_id": "org_custom",
        "roles": ["developer", "viewer"],
        "auth_mode": "dev",
    }


def test_api_token_auth_mode_rejects_missing_token(tmp_path: Path) -> None:
    with build_client(tmp_path / "app.db", auth_mode="api_token") as client:
        health = client.get("/health")
        response = client.get("/projects")

    assert health.status_code == 200
    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer API token is required"


def test_api_token_auth_mode_resolves_member_identity(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    token = "scdc_test_token_123"
    with Session(engine) as session:
        user = User(id="user_token", email="dev@example.test", display_name="Dev")
        organization = Organization(id="org_token", name="Token org")
        workspace = Workspace(
            id="workspace_token",
            organization_id=organization.id,
            name="Token workspace",
        )
        member = OrganizationMember(
            organization_id=organization.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
            api_token_hash=hash_api_token(token),
        )
        session.add(user)
        session.add(organization)
        session.add(workspace)
        session.add(member)
        session.commit()

    with TestClient(create_app(database_url=database_url, auth_mode="api_token")) as client:
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user_token",
        "workspace_id": "workspace_token",
        "organization_id": "org_token",
        "roles": ["admin"],
        "auth_mode": "api_token",
    }


def test_api_token_auth_mode_rejects_inactive_workspace_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    token = "scdc_inactive_workspace_token"
    with Session(engine) as session:
        user = User(id="user_token", email="dev@example.test", display_name="Dev")
        organization = Organization(id="org_token", name="Token org")
        workspace = Workspace(
            id="workspace_token",
            organization_id=organization.id,
            name="Token workspace",
            status="disabled",
        )
        member = OrganizationMember(
            organization_id=organization.id,
            workspace_id=workspace.id,
            user_id=user.id,
            role=WorkspaceRole.ADMIN,
            api_token_hash=hash_api_token(token),
        )
        session.add(user)
        session.add(organization)
        session.add(workspace)
        session.add(member)
        session.commit()

    with TestClient(create_app(database_url=database_url, auth_mode="api_token")) as client:
        response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API token"


def test_money_moving_workspace_endpoints_require_billing_role() -> None:
    endpoints = [
        (
            "post",
            "/workspace/credits/manual-grants",
            {"amount_cents": 100, "reason": "rbac test grant"},
            201,
        ),
        (
            "put",
            "/workspace/spend-limit",
            {"monthly_limit_cents": 1000, "per_run_limit_cents": 200},
            200,
        ),
    ]

    with build_client() as client:
        for role in ("viewer", "developer"):
            for method, path, payload, _success_status in endpoints:
                response = getattr(client, method)(
                    path,
                    json=payload,
                    headers=auth_headers(roles=role),
                )

                assert response.status_code == 403
                assert response.json()["detail"] == "Insufficient workspace role"

        for role in ("owner", "admin", "billing_manager"):
            for method, path, payload, success_status in endpoints:
                response = getattr(client, method)(
                    path,
                    json=payload,
                    headers=auth_headers(
                        workspace_id=f"workspace_{role}",
                        organization_id=f"org_{role}",
                        roles=role,
                    ),
                )

                assert response.status_code == success_status


def test_viewer_cannot_read_credentials_or_billing_detail() -> None:
    with build_client() as client:
        for path in (
            "/github-credentials",
            "/model-credentials",
            "/usage-ledger",
            "/workspace/usage-summary",
        ):
            response = client.get(path, headers=auth_headers(roles="viewer"))
            assert response.status_code == 403
            assert response.json()["detail"] == "Insufficient workspace role"


def test_billing_manager_can_read_billing_but_not_credentials_or_models() -> None:
    with build_client() as client:
        assert client.get(
            "/usage-ledger",
            headers=auth_headers(roles="billing_manager"),
        ).status_code == 200
        assert client.get(
            "/workspace/usage-summary",
            headers=auth_headers(roles="billing_manager"),
        ).status_code == 200

        for path in (
            "/github-credentials",
            "/model-credentials",
            "/model-providers",
            "/model-routes",
        ):
            response = client.get(path, headers=auth_headers(roles="billing_manager"))
            assert response.status_code == 403


def test_developer_and_reviewer_can_read_model_config_but_not_manage_it() -> None:
    with build_client() as client:
        for role in ("developer", "reviewer"):
            assert client.get(
                "/model-providers",
                headers=auth_headers(roles=role),
            ).status_code == 200
            create_response = client.post(
                "/model-providers",
                json={"name": f"provider-{role}", "provider_type": "fake"},
                headers=auth_headers(roles=role),
            )
            assert create_response.status_code == 403


def test_owner_and_admin_can_manage_credentials_and_model_config() -> None:
    with build_client() as client:
        for role in ("owner", "admin"):
            provider = client.post(
                "/model-providers",
                json={"name": f"fake-{role}", "provider_type": "fake"},
                headers=auth_headers(roles=role, workspace_id=f"workspace_{role}"),
            )
            assert provider.status_code == 201
            github_credential = client.post(
                "/github-credentials",
                json={"display_name": f"gh-{role}", "token": f"ghp_{role}_1234"},
                headers=auth_headers(roles=role, workspace_id=f"workspace_{role}"),
            )
            assert github_credential.status_code == 201


def test_workspace_scope_hides_projects_and_project_children() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
    )

    with build_client() as client:
        project_response = client.post(
            "/projects",
            json={"name": "Workspace A project"},
            headers=headers_a,
        )
        project = project_response.json()
        list_response = client.get("/projects", headers=headers_b)
        task_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Cross workspace task", "role_required": "backend"},
            headers=headers_b,
        )

    assert project_response.status_code == 201
    assert project["workspace_id"] == "workspace_a"
    assert project["created_by"] == "user_a"
    assert list_response.status_code == 200
    assert list_response.json() == []
    assert task_response.status_code == 404
    assert task_response.json()["detail"] == "Project not found"


def test_planner_run_records_current_user(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    headers = auth_headers(
        user_id="planner_user",
        workspace_id="workspace_planner",
        organization_id="org_planner",
    )

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "Planner workspace"},
            headers=headers,
        ).json()
        response = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Plan a small backend change"},
            headers=headers,
        )

    assert response.status_code == 201
    with Session(build_engine(database_url)) as session:
        planner_run = session.get(PlannerRun, response.json()["id"])

    assert planner_run is not None
    assert planner_run.created_by == "planner_user"


def test_task_create_rejects_cross_workspace_repo_reference(tmp_path: Path) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project_a = Project(
            id="project_a",
            workspace_id="workspace_a",
            name="Workspace A project",
        )
        project_b = Project(
            id="project_b",
            workspace_id="workspace_b",
            name="Workspace B project",
        )
        repository_b = Repository(
            id="repo_b",
            workspace_id="workspace_b",
            project_id=project_b.id,
            name="Repo B",
            local_path="",
        )
        session.add(project_a)
        session.add(project_b)
        session.add(repository_b)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/projects/project_a/tasks",
            json={
                "title": "Cross workspace repo task",
                "role_required": "backend",
                "repo_id": "repo_b",
            },
            headers=auth_headers(workspace_id="workspace_a", organization_id="org_a"),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Repository not found"


def test_workspace_scope_hides_model_settings_and_denies_cross_workspace_reference() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
    )

    with build_client() as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-a", "provider_type": "deepseek"},
            headers=headers_a,
        ).json()
        credential = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "Workspace A key",
                "secret_value": "sk-workspace-a1234",
            },
            headers=headers_a,
        ).json()

        provider_list_b = client.get("/model-providers", headers=headers_b)
        credential_list_b = client.get("/model-credentials", headers=headers_b)
        cross_credential = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "Workspace B key",
                "secret_value": "sk-workspace-b1234",
            },
            headers=headers_b,
        )

    assert credential["workspace_id"] == "workspace_a"
    assert provider_list_b.status_code == 200
    assert provider_list_b.json() == []
    assert credential_list_b.status_code == 200
    assert credential_list_b.json() == []
    assert cross_credential.status_code == 404
    assert cross_credential.json()["detail"] == "Model provider not found"


def test_workspace_scope_filters_usage_ledger() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
    )

    with build_client() as client:
        project = client.post(
            "/projects",
            json={"name": "Workspace A project"},
            headers=headers_a,
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "Backend task", "role_required": "backend"},
            headers=headers_a,
        ).json()
        usage = client.post(
            "/usage-ledger",
            json={
                "project_id": project["id"],
                "task_id": task["id"],
                "provider_name": "deepseek",
                "model_name": "deepseek-chat",
                "prompt_tokens": 5,
                "completion_tokens": 7,
            },
            headers=headers_a,
        ).json()
        all_for_b = client.get("/usage-ledger", headers=headers_b)
        project_for_b = client.get(
            "/usage-ledger",
            params={"project_id": project["id"]},
            headers=headers_b,
        )

    assert usage["workspace_id"] == "workspace_a"
    assert usage["organization_id"] == "org_a"
    assert usage["user_id"] == "user_a"
    assert all_for_b.status_code == 200
    assert all_for_b.json() == []
    assert project_for_b.status_code == 404
    assert project_for_b.json()["detail"] == "Project not found"


def test_workspace_scope_denies_cross_workspace_cloud_run_and_artifacts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project = Project(
            id="project_a",
            workspace_id="workspace_a",
            name="Workspace A project",
        )
        repository = Repository(
            id="repo_a",
            workspace_id="workspace_a",
            project_id=project.id,
            name="Repo A",
            local_path="",
            provider="github",
            repo_url="https://github.com/example/repo",
            github_owner="example",
            github_repo="repo",
        )
        task = Task(
            id="task_a",
            project_id=project.id,
            title="Task A",
            role_required="backend",
        )
        cloud_run = CloudRun(
            id="cloud_run_a",
            workspace_id="workspace_a",
            project_id=project.id,
            task_id=task.id,
            repo_id=repository.id,
            head_branch="ai-scdc/task-a",
        )
        session.add(project)
        session.add(repository)
        session.add(task)
        session.add(cloud_run)
        session.commit()

    headers_b = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
    )
    with TestClient(create_app(database_url=database_url)) as client:
        cloud_run_response = client.get("/cloud-runs/cloud_run_a", headers=headers_b)
        artifact_response = client.get(
            "/cloud-runs/cloud_run_a/artifacts/manifest",
            headers=headers_b,
        )

    assert cloud_run_response.status_code == 404
    assert cloud_run_response.json()["detail"] == "Cloud run not found"
    assert artifact_response.status_code == 404
    assert artifact_response.json()["detail"] == "Cloud run not found"


def test_workspace_permission_policy_declares_phase_13b_permissions() -> None:
    from ai_company_api.services.workspace_permissions import (
        PERMISSION_ROLES,
        allowed_roles_for_permission,
    )

    expected_permissions = {
        "workspace.metadata.read",
        "project.write",
        "repository.write",
        "conversation.write",
        "planner.write",
        "planner.review",
        "task.write",
        "run.write",
        "execution.evidence.read",
        "conversation.sensitive.read",
        "execution_config.read",
        "artifact.sensitive.read",
        "artifact.cleanup",
        "log.sensitive.read",
        "review.write",
        "approval.write",
        "pull_request.publish",
        "credential.metadata.read",
        "credential.write",
        "model_config.read",
        "model_config.write",
        "billing.read",
        "billing.write",
        "operator.write",
    }
    assert set(PERMISSION_ROLES) == expected_permissions
    assert {
        role.value for role in allowed_roles_for_permission("workspace.metadata.read")
    } == {
        "owner",
        "admin",
        "developer",
        "reviewer",
        "billing_manager",
        "viewer",
    }
    assert {
        role.value for role in allowed_roles_for_permission("credential.write")
    } == {"owner", "admin"}
    assert {
        role.value for role in allowed_roles_for_permission("billing.read")
    } == {"owner", "admin", "billing_manager"}
