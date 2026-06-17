import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    CloudRun,
    Conversation,
    GitHubCredential,
    GitHubCredentialStatus,
    LocalTaskRun,
    ModelCredential,
    ModelCredentialStatus,
    PatchArtifact,
    Project,
    Repository,
    SecretAccessAuditLog,
    Task,
    WorkspaceAuditLog,
    WorkspaceRole,
)
from ai_company_api.services.auth_context import AuthContext, _current_auth_context
from ai_company_api.services.workspace_audit import (
    MAX_AUDIT_STRING_LENGTH,
    require_audited_workspace_permission,
    record_workspace_audit,
    redact_audit_metadata,
)


def test_workspace_audit_table_is_created() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)

    with Session(engine) as session:
        rows = session.exec(select(WorkspaceAuditLog)).all()

    assert rows == []


def test_redact_audit_metadata_removes_sensitive_values() -> None:
    long_safe_value = "a" * (MAX_AUDIT_STRING_LENGTH + 1)
    metadata = {
        "safe_id": "cloud_run_1",
        "safe_note": "short note",
        "long_safe_note": long_safe_value,
        "token": "ghp_secret",
        "queue_receipt": "receipt_secret",
        "download_url": "https://example.test/private",
        "nested": {"stdout": "secret output", "status": "ok"},
        "messages": ["secret log line"],
    }

    assert redact_audit_metadata(metadata) == {
        "safe_id": "cloud_run_1",
        "safe_note": "short note",
        "long_safe_note": f"{long_safe_value[:MAX_AUDIT_STRING_LENGTH]}...",
        "token": "[redacted]",
        "queue_receipt": "[redacted]",
        "download_url": "[redacted]",
        "nested": {"stdout": "[redacted]", "status": "ok"},
        "messages": "[redacted]",
    }


def test_record_workspace_audit_redacts_and_commits_without_auth_context(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)

    with Session(engine) as session:
        log = record_workspace_audit(
            session,
            operation="unit.audit",
            resource_type="unit",
            resource_id="unit_1",
            access_level="high_value_write",
            success=True,
            status_code=200,
            metadata={"token": "secret", "status": "ok"},
            commit=True,
        )

    with Session(build_engine(database_url)) as session:
        stored = session.get(WorkspaceAuditLog, log.id)

    assert stored is not None
    assert stored.workspace_id == "dev_workspace"
    assert stored.organization_id == "dev_organization"
    assert stored.user_id == "dev_user"
    assert stored.auth_mode == "system"
    assert stored.metadata_json == {"token": "[redacted]", "status": "ok"}


def test_denied_audited_permission_does_not_commit_caller_pending_objects(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    context = AuthContext(
        user_id="viewer_user",
        workspace_id="workspace_a",
        organization_id="org_a",
        roles=frozenset({WorkspaceRole.VIEWER}),
        auth_mode="dev",
    )
    token = _current_auth_context.set(context)
    try:
        with Session(engine) as session:
            session.add(Project(id="pending_project", name="Should not persist"))

            with pytest.raises(HTTPException) as exc_info:
                require_audited_workspace_permission(
                    session,
                    "credential.write",
                    operation="unit.denied",
                    resource_type="unit",
                    resource_id="unit_1",
                    access_level="high_value_write",
                )

            assert exc_info.value.status_code == 403
            session.rollback()
    finally:
        _current_auth_context.reset(token)

    with Session(build_engine(database_url)) as session:
        project = session.get(Project, "pending_project")
        audit_logs = session.exec(select(WorkspaceAuditLog)).all()

    assert project is None
    assert len(audit_logs) == 1
    assert audit_logs[0].operation == "unit.denied"
    assert audit_logs[0].success is False
    assert audit_logs[0].status_code == 403
    assert audit_logs[0].error_code == "insufficient_workspace_role"


def test_denied_audited_permission_rolls_back_flushed_file_backed_writes(
    tmp_path,
) -> None:
    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    context = AuthContext(
        user_id="viewer_user",
        workspace_id="workspace_a",
        organization_id="org_a",
        roles=frozenset({WorkspaceRole.VIEWER}),
        auth_mode="dev",
    )
    token = _current_auth_context.set(context)
    try:
        with Session(engine) as session:
            session.add(Project(id="flushed_project", name="Should not persist"))
            session.flush()

            with pytest.raises(HTTPException) as exc_info:
                require_audited_workspace_permission(
                    session,
                    "credential.write",
                    operation="unit.denied_flushed",
                    resource_type="unit",
                    resource_id="unit_1",
                    access_level="high_value_write",
                )

            assert exc_info.value.status_code == 403
    finally:
        _current_auth_context.reset(token)

    with Session(build_engine(database_url)) as session:
        project = session.get(Project, "flushed_project")
        audit_logs = session.exec(select(WorkspaceAuditLog)).all()

    assert project is None
    assert len(audit_logs) == 1
    assert audit_logs[0].operation == "unit.denied_flushed"
    assert audit_logs[0].success is False
    assert audit_logs[0].status_code == 403
    assert audit_logs[0].error_code == "insufficient_workspace_role"


def test_denied_audited_permission_rolls_back_flushed_staticpool_writes() -> None:
    engine = build_engine("sqlite://")
    init_db(engine)
    context = AuthContext(
        user_id="viewer_user",
        workspace_id="workspace_a",
        organization_id="org_a",
        roles=frozenset({WorkspaceRole.VIEWER}),
        auth_mode="dev",
    )
    token = _current_auth_context.set(context)
    try:
        with Session(engine) as session:
            session.add(Project(id="flushed_static_project", name="Should not persist"))
            session.flush()

            with pytest.raises(HTTPException) as exc_info:
                require_audited_workspace_permission(
                    session,
                    "credential.write",
                    operation="unit.denied_flushed_static",
                    resource_type="unit",
                    resource_id="unit_1",
                    access_level="high_value_write",
                )

            assert exc_info.value.status_code == 403
    finally:
        _current_auth_context.reset(token)

    with Session(engine) as session:
        project = session.get(Project, "flushed_static_project")
        audit_logs = session.exec(select(WorkspaceAuditLog)).all()

    assert project is None
    assert len(audit_logs) == 1
    assert audit_logs[0].operation == "unit.denied_flushed_static"
    assert audit_logs[0].success is False
    assert audit_logs[0].status_code == 403
    assert audit_logs[0].error_code == "insufficient_workspace_role"


def test_sensitive_collection_read_records_redacted_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.get(
            "/github-credentials",
            headers={
                "x-ai-scdc-user-id": "owner_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "owner",
            },
        )

    assert response.status_code == 200
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(select(WorkspaceAuditLog)).one()

    assert audit_log.operation == "github_credential.list"
    assert audit_log.resource_type == "github_credential"
    assert audit_log.access_level.value == "high_sensitive_read"
    assert audit_log.success is True
    assert audit_log.workspace_id == "workspace_a"


def test_denied_collection_read_records_audit_without_payload(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.get(
            "/github-credentials",
            headers={
                "x-ai-scdc-user-id": "viewer_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "viewer",
            },
        )

    assert response.status_code == 403
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(select(WorkspaceAuditLog)).one()

    assert audit_log.operation == "github_credential.list"
    assert audit_log.success is False
    assert audit_log.status_code == 403
    assert audit_log.error_code == "insufficient_workspace_role"
    assert "ghp_" not in str(audit_log.model_dump())


def test_github_credential_create_records_success_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/github-credentials",
            json={"display_name": "GitHub write audit", "token": "ghp_success_1234"},
            headers={
                "x-ai-scdc-user-id": "owner_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "owner",
            },
        )

    assert response.status_code == 201
    credential = response.json()
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "github_credential.create"
            )
        ).one()

    assert audit_log.resource_type == "github_credential"
    assert audit_log.resource_id == credential["id"]
    assert audit_log.access_level.value == "high_value_write"
    assert audit_log.success is True
    assert audit_log.status_code == 201


def test_github_credential_create_response_matches_list_after_audit_commit(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    headers = {
        "x-ai-scdc-user-id": "owner_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "owner",
    }

    with TestClient(create_app(database_url=database_url)) as client:
        create_response = client.post(
            "/github-credentials",
            json={"display_name": "GitHub consistency", "token": "ghp_consistent_1234"},
            headers=headers,
        )
        list_response = client.get("/github-credentials", headers=headers)

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert list_response.json() == [create_response.json()]


def test_model_credential_create_response_matches_list_after_audit_commit(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    headers = {
        "x-ai-scdc-user-id": "owner_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "owner",
    }

    with TestClient(create_app(database_url=database_url)) as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-consistency", "provider_type": "deepseek"},
            headers=headers,
        ).json()
        create_response = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "DeepSeek consistency",
                "secret_value": "sk-consistent1234",
            },
            headers=headers,
        )
        list_response = client.get("/model-credentials", headers=headers)

    assert create_response.status_code == 201
    assert list_response.status_code == 200
    assert list_response.json() == [create_response.json()]


def test_github_credential_audit_failure_rolls_back_mutation(
    tmp_path,
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient

    from ai_company_api.api import routes as api_routes

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    def fail_success_workspace_audit(*args, **kwargs):
        if kwargs.get("operation") == "github_credential.create":
            raise RuntimeError("workspace audit failed")
        return original_record_workspace_audit(*args, **kwargs)

    original_record_workspace_audit = api_routes.record_workspace_audit
    monkeypatch.setattr(
        api_routes,
        "record_workspace_audit",
        fail_success_workspace_audit,
    )

    with TestClient(
        create_app(database_url=database_url),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/github-credentials",
            json={"display_name": "GitHub rollback", "token": "ghp_rollback_1234"},
            headers={
                "x-ai-scdc-user-id": "owner_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "owner",
            },
        )

    assert response.status_code == 500
    with Session(build_engine(database_url)) as session:
        credentials = session.exec(select(GitHubCredential)).all()
        secret_audit_logs = session.exec(select(SecretAccessAuditLog)).all()
        workspace_audit_logs = session.exec(select(WorkspaceAuditLog)).all()

    assert credentials == []
    assert secret_audit_logs == []
    assert workspace_audit_logs == []


def test_denied_github_credential_delete_records_audit_and_keeps_credential(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    owner_headers = {
        "x-ai-scdc-user-id": "owner_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "owner",
    }
    viewer_headers = {
        "x-ai-scdc-user-id": "viewer_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "viewer",
    }

    with TestClient(create_app(database_url=database_url)) as client:
        credential = client.post(
            "/github-credentials",
            json={"display_name": "GitHub denied delete", "token": "ghp_denied_1234"},
            headers=owner_headers,
        ).json()
        response = client.delete(
            f"/github-credentials/{credential['id']}",
            headers=viewer_headers,
        )

    assert response.status_code == 403
    with Session(build_engine(database_url)) as session:
        stored_credential = session.get(GitHubCredential, credential["id"])
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "github_credential.delete"
            )
        ).one()

    assert stored_credential is not None
    assert stored_credential.status == GitHubCredentialStatus.ACTIVE
    assert audit_log.resource_id == credential["id"]
    assert audit_log.success is False
    assert audit_log.status_code == 403
    assert audit_log.error_code == "insufficient_workspace_role"


def test_github_credential_delete_records_success_audit_and_secret_audit(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    headers = {
        "x-ai-scdc-user-id": "owner_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "owner",
    }

    with TestClient(create_app(database_url=database_url)) as client:
        credential = client.post(
            "/github-credentials",
            json={"display_name": "GitHub delete audit", "token": "ghp_delete_1234"},
            headers=headers,
        ).json()
        response = client.delete(
            f"/github-credentials/{credential['id']}",
            headers=headers,
        )

    assert response.status_code == 200
    with Session(build_engine(database_url)) as session:
        stored_credential = session.get(GitHubCredential, credential["id"])
        workspace_audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "github_credential.delete"
            )
        ).one()
        secret_delete_log = session.exec(
            select(SecretAccessAuditLog).where(
                SecretAccessAuditLog.operation == "delete"
            )
        ).one()

    assert stored_credential is not None
    assert stored_credential.status == GitHubCredentialStatus.DELETED
    assert workspace_audit_log.resource_id == credential["id"]
    assert workspace_audit_log.resource_type == "github_credential"
    assert workspace_audit_log.success is True
    assert workspace_audit_log.status_code == 200
    assert secret_delete_log.secret_kind == "github_credential"
    assert secret_delete_log.secret_id == credential["id"]


def test_model_credential_delete_records_success_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    headers = {
        "x-ai-scdc-user-id": "owner_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "owner",
    }

    with TestClient(create_app(database_url=database_url)) as client:
        provider = client.post(
            "/model-providers",
            json={"name": "deepseek-delete-audit", "provider_type": "deepseek"},
            headers=headers,
        ).json()
        credential = client.post(
            "/model-credentials",
            json={
                "provider_id": provider["id"],
                "display_name": "DeepSeek delete audit",
                "secret_value": "sk-delete-audit-1234",
            },
            headers=headers,
        ).json()
        response = client.delete(
            f"/model-credentials/{credential['id']}",
            headers=headers,
        )

    assert response.status_code == 200
    with Session(build_engine(database_url)) as session:
        stored_credential = session.get(ModelCredential, credential["id"])
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "model_credential.delete"
            )
        ).one()

    assert stored_credential is not None
    assert stored_credential.status == ModelCredentialStatus.DELETED
    assert audit_log.resource_id == credential["id"]
    assert audit_log.resource_type == "model_credential"
    assert audit_log.success is True
    assert audit_log.status_code == 200


def test_model_route_update_records_success_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    headers = {
        "x-ai-scdc-user-id": "owner_user",
        "x-ai-scdc-workspace-id": "workspace_a",
        "x-ai-scdc-organization-id": "org_a",
        "x-ai-scdc-roles": "owner",
    }

    with TestClient(create_app(database_url=database_url)) as client:
        provider = client.post(
            "/model-providers",
            json={"name": "fake-update-audit", "provider_type": "fake"},
            headers=headers,
        ).json()
        route = client.post(
            "/model-routes",
            json={
                "agent_role": "planner",
                "provider_id": provider["id"],
                "model_name": "fake-before",
            },
            headers=headers,
        ).json()
        response = client.patch(
            f"/model-routes/{route['id']}",
            json={"model_name": "fake-after"},
            headers=headers,
        )

    assert response.status_code == 200
    with Session(build_engine(database_url)) as session:
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "model_route.update"
            )
        ).one()

    assert audit_log.resource_id == route["id"]
    assert audit_log.resource_type == "model_route"
    assert audit_log.success is True
    assert audit_log.status_code == 200


def test_high_value_task_write_records_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "audit project"},
            headers={
                "x-ai-scdc-user-id": "dev_user_a",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "developer",
            },
        ).json()
        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "audit task", "role_required": "backend"},
            headers={
                "x-ai-scdc-user-id": "dev_user_a",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "developer",
            },
        )

    assert response.status_code == 201
    with Session(build_engine(database_url)) as session:
        audit_logs = session.exec(select(WorkspaceAuditLog)).all()

    assert any(
        log.operation == "task.create"
        and log.resource_type == "task"
        and log.success is True
        and log.access_level.value == "high_value_write"
        for log in audit_logs
    )


def test_task_create_denied_records_failed_audit_and_does_not_create_task(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "app.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "denied task audit"},
            headers={
                "x-ai-scdc-user-id": "owner_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "owner",
            },
        ).json()
        response = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "denied task", "role_required": "backend"},
            headers={
                "x-ai-scdc-user-id": "viewer_user",
                "x-ai-scdc-workspace-id": "workspace_a",
                "x-ai-scdc-organization-id": "org_a",
                "x-ai-scdc-roles": "viewer",
            },
        )

    assert response.status_code == 403
    with Session(build_engine(database_url)) as session:
        tasks = session.exec(select(Task)).all()
        audit_log = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "task.create"
            )
        ).one()

    assert tasks == []
    assert audit_log.resource_type == "task"
    assert audit_log.resource_id is None
    assert audit_log.success is False
    assert audit_log.status_code == 403
    assert audit_log.error_code == "insufficient_workspace_role"
    assert audit_log.access_level.value == "high_value_write"


def _auth_headers(
    *,
    user_id: str,
    workspace_id: str = "workspace_a",
    organization_id: str = "org_a",
    roles: str,
) -> dict[str, str]:
    return {
        "x-ai-scdc-user-id": user_id,
        "x-ai-scdc-workspace-id": workspace_id,
        "x-ai-scdc-organization-id": organization_id,
        "x-ai-scdc-roles": roles,
    }


def _failed_audit_logs(
    database_url: str,
    operation: str,
) -> list[WorkspaceAuditLog]:
    with Session(build_engine(database_url)) as session:
        return list(
            session.exec(
                select(WorkspaceAuditLog)
                .where(WorkspaceAuditLog.operation == operation)
                .where(WorkspaceAuditLog.success.is_(False))
            ).all()
        )


def test_repository_create_denied_before_invalid_path_validation(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "repo-create-denied.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "repo create denied"},
            headers=_auth_headers(user_id="owner_user", roles="owner"),
        ).json()
        response = client.post(
            f"/projects/{project['id']}/repositories",
            json={
                "name": "invalid local repo",
                "local_path": str(tmp_path / "does-not-exist"),
                "default_branch": "main",
            },
            headers=_auth_headers(user_id="viewer_user", roles="viewer"),
        )

    assert response.status_code == 403
    audit_logs = _failed_audit_logs(database_url, "repository.create")
    assert len(audit_logs) == 1
    assert audit_logs[0].resource_type == "repository"
    assert audit_logs[0].error_code == "insufficient_workspace_role"


def test_planner_review_denied_before_already_decided_validation(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "planner-review-denied.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "planner review denied"},
            headers=_auth_headers(user_id="owner_user", roles="owner"),
        ).json()
        planner_run = client.post(
            f"/projects/{project['id']}/planner-runs",
            json={"goal": "Create a decided planner run"},
            headers=_auth_headers(user_id="developer_user", roles="developer"),
        ).json()
        decided = client.post(
            f"/planner-runs/{planner_run['id']}/approve",
            headers=_auth_headers(user_id="reviewer_user", roles="reviewer"),
        )
        approve_again = client.post(
            f"/planner-runs/{planner_run['id']}/approve",
            headers=_auth_headers(user_id="developer_user", roles="developer"),
        )
        reject_after_decision = client.post(
            f"/planner-runs/{planner_run['id']}/reject",
            json={"reason": "too late"},
            headers=_auth_headers(user_id="developer_user", roles="developer"),
        )

    assert decided.status_code == 200
    assert approve_again.status_code == 403
    assert reject_after_decision.status_code == 403
    approve_audit = _failed_audit_logs(database_url, "planner_run.approve")
    reject_audit = _failed_audit_logs(database_url, "planner_run.reject")
    assert len(approve_audit) == 1
    assert len(reject_audit) == 1
    assert approve_audit[0].resource_id == planner_run["id"]
    assert reject_audit[0].resource_id == planner_run["id"]


def test_task_transition_denied_before_invalid_transition_validation(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "task-transition-denied.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    with TestClient(create_app(database_url=database_url)) as client:
        project = client.post(
            "/projects",
            json={"name": "task transition denied"},
            headers=_auth_headers(user_id="owner_user", roles="owner"),
        ).json()
        task = client.post(
            f"/projects/{project['id']}/tasks",
            json={"title": "transition target", "role_required": "backend"},
            headers=_auth_headers(user_id="developer_user", roles="developer"),
        ).json()
        response = client.patch(
            f"/tasks/{task['id']}",
            json={"status": "REVIEWING"},
            headers=_auth_headers(user_id="reviewer_user", roles="reviewer"),
        )

    assert response.status_code == 403
    audit_logs = _failed_audit_logs(database_url, "task.transition")
    assert len(audit_logs) == 1
    assert audit_logs[0].resource_id == task["id"]


def test_cloud_run_process_denied_before_non_queued_validation(tmp_path) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "cloud-process-denied.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project = Project(id="project_process", workspace_id="workspace_a", name="Project")
        task = Task(
            id="task_process",
            project_id=project.id,
            title="Process task",
            role_required="backend",
        )
        repository = Repository(
            id="repo_process",
            workspace_id="workspace_a",
            project_id=project.id,
            name="Repo",
            local_path="",
        )
        cloud_run = CloudRun(
            id="cloud_run_process",
            workspace_id="workspace_a",
            project_id=project.id,
            task_id=task.id,
            repo_id=repository.id,
            head_branch="ai-scdc/process",
            status="failed",
        )
        session.add(project)
        session.add(task)
        session.add(repository)
        session.add(cloud_run)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/cloud-runs/cloud_run_process/process",
            headers=_auth_headers(user_id="reviewer_user", roles="reviewer"),
        )

    assert response.status_code == 403
    audit_logs = _failed_audit_logs(database_url, "cloud_run.process")
    assert len(audit_logs) == 1
    assert audit_logs[0].resource_id == "cloud_run_process"


def test_sandbox_profile_create_denied_before_invalid_command_validation(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "sandbox-denied.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project = Project(id="project_sandbox", workspace_id="workspace_a", name="Project")
        repository = Repository(
            id="repo_sandbox",
            workspace_id="workspace_a",
            project_id=project.id,
            name="Repo",
            local_path="",
            provider="github",
            repo_url="https://github.com/example/repo",
            github_owner="example",
            github_repo="repo",
            connection_status="active",
        )
        session.add(project)
        session.add(repository)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/projects/project_sandbox/sandbox-profiles",
            json={
                "repo_id": "repo_sandbox",
                "name": "Denied invalid profile",
                "docker_image": "python:3.11-slim",
                "patch_commands": [
                    {
                        "key": "patch",
                        "label": "Patch",
                        "command": "python patch.py",
                        "is_default": False,
                    }
                ],
            },
            headers=_auth_headers(user_id="viewer_user", roles="viewer"),
        )

    assert response.status_code == 403
    audit_logs = _failed_audit_logs(database_url, "sandbox_profile.create")
    assert len(audit_logs) == 1
    assert audit_logs[0].resource_type == "sandbox_profile"


def test_cloud_and_local_run_start_hide_cross_workspace_repo_before_permission(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "cross-repo-run.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project_a = Project(id="project_a", workspace_id="workspace_a", name="A")
        task_a = Task(
            id="task_a",
            project_id=project_a.id,
            title="Task A",
            role_required="backend",
        )
        project_b = Project(id="project_b", workspace_id="workspace_b", name="B")
        repo_b = Repository(
            id="repo_b",
            workspace_id="workspace_b",
            project_id=project_b.id,
            name="Repo B",
            local_path="",
            provider="github",
            repo_url="https://github.com/example/repo-b",
            github_owner="example",
            github_repo="repo-b",
            connection_status="active",
        )
        session.add(project_a)
        session.add(task_a)
        session.add(project_b)
        session.add(repo_b)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        headers = _auth_headers(user_id="viewer_user", roles="viewer")
        cloud_response = client.post(
            "/tasks/task_a/cloud-runs",
            json={"repo_id": "repo_b"},
            headers=headers,
        )
        local_response = client.post(
            "/tasks/task_a/local-runs",
            json={"repo_id": "repo_b"},
            headers=headers,
        )

    assert cloud_response.status_code == 404
    assert cloud_response.json()["detail"] == "Repository not found"
    assert local_response.status_code == 404
    assert local_response.json()["detail"] == "Repository not found"
    assert _failed_audit_logs(database_url, "cloud_run.start") == []
    assert _failed_audit_logs(database_url, "local_run.start") == []


def test_planner_run_create_denied_before_same_workspace_conversation_mismatch(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "planner-conversation-mismatch.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project_a = Project(id="project_a", workspace_id="workspace_a", name="A")
        project_b = Project(id="project_b", workspace_id="workspace_a", name="B")
        conversation_b = Conversation(
            id="conversation_b",
            project_id=project_b.id,
            user_id="owner_user",
            title="Other project conversation",
        )
        session.add(project_a)
        session.add(project_b)
        session.add(conversation_b)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/projects/project_a/planner-runs",
            json={"goal": "Plan with wrong conversation", "conversation_id": "conversation_b"},
            headers=_auth_headers(user_id="viewer_user", roles="viewer"),
        )

    assert response.status_code == 403
    audit_logs = _failed_audit_logs(database_url, "planner_run.create")
    assert len(audit_logs) == 1
    assert audit_logs[0].resource_type == "planner_run"
    assert audit_logs[0].error_code == "insufficient_workspace_role"


def test_task_create_denied_before_same_workspace_repository_mismatch(
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    database_path = tmp_path / "task-repository-mismatch.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project_a = Project(id="project_a", workspace_id="workspace_a", name="A")
        project_b = Project(id="project_b", workspace_id="workspace_a", name="B")
        repo_b = Repository(
            id="repo_b",
            workspace_id="workspace_a",
            project_id=project_b.id,
            name="Repo B",
            local_path="",
        )
        session.add(project_a)
        session.add(project_b)
        session.add(repo_b)
        session.commit()

    with TestClient(create_app(database_url=database_url)) as client:
        response = client.post(
            "/projects/project_a/tasks",
            json={
                "title": "Wrong repo task",
                "role_required": "backend",
                "repo_id": "repo_b",
            },
            headers=_auth_headers(user_id="viewer_user", roles="viewer"),
        )

    assert response.status_code == 403
    audit_logs = _failed_audit_logs(database_url, "task.create")
    assert len(audit_logs) == 1
    assert audit_logs[0].resource_type == "task"
    assert audit_logs[0].error_code == "insufficient_workspace_role"


def test_local_run_success_audit_failure_rolls_back_business_rows(
    tmp_path,
    monkeypatch,
) -> None:
    from fastapi.testclient import TestClient
    from ai_company_worker.local_runner import LocalRunnerResult

    from ai_company_api.services import local_runner as local_runner_service

    database_path = tmp_path / "local-run-audit-rollback.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = build_engine(database_url)
    init_db(engine)
    with Session(engine) as session:
        project = Project(id="project_local", workspace_id="workspace_a", name="Project")
        repository = Repository(
            id="repo_local",
            workspace_id="workspace_a",
            project_id=project.id,
            name="Repo",
            local_path="",
        )
        task = Task(
            id="task_local",
            project_id=project.id,
            title="Local task",
            role_required="backend",
            allowed_paths=["README.md"],
        )
        session.add(project)
        session.add(repository)
        session.add(task)
        session.commit()

    def fake_run_local_task(_request):
        return LocalRunnerResult(
            status="patch_ready",
            summary="Prepared patch.",
            files_changed=["README.md"],
            tests_run=[],
            test_result="not_run",
            risks=[],
            diff_text="diff --git a/README.md b/README.md",
            worktree_path=str(tmp_path / "worktree"),
            base_sha="base",
            head_sha="head",
        )

    def fail_local_run_success_audit(*args, **kwargs):
        if kwargs.get("operation") == "local_run.start":
            raise RuntimeError("workspace audit failed")
        return original_record_workspace_audit(*args, **kwargs)

    original_record_workspace_audit = local_runner_service.record_workspace_audit
    monkeypatch.setattr(local_runner_service, "RUN_LOCAL_TASK", fake_run_local_task)
    monkeypatch.setattr(
        local_runner_service,
        "record_workspace_audit",
        fail_local_run_success_audit,
    )

    with TestClient(
        create_app(database_url=database_url),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/tasks/task_local/local-runs",
            json={"repo_id": "repo_local"},
            headers=_auth_headers(user_id="developer_user", roles="developer"),
        )

    assert response.status_code == 500
    with Session(build_engine(database_url)) as session:
        local_runs = session.exec(select(LocalTaskRun)).all()
        patch_artifacts = session.exec(select(PatchArtifact)).all()
        task = session.get(Task, "task_local")
        audit_logs = session.exec(
            select(WorkspaceAuditLog).where(
                WorkspaceAuditLog.operation == "local_run.start"
            )
        ).all()

    assert local_runs == []
    assert patch_artifacts == []
    assert task is not None
    assert task.status == "CREATED"
    assert audit_logs == []
