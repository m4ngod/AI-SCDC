from sqlmodel import Session

from ai_company_api.models.entities import SecretAccessAuditLog, Workspace
from ai_company_api.services.auth_context import (
    current_organization_id,
    current_user_id,
    current_workspace_id,
    get_current_auth_context,
)
from ai_company_api.services.secret_vault import SecretVault, get_secret_vault


def record_secret_access(
    session: Session,
    *,
    secret_kind: str,
    secret_id: str,
    access_reason: str,
    workspace_id: str | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
    auth_mode: str | None = None,
    operation: str = "open",
    success: bool = True,
    commit: bool = False,
) -> SecretAccessAuditLog:
    resolved_workspace_id = workspace_id or current_workspace_id()
    context = get_current_auth_context()
    log = SecretAccessAuditLog(
        workspace_id=resolved_workspace_id,
        organization_id=_resolved_organization_id(
            session,
            workspace_id=resolved_workspace_id,
            organization_id=organization_id,
        ),
        user_id=user_id or current_user_id(),
        auth_mode=auth_mode or (context.auth_mode if context is not None else "system"),
        secret_kind=secret_kind,
        secret_id=secret_id,
        operation=operation,
        access_reason=access_reason,
        success=success,
    )
    session.add(log)
    if commit:
        session.commit()
        session.refresh(log)
    return log


def open_secret(
    session: Session,
    encrypted_secret: str,
    *,
    secret_kind: str,
    secret_id: str,
    access_reason: str,
    workspace_id: str | None = None,
    organization_id: str | None = None,
    user_id: str | None = None,
    auth_mode: str | None = None,
    vault: SecretVault | None = None,
    commit_audit: bool = False,
) -> str:
    resolved_vault = vault or get_secret_vault()
    try:
        secret = resolved_vault.open(encrypted_secret)
    except Exception:
        record_secret_access(
            session,
            secret_kind=secret_kind,
            secret_id=secret_id,
            access_reason=access_reason,
            workspace_id=workspace_id,
            organization_id=organization_id,
            user_id=user_id,
            auth_mode=auth_mode,
            success=False,
            commit=commit_audit,
        )
        raise

    record_secret_access(
        session,
        secret_kind=secret_kind,
        secret_id=secret_id,
        access_reason=access_reason,
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        auth_mode=auth_mode,
        success=True,
        commit=commit_audit,
    )
    return secret


def _resolved_organization_id(
    session: Session,
    *,
    workspace_id: str,
    organization_id: str | None,
) -> str:
    if organization_id is not None:
        return organization_id
    context = get_current_auth_context()
    if context is not None and context.workspace_id == workspace_id:
        return context.organization_id
    workspace = session.get(Workspace, workspace_id)
    if workspace is not None:
        return workspace.organization_id
    return current_organization_id()
