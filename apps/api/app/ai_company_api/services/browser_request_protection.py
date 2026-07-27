from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
from typing import NoReturn

from fastapi import HTTPException, Request, status
from sqlmodel import Session

from ai_company_api.models.entities import DeviceSession
from ai_company_api.services.identity_audit import record_identity_audit_event


CSRF_TOKEN_TTL_SECONDS = 60 * 60
UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def issue_csrf_token(
    request: Request,
    *,
    now: datetime,
) -> tuple[str, datetime]:
    device_session = _authenticated_device_session(request)
    expires_at = _as_utc(now) + timedelta(seconds=CSRF_TOKEN_TTL_SECONDS)
    expires_epoch = int(expires_at.timestamp())
    nonce = secrets.token_urlsafe(16)
    payload = _csrf_payload(
        device_session.id,
        expires_epoch=expires_epoch,
        nonce=nonce,
    )
    signature = _csrf_signature(
        device_session.secret_hash,
        payload=payload,
    )
    return f"v1.{expires_epoch}.{nonce}.{signature}", expires_at


def enforce_cookie_request_protection(
    request: Request,
    session: Session,
    *,
    now: datetime,
) -> None:
    if request.method.upper() not in UNSAFE_HTTP_METHODS:
        return

    device_session = _authenticated_device_session(request)
    allowed_origin = request.app.state.public_origin
    presented_origin = request.headers.get("origin")
    if presented_origin is None:
        _reject_browser_request(
            session,
            event_type="origin_rejected",
            reason_code="origin_required",
            device_session=device_session,
        )
    if not hmac.compare_digest(presented_origin, allowed_origin):
        _reject_browser_request(
            session,
            event_type="origin_rejected",
            reason_code="origin_mismatch",
            device_session=device_session,
        )

    presented_token = request.headers.get("x-csrf-token")
    if presented_token is None or presented_token == "":
        _reject_browser_request(
            session,
            event_type="csrf_rejected",
            reason_code="csrf_token_required",
            device_session=device_session,
        )
    token_parts = presented_token.split(".")
    if len(token_parts) != 4 or token_parts[0] != "v1":
        _reject_browser_request(
            session,
            event_type="csrf_rejected",
            reason_code="csrf_token_malformed",
            device_session=device_session,
        )
    try:
        expires_epoch = int(token_parts[1])
    except ValueError:
        _reject_browser_request(
            session,
            event_type="csrf_rejected",
            reason_code="csrf_token_malformed",
            device_session=device_session,
        )
    nonce = token_parts[2]
    presented_signature = token_parts[3]
    if nonce == "" or presented_signature == "":
        _reject_browser_request(
            session,
            event_type="csrf_rejected",
            reason_code="csrf_token_malformed",
            device_session=device_session,
        )
    payload = _csrf_payload(
        device_session.id,
        expires_epoch=expires_epoch,
        nonce=nonce,
    )
    expected_signature = _csrf_signature(
        device_session.secret_hash,
        payload=payload,
    )
    current_signature_matches = hmac.compare_digest(
        presented_signature,
        expected_signature,
    )
    previous_signature_matches = bool(
        device_session.previous_secret_hash is not None
        and device_session.previous_secret_valid_until is not None
        and _as_utc(device_session.previous_secret_valid_until) >= _as_utc(now)
        and hmac.compare_digest(
            presented_signature,
            _csrf_signature(
                device_session.previous_secret_hash,
                payload=payload,
            ),
        )
    )
    if not current_signature_matches and not previous_signature_matches:
        _reject_browser_request(
            session,
            event_type="csrf_rejected",
            reason_code="csrf_token_mismatch",
            device_session=device_session,
        )
    if expires_epoch <= int(_as_utc(now).timestamp()):
        _reject_browser_request(
            session,
            event_type="csrf_rejected",
            reason_code="csrf_token_expired",
            device_session=device_session,
        )


def _authenticated_device_session(request: Request) -> DeviceSession:
    device_session = getattr(
        request.state,
        "authenticated_device_session",
        None,
    )
    if not isinstance(device_session, DeviceSession):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User Session is not valid",
        )
    return device_session


def _reject_browser_request(
    session: Session,
    *,
    event_type: str,
    reason_code: str,
    device_session: DeviceSession,
) -> NoReturn:
    correlation_id = secrets.token_hex(16)
    record_identity_audit_event(
        session,
        event_type=event_type,
        outcome="failure",
        reason_code=reason_code,
        correlation_id=correlation_id,
        user_id=device_session.user_id,
        device_session_id=device_session.id,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=reason_code,
        headers={"X-Correlation-ID": correlation_id},
    )


def _csrf_payload(
    device_session_id: str,
    *,
    expires_epoch: int,
    nonce: str,
) -> bytes:
    return f"{device_session_id}:{expires_epoch}:{nonce}".encode("utf-8")


def _csrf_signature(secret_hash: str, *, payload: bytes) -> str:
    return hmac.new(
        secret_hash.encode("ascii"),
        payload,
        sha256,
    ).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
