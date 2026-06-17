import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import Project, WorkspaceAuditLog, WorkspaceRole
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
