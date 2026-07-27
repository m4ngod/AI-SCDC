from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import secrets

from sqlmodel import Session

from ai_company_api.models.entities import DeviceSession
from ai_company_api.services.identity_audit import record_identity_audit_event


USER_SESSION_COOKIE = "__Host-ai_scdc_session"
USER_SESSION_IDLE_SECONDS = 30 * 24 * 60 * 60
USER_SESSION_ROTATION_SECONDS = 24 * 60 * 60
USER_SESSION_PREVIOUS_SECRET_SECONDS = 2 * 60


class UserSessionCredentialRejected(RuntimeError):
    def __init__(
        self,
        *,
        reason_code: str,
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.correlation_id = correlation_id


@dataclass(frozen=True)
class ResolvedUserSessionCredential:
    device_session: DeviceSession
    presented_secret_hash: str
    uses_current_secret: bool


def resolve_user_session_credential(
    session: Session,
    *,
    cookie_value: str,
    now: datetime,
) -> ResolvedUserSessionCredential:
    session_id, separator, session_secret = cookie_value.partition(".")
    if separator == "" or session_id == "" or session_secret == "":
        _reject_credential("invalid_session_credential")

    device_session = session.get(DeviceSession, session_id)
    if device_session is None:
        _reject_credential("invalid_session_credential")
    presented_secret_hash = hash_session_secret(session_secret)
    uses_current_secret = hmac.compare_digest(
        device_session.secret_hash,
        presented_secret_hash,
    )
    uses_previous_secret = bool(
        device_session.previous_secret_hash is not None
        and hmac.compare_digest(
            device_session.previous_secret_hash,
            presented_secret_hash,
        )
    )
    if device_session.status == "revoked":
        if not uses_current_secret and not uses_previous_secret:
            _reject_credential("invalid_session_credential")
        correlation_id = secrets.token_hex(16)
        record_identity_audit_event(
            session,
            event_type="session_credential_replay",
            outcome="failure",
            reason_code="revoked_session_reuse",
            correlation_id=correlation_id,
            user_id=device_session.user_id,
            device_session_id=device_session.id,
        )
        _reject_credential(
            "invalid_session_credential",
            correlation_id=correlation_id,
        )
    if device_session.status != "active":
        _reject_credential("invalid_session_credential")
    if _as_utc(device_session.idle_expires_at) <= now:
        correlation_id = secrets.token_hex(16)
        device_session.status = "expired"
        device_session.updated_at = now
        session.add(device_session)
        record_identity_audit_event(
            session,
            event_type="session_expired",
            outcome="failure",
            reason_code="idle_timeout",
            correlation_id=correlation_id,
            user_id=device_session.user_id,
            device_session_id=device_session.id,
        )
        _reject_credential(
            "idle_timeout",
            correlation_id=correlation_id,
        )

    if (
        uses_previous_secret
        and (
            device_session.previous_secret_valid_until is None
            or _as_utc(device_session.previous_secret_valid_until) < now
        )
    ):
        correlation_id = secrets.token_hex(16)
        device_session.status = "revoked"
        device_session.revoked_at = now
        device_session.updated_at = now
        session.add(device_session)
        record_identity_audit_event(
            session,
            event_type="session_credential_replay",
            outcome="failure",
            reason_code="suspected_replay",
            correlation_id=correlation_id,
            user_id=device_session.user_id,
            device_session_id=device_session.id,
        )
        _reject_credential(
            "suspected_replay",
            correlation_id=correlation_id,
        )
    if not uses_current_secret and not uses_previous_secret:
        _reject_credential("invalid_session_credential")
    return ResolvedUserSessionCredential(
        device_session=device_session,
        presented_secret_hash=presented_secret_hash,
        uses_current_secret=uses_current_secret,
    )


def hash_session_secret(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _reject_credential(
    reason_code: str,
    *,
    correlation_id: str | None = None,
) -> None:
    raise UserSessionCredentialRejected(
        reason_code=reason_code,
        correlation_id=correlation_id,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
