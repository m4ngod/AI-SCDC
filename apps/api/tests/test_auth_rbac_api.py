from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    Organization,
    OrganizationMember,
    PatchArtifact,
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


def test_credential_delete_requires_owner_or_admin_role() -> None:
    with build_client() as client:
        for role in ("viewer", "developer"):
            headers = auth_headers(
                roles="owner",
                workspace_id=f"workspace_gh_delete_{role}",
                organization_id=f"org_gh_delete_{role}",
            )
            credential = client.post(
                "/github-credentials",
                json={"display_name": f"gh-{role}", "token": f"ghp_{role}_1234"},
                headers=headers,
            ).json()

            response = client.delete(
                f"/github-credentials/{credential['id']}",
                headers=auth_headers(
                    roles=role,
                    workspace_id=f"workspace_gh_delete_{role}",
                    organization_id=f"org_gh_delete_{role}",
                ),
            )

            assert response.status_code == 403
            assert response.json()["detail"] == "Insufficient workspace role"

        owner_headers = auth_headers(
            roles="owner",
            workspace_id="workspace_gh_delete_owner",
            organization_id="org_gh_delete_owner",
        )
        credential = client.post(
            "/github-credentials",
            json={"display_name": "gh-owner", "token": "ghp_owner_delete_1234"},
            headers=owner_headers,
        ).json()
        response = client.delete(
            f"/github-credentials/{credential['id']}",
            headers=owner_headers,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


def test_model_credential_delete_requires_owner_or_admin_role() -> None:
    with build_client() as client:
        for role in ("viewer", "developer"):
            owner_headers = auth_headers(
                roles="owner",
                workspace_id=f"workspace_model_delete_{role}",
                organization_id=f"org_model_delete_{role}",
            )
            provider = client.post(
                "/model-providers",
                json={"name": f"provider-{role}", "provider_type": "deepseek"},
                headers=owner_headers,
            ).json()
            credential = client.post(
                "/model-credentials",
                json={
                    "provider_id": provider["id"],
                    "display_name": f"model-{role}",
                    "secret_value": f"sk-{role}-delete-1234",
                },
                headers=owner_headers,
            ).json()

            response = client.delete(
                f"/model-credentials/{credential['id']}",
                headers=auth_headers(
                    roles=role,
                    workspace_id=f"workspace_model_delete_{role}",
                    organization_id=f"org_model_delete_{role}",
                ),
            )

            assert response.status_code == 403
            assert response.json()["detail"] == "Insufficient workspace role"

        owner_headers = auth_headers(
            roles="owner",
            workspace_id="workspace_model_delete_owner",
            organization_id="org_model_delete_owner",
        )
        provider = client.post(
            "/model-providers",
            json={"name": "provider-owner", "provider_type": "deepseek"},
            headers=owner_headers,
        ).json()
        credential = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "model-owner",
                "secret_value": "sk-owner-delete-1234",
            },
            headers=owner_headers,
        ).json()
        response = client.delete(
            f"/model-credentials/{credential['id']}",
            headers=owner_headers,
        )

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"


def test_model_route_patch_requires_owner_or_admin_role() -> None:
    with build_client() as client:
        for role in ("developer", "reviewer", "viewer", "billing_manager"):
            owner_headers = auth_headers(
                roles="owner",
                workspace_id=f"workspace_route_patch_{role}",
                organization_id=f"org_route_patch_{role}",
            )
            provider = client.post(
                "/model-providers",
                json={"name": f"provider-{role}", "provider_type": "fake"},
                headers=owner_headers,
            ).json()
            route = client.post(
                "/model-routes",
                json={
                    "agent_role": "planner",
                    "provider_id": provider["id"],
                    "model_name": f"fake-{role}",
                },
                headers=owner_headers,
            ).json()

            response = client.patch(
                f"/model-routes/{route['id']}",
                json={"model_name": f"fake-{role}-updated"},
                headers=auth_headers(
                    roles=role,
                    workspace_id=f"workspace_route_patch_{role}",
                    organization_id=f"org_route_patch_{role}",
                ),
            )

            assert response.status_code == 403
            assert response.json()["detail"] == "Insufficient workspace role"

        for role in ("owner", "admin"):
            headers = auth_headers(
                roles=role,
                workspace_id=f"workspace_route_patch_{role}",
                organization_id=f"org_route_patch_{role}",
            )
            provider = client.post(
                "/model-providers",
                json={"name": f"provider-{role}", "provider_type": "fake"},
                headers=headers,
            ).json()
            route = client.post(
                "/model-routes",
                json={
                    "agent_role": "planner",
                    "provider_id": provider["id"],
                    "model_name": f"fake-{role}",
                },
                headers=headers,
            ).json()
            response = client.patch(
                f"/model-routes/{route['id']}",
                json={"model_name": f"fake-{role}-updated"},
                headers=headers,
            )

            assert response.status_code == 200
            assert response.json()["model_name"] == f"fake-{role}-updated"


def test_cross_workspace_credential_delete_and_model_route_patch_still_hide_resource() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b_developer = auth_headers(
        user_id="user_b",
        workspace_id="workspace_b",
        organization_id="org_b",
        roles="developer",
    )

    with build_client() as client:
        credential = client.post(
            "/github-credentials",
            json={"display_name": "gh-cross", "token": "ghp_cross_1234"},
            headers=headers_a,
        ).json()
        provider = client.post(
            "/model-providers",
            json={"name": "fake-cross", "provider_type": "fake"},
            headers=headers_a,
        ).json()
        route = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "model_name": "fake-cross",
            },
            headers=headers_a,
        ).json()

        delete_response = client.delete(
            f"/github-credentials/{credential['id']}",
            headers=headers_b_developer,
        )
        patch_response = client.patch(
            f"/model-routes/{route['id']}",
            json={"model_name": "fake-cross-updated"},
            headers=headers_b_developer,
        )

    assert delete_response.status_code == 404
    assert delete_response.json()["detail"] == "GitHub credential not found"
    assert patch_response.status_code == 404
    assert patch_response.json()["detail"] == "Model route not found"


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


def test_viewer_and_billing_manager_cannot_create_execution_resources() -> None:
    with build_client() as client:
        project = client.post(
            "/projects",
            json={"name": "execution matrix"},
            headers=auth_headers(roles="owner"),
        ).json()

        for role in ("viewer", "billing_manager", "reviewer"):
            task_response = client.post(
                f"/projects/{project['id']}/tasks",
                json={"title": f"task {role}", "role_required": "backend"},
                headers=auth_headers(roles=role),
            )
            assert task_response.status_code == 403


def test_reviewer_can_review_but_cannot_start_run_or_publish_pr(tmp_path: Path) -> None:
    with build_client(tmp_path / "reviewer.db") as client:
        project = client.post(
            "/projects",
            json={"name": "review permissions"},
            headers=auth_headers(roles="owner"),
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "review target", "role_required": "backend"},
            headers=auth_headers(roles="developer"),
        ).json()

        start_response = client.post(
            f"/tasks/{task['id']}/cloud-runs",
            json={"repo_id": "repo_missing"},
            headers=auth_headers(roles="reviewer"),
        )
        assert start_response.status_code == 403


def test_cross_workspace_task_create_still_hides_project() -> None:
    headers_a = auth_headers(workspace_id="workspace_a", organization_id="org_a")
    headers_b = auth_headers(
        user_id="dev_b",
        workspace_id="workspace_b",
        organization_id="org_b",
        roles="viewer",
    )
    with build_client() as client:
        project = client.post(
            "/projects",
            json={"name": "private project"},
            headers=headers_a,
        ).json()
        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "hidden", "role_required": "backend"},
            headers=headers_b,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


def test_planner_review_reviewer_can_approve_and_reject_but_not_create_task_or_start_run(
    tmp_path: Path,
) -> None:
    with build_client(tmp_path / "planner-reviewer.db") as client:
        project = client.post(
            "/projects",
            json={"name": "planner reviewer permissions"},
            headers=auth_headers(roles="owner"),
        ).json()
        approve_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build reviewer approval task"},
            headers=auth_headers(roles="developer"),
        ).json()
        reject_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Build reviewer rejection task"},
            headers=auth_headers(roles="developer"),
        ).json()

        task_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "reviewer task", "role_required": "backend"},
            headers=auth_headers(roles="reviewer"),
        )
        approve_response = client.post(
            f"/planner-runs/{approve_run['id']}/approve",
            headers=auth_headers(roles="reviewer"),
        )
        reject_response = client.post(
            f"/planner-runs/{reject_run['id']}/reject",
            json={"reason": "not now"},
            headers=auth_headers(roles="reviewer"),
        )
        created_task = approve_response.json()["created_tasks"][0]
        run_response = client.post(
            f"/tasks/{created_task['id']}/cloud-runs",
            json={"repo_id": "repo_missing"},
            headers=auth_headers(roles="reviewer"),
        )

    assert task_response.status_code == 403
    assert approve_response.status_code == 200
    assert reject_response.status_code == 200
    assert run_response.status_code == 403


def test_patch_approval_developer_cannot_review_planner_run_or_approve_patch_artifact(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "developer-review.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project = Project(
            id="project_patch_approval",
            workspace_id="workspace_a",
            name="Patch approval project",
        )
        task = Task(
            id="task_patch_approval",
            project_id=project.id,
            title="Patch approval task",
            role_required="backend",
            status="APPROVED",
        )
        artifact = PatchArtifact(
            id="artifact_patch_approval",
            workspace_id=project.workspace_id,
            project_id=project.id,
            task_id=task.id,
            local_run_id="local_run_missing",
            summary="Patch",
            files_changed=["app.py"],
            test_result="passed",
            diff_text="diff --git a/app.py b/app.py",
        )
        session.add(project)
        session.add(task)
        session.add(artifact)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "planner developer denied"},
            headers=auth_headers(roles="owner"),
        ).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Developer cannot decide"},
            headers=auth_headers(roles="developer"),
        ).json()

        approve_response = client.post(
            f"/planner-runs/{planner_run['id']}/approve",
            headers=auth_headers(roles="developer"),
        )
        reject_response = client.post(
            f"/planner-runs/{planner_run['id']}/reject",
            json={"reason": "no"},
            headers=auth_headers(roles="developer"),
        )
        patch_approval_response = client.post(
            "/patch-artifacts/artifact_patch_approval/approvals",
            headers=auth_headers(roles="developer"),
        )

    assert approve_response.status_code == 403
    assert reject_response.status_code == 403
    assert patch_approval_response.status_code == 403


def test_developer_can_create_task_and_pass_run_permission_to_validation(
    tmp_path: Path,
) -> None:
    with build_client(tmp_path / "developer-run.db") as client:
        project = client.post(
            "/projects",
            json={"name": "developer execution"},
            headers=auth_headers(roles="owner"),
        ).json()
        task_response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "developer task", "role_required": "backend"},
            headers=auth_headers(roles="developer"),
        )
        run_response = client.post(
            f"/tasks/{task_response.json()['id']}/cloud-runs",
            json={"repo_id": "repo_missing"},
            headers=auth_headers(roles="developer"),
        )

    assert task_response.status_code == 201
    assert run_response.status_code == 404
    assert run_response.json()["detail"] == "Repository not found"
