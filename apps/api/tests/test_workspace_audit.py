import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.main import create_app
from ai_company_api.models.entities import (
    GitHubCredential,
    GitHubCredentialStatus,
    ModelCredential,
    ModelCredentialStatus,
    Project,
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
