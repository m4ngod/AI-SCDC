from datetime import datetime

from sqlmodel import Session

from ai_company_api.models.entities import IdentityAuditEvent
from ai_company_api.services.audit_request_context import (
    current_audit_request_context,
    resolved_audit_time,
    resolved_request_id,
    safe_user_agent,
)


def record_identity_audit_event(
    session: Session,
    *,
    event_type: str,
    outcome: str,
    reason_code: str,
    correlation_id: str,
    request_id: str | None = None,
    related_correlation_id: str | None = None,
    actor_user_id: str | None = None,
    operator_reason: str | None = None,
    user_id: str | None = None,
    external_identity_id: str | None = None,
    device_session_id: str | None = None,
    client_ip_address: str | None = None,
    user_agent: str | None = None,
    created_at: datetime | None = None,
    commit: bool = True,
) -> IdentityAuditEvent:
    request_context = current_audit_request_context()
    event = IdentityAuditEvent(
        event_type=event_type,
        outcome=outcome,
        reason_code=reason_code,
        request_id=resolved_request_id(request_id),
        correlation_id=correlation_id,
        related_correlation_id=related_correlation_id,
        actor_user_id=actor_user_id,
        operator_reason=operator_reason,
        user_id=user_id,
        external_identity_id=external_identity_id,
        device_session_id=device_session_id,
        client_ip_address=(
            client_ip_address
            if client_ip_address is not None
            else (
                request_context.client_ip_address
                if request_context is not None
                else None
            )
        ),
        user_agent=safe_user_agent(
            user_agent
            if user_agent is not None
            else (
                request_context.user_agent
                if request_context is not None
                else None
            )
        ),
        created_at=resolved_audit_time(created_at),
    )
    session.add(event)
    if commit:
        session.commit()
        session.refresh(event)
    return event
