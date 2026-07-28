import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
from threading import Event
from time import monotonic

from sqlalchemy import or_, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    DeviceSession,
    ExternalIdentity,
    OrganizationMember,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
    CustomerIdentityProviderError,
)
from ai_company_api.services.identity_audit import record_identity_audit_event


IDENTITY_STATUS_SYNCHRONIZATION_INTERVAL = timedelta(minutes=5)
ACTIVE_IDENTITY_STATUS = "active"
INACTIVE_IDENTITY_STATUSES = frozenset({"locked", "disabled", "missing"})
CONTROLLED_RESTORATION_STATUSES = frozenset({"disabled", "missing"})
CONFIRMED_IDENTITY_STATUSES = frozenset(
    {ACTIVE_IDENTITY_STATUS, *INACTIVE_IDENTITY_STATUSES}
)


@dataclass(frozen=True)
class IdentityStatusSynchronizationResult:
    correlation_id: str | None
    human_credentials_revoked: bool


@dataclass(frozen=True)
class _IdentityStatusClaim:
    external_identity_id: str
    user_id: str
    issuer: str
    subject: str
    previous_status: str
    token: str


def synchronize_due_identity_status(
    session: Session,
    *,
    provider: CustomerIdentityProvider,
    user_id: str,
    now: datetime,
    device_session_id: str | None = None,
    correlation_id: str | None = None,
    synchronization_interval: timedelta = (
        IDENTITY_STATUS_SYNCHRONIZATION_INTERVAL
    ),
) -> IdentityStatusSynchronizationResult:
    checked_at = _as_utc(now)
    claims = _claim_due_identity_status_checks(
        session,
        user_id=user_id,
        checked_at=checked_at,
        synchronization_interval=synchronization_interval,
    )
    if not claims:
        return IdentityStatusSynchronizationResult(
            correlation_id=None,
            human_credentials_revoked=False,
        )

    resolved_correlation_id = correlation_id or secrets.token_hex(16)
    human_credentials_revoked = False
    for claim in claims:
        try:
            provider_status = provider.identity_status(
                issuer=claim.issuer,
                subject=claim.subject,
            )
        except CustomerIdentityProviderError:
            _record_failed_claim(
                session,
                claim=claim,
                correlation_id=resolved_correlation_id,
                device_session_id=device_session_id,
                reason_code="provider_unavailable",
            )
            continue

        if provider_status not in CONFIRMED_IDENTITY_STATUSES:
            _record_failed_claim(
                session,
                claim=claim,
                correlation_id=resolved_correlation_id,
                device_session_id=device_session_id,
                reason_code="provider_status_invalid",
            )
            continue

        applied = _apply_confirmed_claim(
            session,
            claim=claim,
            confirmed_status=provider_status,
            confirmed_at=checked_at,
            correlation_id=resolved_correlation_id,
            device_session_id=device_session_id,
        )
        human_credentials_revoked = (
            human_credentials_revoked or applied.human_credentials_revoked
        )

    return IdentityStatusSynchronizationResult(
        correlation_id=resolved_correlation_id,
        human_credentials_revoked=human_credentials_revoked,
    )


def _claim_due_identity_status_checks(
    session: Session,
    *,
    user_id: str,
    checked_at: datetime,
    synchronization_interval: timedelta,
) -> list[_IdentityStatusClaim]:
    due_before = checked_at - synchronization_interval
    token = secrets.token_hex(16)
    claimed_rows = session.execute(
        update(ExternalIdentity)
        .where(
            ExternalIdentity.user_id == user_id,
            ExternalIdentity.status == ACTIVE_IDENTITY_STATUS,
            or_(
                ExternalIdentity.last_status_checked_at.is_(None),
                ExternalIdentity.last_status_checked_at <= due_before,
            ),
        )
        .values(
            last_status_checked_at=checked_at,
            status_check_token=token,
            updated_at=checked_at,
        )
        .returning(
            ExternalIdentity.id,
            ExternalIdentity.user_id,
            ExternalIdentity.issuer,
            ExternalIdentity.subject,
            ExternalIdentity.last_confirmed_status,
        )
        .execution_options(synchronize_session=False)
    ).all()
    claims = [
        _IdentityStatusClaim(
            external_identity_id=row.id,
            user_id=row.user_id,
            issuer=row.issuer,
            subject=row.subject,
            previous_status=_normalized_status(
                row.last_confirmed_status
            ),
            token=token,
        )
        for row in claimed_rows
    ]
    session.commit()
    session.expire_all()
    return claims


def _record_failed_claim(
    session: Session,
    *,
    claim: _IdentityStatusClaim,
    correlation_id: str,
    device_session_id: str | None,
    reason_code: str,
) -> None:
    completed = session.execute(
        update(ExternalIdentity)
        .where(
            ExternalIdentity.id == claim.external_identity_id,
            ExternalIdentity.status_check_token == claim.token,
        )
        .values(status_check_token=None)
        .execution_options(synchronize_session=False)
    )
    if completed.rowcount != 1:
        session.rollback()
        return
    record_identity_audit_event(
        session,
        event_type="identity_status_reconciliation",
        outcome="failure",
        reason_code=reason_code,
        correlation_id=correlation_id,
        user_id=claim.user_id,
        external_identity_id=claim.external_identity_id,
        device_session_id=device_session_id,
        commit=False,
    )
    session.commit()
    session.expire_all()


def _apply_confirmed_claim(
    session: Session,
    *,
    claim: _IdentityStatusClaim,
    confirmed_status: str,
    confirmed_at: datetime,
    correlation_id: str,
    device_session_id: str | None,
) -> IdentityStatusSynchronizationResult:
    local_status = (
        confirmed_status
        if confirmed_status in CONTROLLED_RESTORATION_STATUSES
        else ACTIVE_IDENTITY_STATUS
    )
    applied = session.execute(
        update(ExternalIdentity)
        .where(
            ExternalIdentity.id == claim.external_identity_id,
            ExternalIdentity.status_check_token == claim.token,
        )
        .values(
            status=local_status,
            last_confirmed_status=confirmed_status,
            last_confirmed_at=confirmed_at,
            last_status_checked_at=confirmed_at,
            status_check_token=None,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    if applied.rowcount != 1:
        session.rollback()
        session.expire_all()
        identity = session.get(
            ExternalIdentity,
            claim.external_identity_id,
        )
        return IdentityStatusSynchronizationResult(
            correlation_id=correlation_id,
            human_credentials_revoked=bool(
                identity is not None
                and identity.last_confirmed_status
                in INACTIVE_IDENTITY_STATUSES
            ),
        )

    session.expire_all()
    external_identity = session.get(
        ExternalIdentity,
        claim.external_identity_id,
    )
    if external_identity is None:
        session.rollback()
        return IdentityStatusSynchronizationResult(
            correlation_id=correlation_id,
            human_credentials_revoked=False,
        )
    result = _record_confirmed_status_effects(
        session,
        external_identity=external_identity,
        previous_status=claim.previous_status,
        confirmed_status=confirmed_status,
        confirmed_at=confirmed_at,
        correlation_id=correlation_id,
        device_session_id=device_session_id,
        record_reconciliation=True,
    )
    session.commit()
    session.expire_all()
    return result


async def maintain_identity_status_synchronization(
    *,
    engine: Engine,
    provider: CustomerIdentityProvider,
    clock: Callable[[], datetime],
    poll_seconds: float,
    stop_event: Event,
    on_health_change: Callable[[bool], None],
) -> None:
    scheduled_interval = max(
        timedelta(0),
        IDENTITY_STATUS_SYNCHRONIZATION_INTERVAL
        - timedelta(seconds=poll_seconds),
    )
    next_sweep_at = monotonic() + poll_seconds
    while not stop_event.is_set():
        wait_seconds = max(0.0, next_sweep_at - monotonic())
        if await asyncio.to_thread(stop_event.wait, wait_seconds):
            return
        next_sweep_at += poll_seconds
        try:
            synchronized_identity = await asyncio.to_thread(
                synchronize_active_device_session_users,
                engine=engine,
                provider=provider,
                now=clock(),
                synchronization_interval=scheduled_interval,
            )
        except Exception:
            on_health_change(False)
            continue
        if synchronized_identity:
            on_health_change(True)
        if next_sweep_at < monotonic():
            next_sweep_at = monotonic()


def synchronize_active_device_session_users(
    *,
    engine: Engine,
    provider: CustomerIdentityProvider,
    now: datetime,
    synchronization_interval: timedelta,
) -> bool:
    checked_at = _as_utc(now)
    with Session(engine) as session:
        user_ids = session.exec(
            select(DeviceSession.user_id)
            .where(
                DeviceSession.status == "active",
                DeviceSession.idle_expires_at >= checked_at,
            )
            .distinct()
        ).all()

    synchronized_identity = False
    for user_id in user_ids:
        with Session(engine) as session:
            result = synchronize_due_identity_status(
                session,
                provider=provider,
                user_id=user_id,
                now=checked_at,
                synchronization_interval=synchronization_interval,
            )
            synchronized_identity = (
                synchronized_identity
                or result.correlation_id is not None
            )
    return synchronized_identity


def record_confirmed_identity_status(
    session: Session,
    *,
    external_identity: ExternalIdentity,
    confirmed_status: str,
    now: datetime,
    correlation_id: str,
    device_session_id: str | None = None,
    record_reconciliation: bool,
) -> IdentityStatusSynchronizationResult:
    if confirmed_status not in CONFIRMED_IDENTITY_STATUSES:
        raise ValueError("Identity status is not a confirmed provider status")

    confirmed_at = _as_utc(now)
    previous_status = _normalized_status(
        external_identity.last_confirmed_status
    )
    external_identity.last_confirmed_status = confirmed_status
    external_identity.last_confirmed_at = confirmed_at
    external_identity.last_status_checked_at = confirmed_at
    external_identity.status_check_token = None
    if confirmed_status in CONTROLLED_RESTORATION_STATUSES:
        external_identity.status = confirmed_status
    external_identity.updated_at = confirmed_at
    session.add(external_identity)
    return _record_confirmed_status_effects(
        session,
        external_identity=external_identity,
        previous_status=previous_status,
        confirmed_status=confirmed_status,
        confirmed_at=confirmed_at,
        correlation_id=correlation_id,
        device_session_id=device_session_id,
        record_reconciliation=record_reconciliation,
    )


def _record_confirmed_status_effects(
    session: Session,
    *,
    external_identity: ExternalIdentity,
    previous_status: str,
    confirmed_status: str,
    confirmed_at: datetime,
    correlation_id: str,
    device_session_id: str | None,
    record_reconciliation: bool,
) -> IdentityStatusSynchronizationResult:
    if record_reconciliation:
        record_identity_audit_event(
            session,
            event_type="identity_status_reconciled",
            outcome="success",
            reason_code=f"identity_status_{confirmed_status}",
            correlation_id=correlation_id,
            user_id=external_identity.user_id,
            external_identity_id=external_identity.id,
            device_session_id=device_session_id,
            commit=False,
        )
    if previous_status != confirmed_status:
        record_identity_audit_event(
            session,
            event_type="identity_status_transition_confirmed",
            outcome="success",
            reason_code=f"{previous_status}_to_{confirmed_status}",
            correlation_id=correlation_id,
            user_id=external_identity.user_id,
            external_identity_id=external_identity.id,
            device_session_id=device_session_id,
            commit=False,
        )

    if confirmed_status == ACTIVE_IDENTITY_STATUS:
        return IdentityStatusSynchronizationResult(
            correlation_id=correlation_id,
            human_credentials_revoked=False,
        )

    session.execute(
        update(DeviceSession)
        .where(
            DeviceSession.user_id == external_identity.user_id,
            DeviceSession.status == "active",
        )
        .values(
            status="revoked",
            revoked_at=confirmed_at,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    record_identity_audit_event(
        session,
        event_type="identity_status_user_sessions_revoked",
        outcome="success",
        reason_code=f"identity_status_{confirmed_status}",
        correlation_id=correlation_id,
        user_id=external_identity.user_id,
        external_identity_id=external_identity.id,
        device_session_id=device_session_id,
        commit=False,
    )
    session.execute(
        update(OrganizationMember)
        .where(
            OrganizationMember.user_id == external_identity.user_id,
            OrganizationMember.api_token_hash.is_not(None),
        )
        .values(
            api_token_hash=None,
            updated_at=confirmed_at,
        )
        .execution_options(synchronize_session=False)
    )
    record_identity_audit_event(
        session,
        event_type="identity_status_workspace_api_tokens_revoked",
        outcome="success",
        reason_code=f"identity_status_{confirmed_status}",
        correlation_id=correlation_id,
        user_id=external_identity.user_id,
        external_identity_id=external_identity.id,
        device_session_id=device_session_id,
        commit=False,
    )
    return IdentityStatusSynchronizationResult(
        correlation_id=correlation_id,
        human_credentials_revoked=True,
    )


def _normalized_status(value: str) -> str:
    return value if value in CONFIRMED_IDENTITY_STATUSES else "unknown"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
