import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import or_
from sqlmodel import Session, select

from ai_company_api.db.session import get_session_dependency
from ai_company_api.models.entities import (
    DeviceSession,
    IdentityAuditEvent,
    Organization,
    OrganizationMember,
    Workspace,
)
from ai_company_api.schemas.api import (
    AccountLinkCreate,
    AccountLinkRead,
    SignOutRead,
    WorkspaceSelectionUpdate,
)
from ai_company_api.services.account_link_recovery import create_account_link
from ai_company_api.services.auth_context import (
    AuthContext,
    USER_SESSION_AUTH_MODE,
    get_auth_context_dependency,
    get_workspace_selection_auth_context_dependency,
)
from ai_company_api.services.browser_request_protection import issue_csrf_token
from ai_company_api.services.identity_login import (
    complete_login_callback,
    reject_malformed_login_callback,
    start_login,
)
from ai_company_api.services.identity_logout import sign_out_current_device
from ai_company_api.services.identity_audit import record_identity_audit_event


router = APIRouter(prefix="/auth", tags=["identity"])
SessionDep = Annotated[Session, Depends(get_session_dependency)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context_dependency)]
SelectionAuthDep = Annotated[
    AuthContext,
    Depends(get_workspace_selection_auth_context_dependency),
]


@router.get("/login")
def get_login(
    request: Request,
    session: SessionDep,
    return_to: str = Query(...),
) -> Response:
    return start_login(
        session,
        provider=request.app.state.customer_identity_provider,
        return_to=return_to,
        allowed_return_destinations=request.app.state.allowed_login_return_destinations,
        public_origin=request.app.state.public_origin,
        transaction_ttl_seconds=request.app.state.login_transaction_ttl_seconds,
        now=request.app.state.identity_clock(),
    )


@router.get("/callback")
def get_callback(
    request: Request,
    session: SessionDep,
    state: str | None = Query(default=None),
    code: str | None = Query(default=None),
) -> Response:
    if not state or not code:
        return reject_malformed_login_callback(session)
    return complete_login_callback(
        session,
        provider=request.app.state.customer_identity_provider,
        request=request,
        state_value=state,
        code=code,
        personal_onboarding_failure_step=(
            request.app.state.personal_onboarding_failure_step
        ),
        now=request.app.state.identity_clock(),
    )


@router.get("/csrf")
def get_csrf_token(
    request: Request,
    session: SessionDep,
    _auth: SelectionAuthDep,
) -> dict[str, str]:
    csrf_token, expires_at = issue_csrf_token(
        request,
        now=request.app.state.identity_clock(),
    )
    return {
        "csrf_token": csrf_token,
        "expires_at": expires_at.isoformat(),
    }


@router.post("/logout", response_model=SignOutRead)
def post_logout(
    request: Request,
    session: SessionDep,
    auth: SelectionAuthDep,
) -> Response:
    device_session = getattr(
        request.state,
        "authenticated_device_session",
        None,
    )
    if (
        auth.auth_mode != USER_SESSION_AUTH_MODE
        or not isinstance(device_session, DeviceSession)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sign_out_requires_user_session",
        )
    return sign_out_current_device(
        session,
        request=request,
        provider=request.app.state.customer_identity_provider,
        device_session=device_session,
        now=request.app.state.identity_clock(),
    )


@router.put("/workspace-selection", status_code=status.HTTP_204_NO_CONTENT)
def put_workspace_selection(
    request: Request,
    data: WorkspaceSelectionUpdate,
    session: SessionDep,
    auth: SelectionAuthDep,
) -> Response:
    device_session = getattr(
        request.state,
        "authenticated_device_session",
        None,
    )
    if (
        auth.auth_mode != USER_SESSION_AUTH_MODE
        or not isinstance(device_session, DeviceSession)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="workspace_selection_requires_user_session",
        )
    selected = session.exec(
        select(OrganizationMember, Workspace, Organization)
        .join(Workspace, Workspace.id == OrganizationMember.workspace_id)
        .join(
            Organization,
            Organization.id == OrganizationMember.organization_id,
        )
        .where(
            OrganizationMember.user_id == auth.user_id,
            OrganizationMember.workspace_id == data.workspace_id,
            OrganizationMember.status == "active",
            Workspace.status == "active",
            Organization.status == "active",
            Workspace.organization_id == Organization.id,
        )
    ).first()
    if selected is None:
        correlation_id = secrets.token_hex(16)
        record_identity_audit_event(
            session,
            event_type="workspace_selection_rejected",
            outcome="failure",
            reason_code="workspace_not_accessible",
            correlation_id=correlation_id,
            user_id=auth.user_id,
            device_session_id=device_session.id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="workspace_not_accessible",
            headers={"X-Correlation-ID": correlation_id},
        )
    _membership, workspace, organization = selected
    device_session.active_workspace_id = workspace.id
    device_session.active_organization_id = organization.id
    device_session.updated_at = request.app.state.identity_clock()
    correlation_id = secrets.token_hex(16)
    session.add(device_session)
    record_identity_audit_event(
        session,
        event_type="workspace_selection_changed",
        outcome="success",
        reason_code="user_selected_workspace",
        correlation_id=correlation_id,
        user_id=auth.user_id,
        device_session_id=device_session.id,
        commit=False,
    )
    session.commit()
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={"X-Correlation-ID": correlation_id},
    )


@router.get("/test/audit-events")
def get_identity_audit_events(
    request: Request,
    session: SessionDep,
    correlation_id: str = Query(...),
) -> list[dict[str, str | None]]:
    if not request.app.state.identity_audit_observer_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    events = session.exec(
        select(IdentityAuditEvent)
        .where(
            or_(
                IdentityAuditEvent.correlation_id == correlation_id,
                IdentityAuditEvent.related_correlation_id == correlation_id,
            )
        )
        .order_by(IdentityAuditEvent.created_at, IdentityAuditEvent.id)
    ).all()
    response: list[dict[str, str | None]] = []
    for event in events:
        item = {
            "event_type": event.event_type,
            "outcome": event.outcome,
            "reason_code": event.reason_code,
            "correlation_id": event.correlation_id,
            "user_id": event.user_id,
            "external_identity_id": event.external_identity_id,
            "device_session_id": event.device_session_id,
        }
        if event.related_correlation_id is not None:
            item["related_correlation_id"] = event.related_correlation_id
        if event.actor_user_id is not None:
            item["actor_user_id"] = event.actor_user_id
        if event.operator_reason is not None:
            item["operator_reason"] = event.operator_reason
        response.append(item)
    return response


@router.post(
    "/operator/account-links",
    response_model=AccountLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def post_account_link(
    request: Request,
    data: AccountLinkCreate,
    session: SessionDep,
    auth: AuthDep,
) -> AccountLinkRead:
    return create_account_link(
        session,
        auth=auth,
        data=data,
        operator_user_ids=request.app.state.identity_operator_user_ids,
    )
