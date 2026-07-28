import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import secrets
from threading import Event
from time import monotonic

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, or_, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    AuditRetentionState,
    IdentityAuditEvent,
    SecretAccessAuditLog,
    WorkspaceAuditLog,
)
from ai_company_api.schemas.api import (
    AuditExportRead,
    AuditRetentionCleanupRead,
    AuditRetentionFixtureRead,
    IdentityAuditEventRead,
    SecretAccessAuditEventRead,
    WorkspaceAuditEventRead,
)
from ai_company_api.services.audit_request_context import (
    resolved_correlation_id,
    resolved_request_id,
)
from ai_company_api.services.auth_context import (
    USER_SESSION_AUTH_MODE,
    AuthContext,
)
from ai_company_api.services.identity_audit import (
    record_identity_audit_event,
)
from ai_company_api.services.recent_authentication import (
    require_recent_authentication,
)
from ai_company_api.services.secret_access_audit import (
    record_secret_access,
)
from ai_company_api.services.workspace_audit import (
    record_workspace_audit,
)
from ai_company_api.services.workspace_permissions import (
    allowed_roles_for_permission,
)


IDENTITY_DETAIL_RETENTION = timedelta(days=90)
AUDIT_CORE_RETENTION = timedelta(days=365)
AUDIT_CLEANUP_INTERVAL = timedelta(days=1)
AUDIT_CLEANUP_CLAIM_TIMEOUT = timedelta(hours=1)
AUDIT_RETENTION_STATE_ID = "default"


def export_audit_events(
    session: Session,
    *,
    request: Request,
    auth: AuthContext,
    operator_user_ids: frozenset[str],
    correlation_id: str | None,
    request_id: str | None,
    identity_event_type: str | None,
) -> AuditExportRead:
    operation_correlation_id = resolved_correlation_id()
    _require_audit_operator(
        session,
        request=request,
        auth=auth,
        operator_user_ids=operator_user_ids,
        correlation_id=operation_correlation_id,
        reason_code="audit_export",
    )
    if not any((correlation_id, request_id, identity_event_type)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An audit export filter is required",
        )

    identity_statement = select(IdentityAuditEvent)
    workspace_statement = select(WorkspaceAuditLog)
    secret_statement = select(SecretAccessAuditLog)
    if correlation_id is not None:
        identity_statement = identity_statement.where(
            or_(
                IdentityAuditEvent.correlation_id == correlation_id,
                IdentityAuditEvent.related_correlation_id
                == correlation_id,
            )
        )
        workspace_statement = workspace_statement.where(
            WorkspaceAuditLog.correlation_id == correlation_id
        )
        secret_statement = secret_statement.where(
            SecretAccessAuditLog.correlation_id == correlation_id
        )
    if request_id is not None:
        identity_statement = identity_statement.where(
            IdentityAuditEvent.request_id == request_id
        )
        workspace_statement = workspace_statement.where(
            WorkspaceAuditLog.request_id == request_id
        )
        secret_statement = secret_statement.where(
            SecretAccessAuditLog.request_id == request_id
        )
    if identity_event_type is not None:
        identity_statement = identity_statement.where(
            IdentityAuditEvent.event_type == identity_event_type
        )
        if correlation_id is None and request_id is None:
            workspace_statement = workspace_statement.where(False)
            secret_statement = secret_statement.where(False)

    identity_events = session.exec(
        identity_statement.order_by(
            IdentityAuditEvent.created_at,
            IdentityAuditEvent.id,
        ).limit(500)
    ).all()
    workspace_events = session.exec(
        workspace_statement.order_by(
            WorkspaceAuditLog.created_at,
            WorkspaceAuditLog.id,
        ).limit(500)
    ).all()
    secret_events = session.exec(
        secret_statement.order_by(
            SecretAccessAuditLog.created_at,
            SecretAccessAuditLog.id,
        ).limit(500)
    ).all()
    export = AuditExportRead(
        identity_events=[
            IdentityAuditEventRead(
                event_type=event.event_type,
                outcome=event.outcome,
                reason_code=event.reason_code,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
                user_id=event.user_id,
                client_ip_address=event.client_ip_address,
                user_agent=event.user_agent,
                created_at=event.created_at,
            )
            for event in identity_events
        ],
        workspace_events=[
            WorkspaceAuditEventRead(
                operation=event.operation,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
                workspace_id=event.workspace_id,
                success=event.success,
                status_code=event.status_code,
                metadata=event.metadata_json,
                created_at=event.created_at,
            )
            for event in workspace_events
        ],
        secret_access_events=[
            SecretAccessAuditEventRead(
                secret_kind=event.secret_kind,
                secret_id=event.secret_id,
                operation=event.operation,
                access_reason=event.access_reason,
                request_id=event.request_id,
                correlation_id=event.correlation_id,
                workspace_id=event.workspace_id,
                success=event.success,
                created_at=event.created_at,
            )
            for event in secret_events
        ],
    )
    record_identity_audit_event(
        session,
        event_type="audit_export_read",
        outcome="success",
        reason_code="operator_requested",
        correlation_id=operation_correlation_id,
        actor_user_id=auth.user_id,
        user_id=auth.user_id,
    )
    return export


def run_operator_audit_retention_cleanup(
    session: Session,
    *,
    request: Request,
    auth: AuthContext,
    operator_user_ids: frozenset[str],
    now: datetime,
) -> AuditRetentionCleanupRead:
    correlation_id = resolved_correlation_id()
    _require_audit_operator(
        session,
        request=request,
        auth=auth,
        operator_user_ids=operator_user_ids,
        correlation_id=correlation_id,
        reason_code="audit_retention_cleanup",
    )
    return run_audit_retention_cleanup(
        session,
        now=now,
        correlation_id=correlation_id,
        reason_code="operator_requested",
        actor_user_id=auth.user_id,
    )


def run_audit_retention_cleanup(
    session: Session,
    *,
    now: datetime,
    correlation_id: str,
    reason_code: str,
    actor_user_id: str | None = None,
    commit: bool = True,
) -> AuditRetentionCleanupRead:
    cleanup_at = _as_utc(now)
    core_cutoff = cleanup_at - AUDIT_CORE_RETENTION
    detail_cutoff = cleanup_at - IDENTITY_DETAIL_RETENTION

    identity_deleted = session.execute(
        delete(IdentityAuditEvent).where(
            IdentityAuditEvent.created_at < core_cutoff
        )
    ).rowcount
    workspace_deleted = session.execute(
        delete(WorkspaceAuditLog).where(
            WorkspaceAuditLog.created_at < core_cutoff
        )
    ).rowcount
    secret_deleted = session.execute(
        delete(SecretAccessAuditLog).where(
            SecretAccessAuditLog.created_at < core_cutoff
        )
    ).rowcount
    details_removed = session.execute(
        update(IdentityAuditEvent)
        .where(
            IdentityAuditEvent.created_at < detail_cutoff,
            or_(
                IdentityAuditEvent.client_ip_address.is_not(None),
                IdentityAuditEvent.user_agent.is_not(None),
            ),
        )
        .values(
            client_ip_address=None,
            user_agent=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    record_identity_audit_event(
        session,
        event_type="audit_retention_cleanup",
        outcome="success",
        reason_code=reason_code,
        correlation_id=correlation_id,
        actor_user_id=actor_user_id,
        user_id=actor_user_id,
        created_at=cleanup_at,
        commit=False,
    )
    if commit:
        session.commit()
    return AuditRetentionCleanupRead(
        status="completed",
        correlation_id=correlation_id,
        identity_details_removed=details_removed,
        identity_events_deleted=identity_deleted,
        workspace_events_deleted=workspace_deleted,
        secret_access_events_deleted=secret_deleted,
    )


def create_audit_retention_fixture(
    session: Session,
    *,
    auth: AuthContext,
    now: datetime,
    age_days: int,
) -> AuditRetentionFixtureRead:
    created_at = _as_utc(now) - timedelta(days=age_days)
    correlation_id = f"fixture_correlation_{secrets.token_hex(16)}"
    request_id = f"fixture_request_{secrets.token_hex(16)}"
    suffix = secrets.token_hex(8)
    record_identity_audit_event(
        session,
        event_type="audit_retention_fixture",
        outcome="success",
        reason_code="test_fixture",
        request_id=request_id,
        correlation_id=correlation_id,
        user_id=auth.user_id,
        client_ip_address="203.0.113.10",
        user_agent="RetentionFixture/1.0",
        created_at=created_at,
        commit=False,
    )
    record_workspace_audit(
        session,
        operation="audit_retention.fixture",
        resource_type="audit_fixture",
        resource_id=f"audit_fixture_{suffix}",
        access_level="high_value_write",
        success=True,
        status_code=status.HTTP_201_CREATED,
        request_id=request_id,
        correlation_id=correlation_id,
        created_at=created_at,
        commit=False,
    )
    record_secret_access(
        session,
        secret_kind="audit_fixture",
        secret_id=f"secret_fixture_{suffix}",
        operation="open",
        access_reason="audit_retention_fixture",
        request_id=request_id,
        correlation_id=correlation_id,
        created_at=created_at,
        commit=False,
    )
    session.commit()
    return AuditRetentionFixtureRead(correlation_id=correlation_id)


async def maintain_audit_retention(
    *,
    engine: Engine,
    clock: Callable[[], datetime],
    poll_seconds: float,
    stop_event: Event,
    on_health_change: Callable[[bool], None],
    failure_step: str | None = None,
) -> None:
    next_sweep_at = monotonic() + poll_seconds
    while not stop_event.is_set():
        wait_seconds = max(0.0, next_sweep_at - monotonic())
        if await asyncio.to_thread(stop_event.wait, wait_seconds):
            return
        next_sweep_at += poll_seconds
        try:
            ran = await asyncio.to_thread(
                run_scheduled_audit_retention_if_due,
                engine=engine,
                now=clock(),
                failure_step=failure_step,
            )
        except Exception:
            on_health_change(False)
            continue
        if ran:
            on_health_change(True)
        if next_sweep_at < monotonic():
            next_sweep_at = monotonic()


def run_scheduled_audit_retention_if_due(
    *,
    engine: Engine,
    now: datetime,
    failure_step: str | None = None,
) -> bool:
    cleanup_at = _as_utc(now)
    _ensure_retention_state(engine)
    claim_token = secrets.token_hex(16)
    with Session(engine) as session:
        claimed = session.execute(
            update(AuditRetentionState)
            .where(
                AuditRetentionState.id == AUDIT_RETENTION_STATE_ID,
                or_(
                    AuditRetentionState.last_completed_at.is_(None),
                    AuditRetentionState.last_completed_at
                    <= cleanup_at - AUDIT_CLEANUP_INTERVAL,
                ),
                or_(
                    AuditRetentionState.claim_token.is_(None),
                    AuditRetentionState.claimed_at
                    <= cleanup_at - AUDIT_CLEANUP_CLAIM_TIMEOUT,
                ),
            )
            .values(
                claim_token=claim_token,
                claimed_at=cleanup_at,
            )
            .returning(AuditRetentionState.id)
            .execution_options(synchronize_session=False)
        ).first()
        session.commit()
    if claimed is None:
        return False

    correlation_id = f"retention_{secrets.token_hex(16)}"
    try:
        with Session(engine) as session:
            if failure_step == "before_cleanup":
                raise RuntimeError(
                    "Injected audit retention cleanup failure"
                )
            run_audit_retention_cleanup(
                session,
                now=cleanup_at,
                correlation_id=correlation_id,
                reason_code="scheduled_daily_cleanup",
                commit=False,
            )
            completed = session.execute(
                update(AuditRetentionState)
                .where(
                    AuditRetentionState.id
                    == AUDIT_RETENTION_STATE_ID,
                    AuditRetentionState.claim_token == claim_token,
                )
                .values(
                    last_completed_at=cleanup_at,
                    claim_token=None,
                    claimed_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if completed.rowcount != 1:
                session.rollback()
                return False
            session.commit()
    except Exception:
        _record_scheduled_cleanup_failure(
            engine,
            occurred_at=cleanup_at,
            correlation_id=correlation_id,
        )
        raise
    return True


def _ensure_retention_state(engine: Engine) -> None:
    with Session(engine) as session:
        if session.get(AuditRetentionState, AUDIT_RETENTION_STATE_ID):
            return
        session.add(AuditRetentionState(id=AUDIT_RETENTION_STATE_ID))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()


def _record_scheduled_cleanup_failure(
    engine: Engine,
    *,
    occurred_at: datetime,
    correlation_id: str,
) -> None:
    try:
        with Session(engine) as session:
            record_identity_audit_event(
                session,
                event_type="audit_retention_cleanup",
                outcome="failure",
                reason_code="scheduled_daily_cleanup_failed",
                correlation_id=correlation_id,
                created_at=occurred_at,
            )
    except Exception:
        # The maintainer still reports degraded health when the audit store
        # itself is unavailable. A later run can take over the stale claim.
        return


def _require_audit_operator(
    session: Session,
    *,
    request: Request,
    auth: AuthContext,
    operator_user_ids: frozenset[str],
    correlation_id: str,
    reason_code: str,
) -> None:
    if (
        auth.auth_mode != USER_SESSION_AUTH_MODE
        or auth.user_id not in operator_user_ids
        or not auth.has_any_role(
            allowed_roles_for_permission("operator.write")
        )
    ):
        record_identity_audit_event(
            session,
            event_type=f"{reason_code}_rejected",
            outcome="failure",
            reason_code="operator_not_authorized",
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            user_id=auth.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Audit operation is not authorized",
            headers={"X-Correlation-ID": correlation_id},
        )
    require_recent_authentication(
        request,
        session,
        reason_code=reason_code,
        correlation_id=correlation_id,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
