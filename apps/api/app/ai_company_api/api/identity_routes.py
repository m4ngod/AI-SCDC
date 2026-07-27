from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlmodel import Session, select

from ai_company_api.db.session import get_session_dependency
from ai_company_api.models.entities import IdentityAuditEvent
from ai_company_api.services.identity_login import (
    complete_login_callback,
    reject_malformed_login_callback,
    start_login,
)


router = APIRouter(prefix="/auth", tags=["identity"])
SessionDep = Annotated[Session, Depends(get_session_dependency)]


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
        .where(IdentityAuditEvent.correlation_id == correlation_id)
        .order_by(IdentityAuditEvent.created_at, IdentityAuditEvent.id)
    ).all()
    return [
        {
            "event_type": event.event_type,
            "outcome": event.outcome,
            "reason_code": event.reason_code,
            "correlation_id": event.correlation_id,
            "user_id": event.user_id,
            "external_identity_id": event.external_identity_id,
            "device_session_id": event.device_session_id,
        }
        for event in events
    ]
