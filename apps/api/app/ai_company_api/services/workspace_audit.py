from typing import Any

from fastapi import HTTPException
from sqlalchemy.engine import Connection
from sqlmodel import Session

from ai_company_api.models.entities import WorkspaceAuditAccessLevel, WorkspaceAuditLog
from ai_company_api.services.auth_context import (
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    get_current_auth_context,
)
from ai_company_api.services.workspace_permissions import require_workspace_permission


SENSITIVE_AUDIT_KEY_PARTS = (
    "secret",
    "token",
    "receipt",
    "content",
    "message",
    "stdout",
    "stderr",
    "payload",
    "diff",
    "download_url",
    "presigned",
    "encrypted",
    "authorization",
    "clone_token",
    "queue_receipt",
    "callback",
)
MAX_AUDIT_STRING_LENGTH = 256


def redact_audit_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in SENSITIVE_AUDIT_KEY_PARTS):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_audit_metadata(item)
        return redacted
    if isinstance(value, list):
        return [redact_audit_metadata(item) for item in value]
    if isinstance(value, str) and len(value) > MAX_AUDIT_STRING_LENGTH:
        return f"{value[:MAX_AUDIT_STRING_LENGTH]}..."
    return value


def record_workspace_audit(
    session: Session,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    access_level: WorkspaceAuditAccessLevel | str,
    success: bool,
    status_code: int,
    error_code: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> WorkspaceAuditLog:
    context = get_current_auth_context()
    if context is None:
        workspace_id = DEV_WORKSPACE_ID
        organization_id = DEV_ORGANIZATION_ID
        user_id = DEV_USER_ID
        auth_mode = "system"
    else:
        workspace_id = context.workspace_id
        organization_id = context.organization_id
        user_id = context.user_id
        auth_mode = context.auth_mode

    log = WorkspaceAuditLog(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        auth_mode=auth_mode,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        access_level=WorkspaceAuditAccessLevel(access_level),
        success=success,
        status_code=status_code,
        error_code=error_code,
        metadata_json=redact_audit_metadata(metadata or {}),
    )
    session.add(log)
    if commit:
        session.commit()
        session.refresh(log)
    else:
        session.flush()
    return log


def require_audited_workspace_permission(
    session: Session,
    permission: str,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    access_level: WorkspaceAuditAccessLevel | str,
) -> None:
    try:
        require_workspace_permission(permission)
    except HTTPException as exc:
        if exc.status_code == 403:
            with Session(_isolated_audit_bind(session)) as audit_session:
                record_workspace_audit(
                    audit_session,
                    operation=operation,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    access_level=access_level,
                    success=False,
                    status_code=403,
                    error_code="insufficient_workspace_role",
                    commit=True,
                )
        raise


def _isolated_audit_bind(session: Session):
    bind = session.get_bind()
    if isinstance(bind, Connection):
        return bind.engine
    return bind
