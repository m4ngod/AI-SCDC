import secrets
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session

from ai_company_api.models.entities import WorkspaceAuditAccessLevel, WorkspaceAuditLog
from ai_company_api.services.auth_context import (
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    USER_SESSION_AUTH_MODE,
    get_current_auth_context,
)
from ai_company_api.services.identity_audit import record_identity_audit_event
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
    workspace_id: str | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
    auth_mode: str | None = None,
    commit: bool = False,
) -> WorkspaceAuditLog:
    context = get_current_auth_context()
    if context is None:
        audit_workspace_id = workspace_id or DEV_WORKSPACE_ID
        audit_organization_id = organization_id or DEV_ORGANIZATION_ID
        audit_user_id = user_id or DEV_USER_ID
        audit_auth_mode = auth_mode or "system"
    else:
        audit_workspace_id = workspace_id or context.workspace_id
        audit_organization_id = organization_id or context.organization_id
        audit_user_id = user_id or context.user_id
        audit_auth_mode = auth_mode or context.auth_mode

    log = WorkspaceAuditLog(
        workspace_id=audit_workspace_id,
        organization_id=audit_organization_id,
        user_id=audit_user_id,
        auth_mode=audit_auth_mode,
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
            session.rollback()
            correlation_id = secrets.token_hex(16)
            record_workspace_audit(
                session,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                access_level=access_level,
                success=False,
                status_code=403,
                error_code="insufficient_workspace_role",
                metadata={"correlation_id": correlation_id},
                commit=False,
            )
            context = get_current_auth_context()
            if (
                context is not None
                and context.auth_mode == USER_SESSION_AUTH_MODE
            ):
                record_identity_audit_event(
                    session,
                    event_type="workspace_authorization_denied",
                    outcome="failure",
                    reason_code="insufficient_workspace_role",
                    correlation_id=correlation_id,
                    user_id=context.user_id,
                    device_session_id=context.device_session_id,
                    commit=False,
                )
            session.commit()
            headers = dict(exc.headers or {})
            headers["X-Correlation-ID"] = correlation_id
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=headers,
            ) from None
        raise


def require_audited_workspace_permission_if_authenticated(
    session: Session,
    permission: str,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None = None,
    access_level: WorkspaceAuditAccessLevel | str,
) -> bool:
    if get_current_auth_context() is None:
        return False
    require_audited_workspace_permission(
        session,
        permission,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        access_level=access_level,
    )
    return True
