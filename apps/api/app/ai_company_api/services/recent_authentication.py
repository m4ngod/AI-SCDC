from datetime import datetime, timedelta, timezone
import secrets

from fastapi import HTTPException, Request, status
from sqlmodel import Session

from ai_company_api.models.entities import DeviceSession
from ai_company_api.services.auth_context import (
    USER_SESSION_AUTH_MODE,
    get_current_auth_context,
)
from ai_company_api.services.identity_audit import record_identity_audit_event


RECENT_AUTHENTICATION_TTL_SECONDS = 15 * 60


def require_recent_authentication(
    request: Request,
    session: Session,
    *,
    reason_code: str = "sensitive_credential_change",
    correlation_id: str | None = None,
) -> None:
    auth = get_current_auth_context()
    if auth is None or auth.auth_mode != USER_SESSION_AUTH_MODE:
        return
    device_session = getattr(
        request.state,
        "authenticated_device_session",
        None,
    )
    now = _as_utc(request.app.state.identity_clock())
    if (
        isinstance(device_session, DeviceSession)
        and device_session.recent_authenticated_at is not None
        and _as_utc(device_session.recent_authenticated_at)
        + timedelta(seconds=RECENT_AUTHENTICATION_TTL_SECONDS)
        > now
    ):
        return

    resolved_correlation_id = correlation_id or secrets.token_hex(16)
    record_identity_audit_event(
        session,
        event_type="recent_authentication_required",
        outcome="failure",
        reason_code=reason_code,
        correlation_id=resolved_correlation_id,
        user_id=auth.user_id,
        device_session_id=(
            device_session.id
            if isinstance(device_session, DeviceSession)
            else None
        ),
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="reauthentication_required",
        headers={"X-Correlation-ID": resolved_correlation_id},
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
