from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    AccountKind,
    AccountLinkRecovery,
    ExternalIdentity,
    Organization,
    OrganizationMember,
    User,
    utc_now,
)
from ai_company_api.schemas.api import AccountLinkCreate, AccountLinkRead
from ai_company_api.services.auth_context import AuthContext
from ai_company_api.services.identity_audit import record_identity_audit_event
from ai_company_api.services.workspace_permissions import (
    allowed_roles_for_permission,
)


ACCOUNT_LINK_REASONS = frozenset(
    {
        "legacy_account_migration",
        "verified_support_recovery",
    }
)


def create_account_link(
    session: Session,
    *,
    auth: AuthContext,
    data: AccountLinkCreate,
    operator_user_ids: frozenset[str],
) -> AccountLinkRead:
    correlation_id = _stripped_text(data.correlation_id) or ""
    supplied_reason = _stripped_text(data.reason)
    if (
        auth.user_id not in operator_user_ids
        or not auth.has_any_role(
            allowed_roles_for_permission("operator.write")
        )
    ):
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=supplied_reason,
            reason_code="operator_not_authorized",
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account link operation is not authorized",
        )

    normalized = _normalized_request(data)
    if normalized is None:
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            reason_code="invalid_request",
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Account link request is invalid",
        )
    issuer, subject, user_id, operator_reason = normalized

    recovery = session.exec(
        select(AccountLinkRecovery).where(
            AccountLinkRecovery.correlation_id == correlation_id
        )
    ).first()
    if recovery is None:
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="recovery_not_found",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Account link recovery was not found",
        )
    if recovery.status != "pending":
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="recovery_already_used",
            status_code=status.HTTP_409_CONFLICT,
            detail="Account link recovery is no longer pending",
        )
    if recovery.issuer != issuer or recovery.subject != subject:
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="identity_mismatch",
            status_code=status.HTTP_409_CONFLICT,
            detail="External Identity does not match the recovery",
        )

    user = session.get(User, user_id)
    membership = session.exec(
        select(OrganizationMember)
        .join(
            Organization,
            Organization.id == OrganizationMember.organization_id,
        )
        .where(
            OrganizationMember.user_id == user_id,
            OrganizationMember.status == "active",
            Organization.status == "active",
            Organization.account_kind == AccountKind.LEGACY,
        )
    ).first()
    if (
        user is None
        or user.status != "active"
        or membership is None
    ):
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="target_not_in_operator_scope",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target legacy User was not found",
        )
    if (user.email or "").strip().lower() != recovery.verified_email:
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="target_email_mismatch",
            status_code=status.HTTP_409_CONFLICT,
            detail="Target User does not match the verified email",
        )

    existing_identity = session.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == issuer,
            ExternalIdentity.subject == subject,
        )
    ).first()
    if existing_identity is not None:
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="external_identity_already_linked",
            status_code=status.HTTP_409_CONFLICT,
            detail="External Identity is already linked",
        )

    external_identity = ExternalIdentity(
        issuer=issuer,
        subject=subject,
        user_id=user.id,
        email=recovery.verified_email,
        account_link_correlation_id=correlation_id,
    )
    session.add(external_identity)
    try:
        session.flush()
        recovery.status = "completed"
        recovery.target_user_id = user.id
        recovery.completed_at = utc_now()
        session.add(recovery)
        record_identity_audit_event(
            session,
            event_type="account_link_created",
            outcome="success",
            reason_code="operator_mapping",
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            user_id=user.id,
            external_identity_id=external_identity.id,
            commit=False,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        _reject_account_link(
            session,
            correlation_id=correlation_id,
            actor_user_id=auth.user_id,
            operator_reason=operator_reason,
            reason_code="account_link_conflict",
            status_code=status.HTTP_409_CONFLICT,
            detail="Account link recovery is no longer pending",
        )
    return AccountLinkRead(
        status="linked",
        correlation_id=correlation_id,
        user_id=user.id,
        external_identity_id=external_identity.id,
    )


def _normalized_request(
    data: AccountLinkCreate,
) -> tuple[str, str, str, str] | None:
    correlation_id = _stripped_text(data.correlation_id)
    issuer = _stripped_text(data.issuer)
    subject = _stripped_text(data.subject)
    user_id = _stripped_text(data.user_id)
    operator_reason = _stripped_text(data.reason)
    if (
        not correlation_id
        or not issuer
        or not subject
        or not user_id
        or operator_reason not in ACCOUNT_LINK_REASONS
    ):
        return None
    return issuer, subject, user_id, operator_reason


def _stripped_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()


def _reject_account_link(
    session: Session,
    *,
    correlation_id: str,
    actor_user_id: str,
    reason_code: str,
    status_code: int,
    detail: str,
    operator_reason: str | None = None,
) -> None:
    safe_correlation_id = correlation_id or "missing_correlation"
    record_identity_audit_event(
        session,
        event_type="account_link_rejected",
        outcome="failure",
        reason_code=reason_code,
        correlation_id=safe_correlation_id,
        actor_user_id=actor_user_id,
        operator_reason=(
            operator_reason
            if operator_reason in ACCOUNT_LINK_REASONS
            else None
        ),
    )
    raise HTTPException(status_code=status_code, detail=detail)
