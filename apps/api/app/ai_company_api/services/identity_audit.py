from sqlmodel import Session

from ai_company_api.models.entities import IdentityAuditEvent


def record_identity_audit_event(
    session: Session,
    *,
    event_type: str,
    outcome: str,
    reason_code: str,
    correlation_id: str,
    related_correlation_id: str | None = None,
    actor_user_id: str | None = None,
    operator_reason: str | None = None,
    user_id: str | None = None,
    external_identity_id: str | None = None,
    device_session_id: str | None = None,
    commit: bool = True,
) -> IdentityAuditEvent:
    event = IdentityAuditEvent(
        event_type=event_type,
        outcome=outcome,
        reason_code=reason_code,
        correlation_id=correlation_id,
        related_correlation_id=related_correlation_id,
        actor_user_id=actor_user_id,
        operator_reason=operator_reason,
        user_id=user_id,
        external_identity_id=external_identity_id,
        device_session_id=device_session_id,
    )
    session.add(event)
    if commit:
        session.commit()
        session.refresh(event)
    return event
