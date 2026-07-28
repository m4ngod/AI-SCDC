from datetime import datetime, timezone
import secrets

from fastapi import HTTPException, Request, status
from sqlalchemy import update
from sqlmodel import Session, select

from ai_company_api.models.entities import ExternalIdentity
from ai_company_api.schemas.api import (
    ExternalIdentityRestore,
    ExternalIdentityRestoreRead,
)
from ai_company_api.services.auth_context import (
    USER_SESSION_AUTH_MODE,
    AuthContext,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
    CustomerIdentityProviderError,
)
from ai_company_api.services.identity_audit import record_identity_audit_event
from ai_company_api.services.identity_status_synchronization import (
    ACTIVE_IDENTITY_STATUS,
    CONTROLLED_RESTORATION_STATUSES,
)
from ai_company_api.services.recent_authentication import (
    require_recent_authentication,
)
from ai_company_api.services.workspace_permissions import (
    allowed_roles_for_permission,
)


EXTERNAL_IDENTITY_RESTORATION_REASONS = frozenset(
    {"provider_identity_reactivated"}
)


def restore_external_identity(
    session: Session,
    *,
    request: Request,
    auth: AuthContext,
    data: ExternalIdentityRestore,
    operator_user_ids: frozenset[str],
    provider: CustomerIdentityProvider,
    now: datetime,
) -> ExternalIdentityRestoreRead:
    correlation_id = secrets.token_hex(16)
    operator_reason = _stripped_text(data.reason)
    if (
        auth.auth_mode != USER_SESSION_AUTH_MODE
        or auth.user_id not in operator_user_ids
        or not auth.has_any_role(
            allowed_roles_for_permission("operator.write")
        )
    ):
        _reject_restoration(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="operator_not_authorized",
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External Identity restoration is not authorized",
        )

    issuer = _stripped_text(data.issuer)
    subject = _stripped_text(data.subject)
    if (
        not issuer
        or not subject
        or operator_reason not in EXTERNAL_IDENTITY_RESTORATION_REASONS
    ):
        _reject_restoration(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="invalid_request",
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="External Identity restoration request is invalid",
        )

    require_recent_authentication(
        request,
        session,
        reason_code="external_identity_restoration",
        correlation_id=correlation_id,
    )
    external_identity = session.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == issuer,
            ExternalIdentity.subject == subject,
        )
    ).first()
    if (
        external_identity is None
        or external_identity.status
        not in CONTROLLED_RESTORATION_STATUSES
    ):
        _reject_restoration(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="restoration_not_required",
            status_code=status.HTTP_409_CONFLICT,
            detail="External Identity is not awaiting controlled restoration",
        )

    external_identity_id = external_identity.id
    external_identity_user_id = external_identity.user_id
    external_identity_issuer = external_identity.issuer
    external_identity_subject = external_identity.subject
    session.commit()
    session.expire_all()
    try:
        provider_status = provider.identity_status(
            issuer=external_identity_issuer,
            subject=external_identity_subject,
        )
    except CustomerIdentityProviderError:
        _reject_restoration(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="provider_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="External Identity status is temporarily unavailable",
        )
    if provider_status != ACTIVE_IDENTITY_STATUS:
        _reject_restoration(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="provider_identity_not_active",
            status_code=status.HTTP_409_CONFLICT,
            detail="External Identity is not active at the provider",
        )

    restored_at = _as_utc(now)
    restored = session.execute(
        update(ExternalIdentity)
        .where(
            ExternalIdentity.id == external_identity_id,
            ExternalIdentity.status.in_(
                CONTROLLED_RESTORATION_STATUSES
            ),
        )
        .values(
            status=ACTIVE_IDENTITY_STATUS,
            last_confirmed_status=ACTIVE_IDENTITY_STATUS,
            last_confirmed_at=restored_at,
            last_status_checked_at=restored_at,
            status_check_token=None,
            updated_at=restored_at,
        )
        .execution_options(synchronize_session=False)
    )
    if restored.rowcount != 1:
        session.rollback()
        _reject_restoration(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="restoration_conflict",
            status_code=status.HTTP_409_CONFLICT,
            detail="External Identity restoration state changed",
        )

    record_identity_audit_event(
        session,
        event_type="external_identity_restored",
        outcome="success",
        reason_code=operator_reason,
        correlation_id=correlation_id,
        actor_user_id=auth.user_id,
        operator_reason=operator_reason,
        user_id=external_identity_user_id,
        external_identity_id=external_identity_id,
        commit=False,
    )
    session.commit()
    return ExternalIdentityRestoreRead(
        status="restored",
        correlation_id=correlation_id,
        user_id=external_identity_user_id,
        external_identity_id=external_identity_id,
    )


def _reject_restoration(
    session: Session,
    *,
    correlation_id: str,
    actor_user_id: str,
    operator_reason: str | None,
    reason_code: str,
    status_code: int,
    detail: str,
) -> None:
    record_identity_audit_event(
        session,
        event_type="external_identity_restoration_rejected",
        outcome="failure",
        reason_code=reason_code,
        correlation_id=correlation_id,
        actor_user_id=actor_user_id,
        operator_reason=(
            operator_reason
            if operator_reason in EXTERNAL_IDENTITY_RESTORATION_REASONS
            else None
        ),
    )
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"X-Correlation-ID": correlation_id},
    )


def _stripped_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
