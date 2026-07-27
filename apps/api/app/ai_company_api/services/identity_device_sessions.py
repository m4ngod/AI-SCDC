from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from ai_company_api.models.entities import DeviceSession
from ai_company_api.schemas.api import DeviceSessionRead


UNKNOWN_DEVICE_DESCRIPTION = "Unknown browser on Unknown device"


class DeviceSessionRevocationOperationError(RuntimeError):
    pass


def coarse_device_description(user_agent: str | None) -> str:
    if not user_agent:
        return UNKNOWN_DEVICE_DESCRIPTION
    normalized = user_agent.casefold()
    if "edg/" in normalized:
        browser = "Edge"
    elif "firefox/" in normalized or "fxios/" in normalized:
        browser = "Firefox"
    elif "chrome/" in normalized or "crios/" in normalized:
        browser = "Chrome"
    elif "safari/" in normalized:
        browser = "Safari"
    else:
        browser = "Unknown browser"

    if "android" in normalized:
        device = "Android"
    elif "iphone" in normalized or "ipad" in normalized:
        device = "iOS"
    elif "windows" in normalized:
        device = "Windows"
    elif "macintosh" in normalized or "mac os x" in normalized:
        device = "macOS"
    elif "linux" in normalized:
        device = "Linux"
    else:
        device = "Unknown device"
    return f"{browser} on {device}"


def active_device_sessions(
    session: Session,
    *,
    user_id: str,
    current_device_session_id: str,
    now: datetime,
) -> list[DeviceSessionRead]:
    device_sessions = session.exec(
        select(DeviceSession)
        .where(
            DeviceSession.user_id == user_id,
            DeviceSession.status == "active",
            DeviceSession.idle_expires_at > now,
        )
        .order_by(DeviceSession.created_at.desc(), DeviceSession.id.desc())
    ).all()
    return [
        DeviceSessionRead(
            id=device_session.id,
            device_description=device_session.device_description,
            created_at=device_session.created_at,
            last_seen_at=device_session.last_seen_at,
            status=device_session.status,
            is_current=device_session.id == current_device_session_id,
        )
        for device_session in device_sessions
    ]


def revoke_active_device_session(
    session: Session,
    *,
    user_id: str,
    device_session_id: str,
    now: datetime,
) -> bool:
    result = session.execute(
        update(DeviceSession)
        .where(
            DeviceSession.id == device_session_id,
            DeviceSession.user_id == user_id,
            DeviceSession.status == "active",
            DeviceSession.idle_expires_at > now,
        )
        .values(
            status="revoked",
            revoked_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def revoke_other_active_device_sessions(
    session: Session,
    *,
    user_id: str,
    current_device_session_id: str,
    now: datetime,
    failure_mode: str | None = None,
) -> int:
    result = session.execute(
        update(DeviceSession)
        .where(
            DeviceSession.user_id == user_id,
            DeviceSession.id != current_device_session_id,
            DeviceSession.status == "active",
            DeviceSession.idle_expires_at > now,
        )
        .values(
            status="revoked",
            revoked_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if failure_mode == "database":
        raise SQLAlchemyError(
            "injected_device_session_revocation_failure"
        )
    if failure_mode == "operation":
        raise DeviceSessionRevocationOperationError(
            "injected_device_session_revocation_failure"
        )
    return result.rowcount


def is_idle_expired(
    device_session: DeviceSession,
    *,
    now: datetime,
) -> bool:
    return _as_utc(device_session.idle_expires_at) <= _as_utc(now)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
