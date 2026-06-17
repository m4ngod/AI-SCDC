from sqlmodel import Session, select

from ai_company_api.db.session import build_engine, init_db
from ai_company_api.models.entities import WorkspaceAuditLog
from ai_company_api.services.workspace_audit import (
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
    metadata = {
        "safe_id": "cloud_run_1",
        "token": "ghp_secret",
        "queue_receipt": "receipt_secret",
        "download_url": "https://example.test/private",
        "nested": {"stdout": "secret output", "status": "ok"},
        "messages": ["secret log line"],
    }

    assert redact_audit_metadata(metadata) == {
        "safe_id": "cloud_run_1",
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
