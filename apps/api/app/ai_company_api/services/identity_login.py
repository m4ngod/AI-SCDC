from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from urllib.parse import urlencode

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    AccountLinkRecovery,
    AccountKind,
    DeviceSession,
    ExternalIdentity,
    LoginTransaction,
    Organization,
    OrganizationMember,
    User,
    Workspace,
    WorkspaceRole,
    utc_now,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
    CustomerIdentityProviderError,
    CustomerIdentityProviderUnavailable,
    OidcAuthorizationRequest,
    ValidatedExternalIdentity,
)
from ai_company_api.services.identity_audit import record_identity_audit_event
from ai_company_api.services.identity_device_sessions import (
    coarse_device_description,
)
from ai_company_api.services.user_session_credentials import (
    USER_SESSION_COOKIE,
    USER_SESSION_IDLE_SECONDS,
    USER_SESSION_PREVIOUS_SECRET_SECONDS,
    UserSessionCredentialRejected,
    hash_session_secret,
    resolve_user_session_credential,
)


LOGIN_BROWSER_COOKIE = "__Host-ai_scdc_login"
LOGIN_TRANSACTION_TTL_SECONDS = 600
RECENT_AUTHENTICATION_EMAIL_ACR = (
    "urn:ai-scdc:email-verification-code"
)
RECENT_AUTHENTICATION_CLOCK_SKEW_SECONDS = 60
PERSONAL_ONBOARDING_FAILURE_STEPS = frozenset(
    {
        "user",
        "account",
        "workspace",
        "membership",
        "external_identity",
        "device_session",
    }
)


class PersonalOnboardingInjectedFailure(RuntimeError):
    pass


def start_login(
    session: Session,
    *,
    provider: CustomerIdentityProvider,
    return_to: str,
    allowed_return_destinations: frozenset[str],
    public_origin: str,
    transaction_ttl_seconds: int = LOGIN_TRANSACTION_TTL_SECONDS,
    now: datetime | None = None,
) -> Response:
    return _start_authentication_transaction(
        session,
        provider=provider,
        return_to=return_to,
        allowed_return_destinations=allowed_return_destinations,
        public_origin=public_origin,
        transaction_ttl_seconds=transaction_ttl_seconds,
        purpose="login",
        requested_device_session=None,
        now=now,
    )


def start_recent_authentication(
    session: Session,
    *,
    provider: CustomerIdentityProvider,
    device_session: DeviceSession,
    return_to: str,
    allowed_return_destinations: frozenset[str],
    public_origin: str,
    transaction_ttl_seconds: int = LOGIN_TRANSACTION_TTL_SECONDS,
    now: datetime | None = None,
) -> Response:
    return _start_authentication_transaction(
        session,
        provider=provider,
        return_to=return_to,
        allowed_return_destinations=allowed_return_destinations,
        public_origin=public_origin,
        transaction_ttl_seconds=transaction_ttl_seconds,
        purpose="recent_authentication",
        requested_device_session=device_session,
        now=now,
    )


def _start_authentication_transaction(
    session: Session,
    *,
    provider: CustomerIdentityProvider,
    return_to: str,
    allowed_return_destinations: frozenset[str],
    public_origin: str,
    transaction_ttl_seconds: int,
    purpose: str,
    requested_device_session: DeviceSession | None,
    now: datetime | None,
) -> Response:
    current_time = _as_utc(now) if now is not None else utc_now()
    correlation_id = secrets.token_hex(16)
    is_recent_authentication = purpose == "recent_authentication"
    requires_fresh_email_verification = (
        is_recent_authentication or purpose == "login"
    )
    failure_event_type = (
        "recent_authentication_failed"
        if is_recent_authentication
        else "login_failure"
    )
    failure_error = (
        "recent_authentication_failed"
        if is_recent_authentication
        else "login_failed"
    )
    user_id = (
        requested_device_session.user_id
        if requested_device_session is not None
        else None
    )
    device_session_id = (
        requested_device_session.id
        if requested_device_session is not None
        else None
    )
    if return_to not in allowed_return_destinations:
        record_identity_audit_event(
            session,
            event_type=failure_event_type,
            outcome="failure",
            reason_code="return_destination_not_allowed",
            correlation_id=correlation_id,
            user_id=user_id,
            device_session_id=device_session_id,
        )
        return _safe_failure_response(
            error=failure_error,
            correlation_id=correlation_id,
        )

    state_value = secrets.token_urlsafe(32)
    nonce_value = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    browser_secret = secrets.token_urlsafe(32)
    redirect_uri = f"{public_origin.rstrip('/')}/auth/callback"
    try:
        provider.discover()
    except CustomerIdentityProviderUnavailable:
        record_identity_audit_event(
            session,
            event_type="identity_provider_unavailable",
            outcome="failure",
            reason_code="discovery_unavailable",
            correlation_id=correlation_id,
            user_id=user_id,
            device_session_id=device_session_id,
        )
        return (
            _recent_authentication_return_response(
                return_to,
                result="provider_unavailable",
                correlation_id=correlation_id,
            )
            if is_recent_authentication
            else _safe_failure_response(
                error="identity_provider_unavailable",
                correlation_id=correlation_id,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        )
    except CustomerIdentityProviderError:
        record_identity_audit_event(
            session,
            event_type=failure_event_type,
            outcome="failure",
            reason_code="discovery_failed",
            correlation_id=correlation_id,
            user_id=user_id,
            device_session_id=device_session_id,
        )
        return (
            _recent_authentication_return_response(
                return_to,
                result="failed",
                correlation_id=correlation_id,
            )
            if is_recent_authentication
            else _safe_failure_response(
                error=failure_error,
                correlation_id=correlation_id,
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        )
    authorization_request = OidcAuthorizationRequest(
        client_id=provider.client_id,
        redirect_uri=redirect_uri,
        state=state_value,
        nonce=nonce_value,
        code_challenge=_pkce_s256(code_verifier),
        prompt="login" if requires_fresh_email_verification else None,
        max_age_seconds=(
            0 if requires_fresh_email_verification else None
        ),
        acr_values=(
            RECENT_AUTHENTICATION_EMAIL_ACR
            if requires_fresh_email_verification
            else None
        ),
        authentication_requested_at=current_time,
    )
    try:
        authorization_url = provider.authorization_url(authorization_request)
    except CustomerIdentityProviderUnavailable:
        record_identity_audit_event(
            session,
            event_type="identity_provider_unavailable",
            outcome="failure",
            reason_code="authorization_unavailable",
            correlation_id=correlation_id,
            user_id=user_id,
            device_session_id=device_session_id,
        )
        return (
            _recent_authentication_return_response(
                return_to,
                result="provider_unavailable",
                correlation_id=correlation_id,
            )
            if is_recent_authentication
            else _safe_failure_response(
                error="identity_provider_unavailable",
                correlation_id=correlation_id,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        )
    except CustomerIdentityProviderError:
        record_identity_audit_event(
            session,
            event_type=failure_event_type,
            outcome="failure",
            reason_code="authorization_failed",
            correlation_id=correlation_id,
            user_id=user_id,
            device_session_id=device_session_id,
        )
        return (
            _recent_authentication_return_response(
                return_to,
                result="failed",
                correlation_id=correlation_id,
            )
            if is_recent_authentication
            else _safe_failure_response(
                error=failure_error,
                correlation_id=correlation_id,
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        )

    transaction = LoginTransaction(
        state_hash=_secret_hash(state_value),
        nonce_hash=_secret_hash(nonce_value),
        pkce_verifier=code_verifier,
        return_to=return_to,
        browser_binding_hash=_secret_hash(browser_secret),
        redirect_uri=redirect_uri,
        correlation_id=correlation_id,
        purpose=purpose,
        requested_session_id=device_session_id,
        expires_at=current_time + timedelta(seconds=transaction_ttl_seconds),
        created_at=current_time,
    )
    session.add(transaction)
    if is_recent_authentication:
        record_identity_audit_event(
            session,
            event_type="recent_authentication_started",
            outcome="success",
            reason_code="forced_email_verification",
            correlation_id=correlation_id,
            user_id=user_id,
            device_session_id=device_session_id,
            commit=False,
        )
    session.commit()
    session.refresh(transaction)

    response = RedirectResponse(authorization_url, status_code=303)
    response.set_cookie(
        key=LOGIN_BROWSER_COOKIE,
        value=f"{transaction.id}.{browser_secret}",
        max_age=transaction_ttl_seconds,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.headers["X-Correlation-ID"] = transaction.correlation_id
    return response


def complete_login_callback(
    session: Session,
    *,
    provider: CustomerIdentityProvider,
    request: Request,
    state_value: str,
    code: str,
    personal_onboarding_failure_step: str | None = None,
    now: datetime | None = None,
) -> Response:
    current_time = _as_utc(now) if now is not None else utc_now()
    transaction = session.exec(
        select(LoginTransaction).where(
            LoginTransaction.state_hash == _secret_hash(state_value)
        )
    ).first()
    if transaction is None:
        return _reject_callback(
            session,
            transaction=None,
            correlation_id=secrets.token_hex(16),
            reason_code="state_not_found",
        )
    if transaction.status == "completed":
        completed_response = _completed_session_response(
            request,
            session,
            transaction,
            now=current_time,
        )
        if completed_response is not None:
            return completed_response
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="completed_transaction_replay",
        )
    if transaction.status == "processing":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="callback_already_claimed",
            error="login_callback_in_progress",
            status_code=status.HTTP_409_CONFLICT,
            terminalize_transaction=False,
        )
    if transaction.status != "pending":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="transaction_not_pending",
        )
    if _as_utc(transaction.expires_at) <= current_time:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="transaction_expired",
        )
    if not _has_browser_binding(request, transaction):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="browser_binding_mismatch",
        )
    if not _claim_login_transaction(session, transaction):
        session.expire_all()
        current_transaction = session.get(LoginTransaction, transaction.id)
        if (
            current_transaction is not None
            and current_transaction.status == "completed"
        ):
            completed_response = _completed_session_response(
                request,
                session,
                current_transaction,
                now=current_time,
            )
            if completed_response is not None:
                return completed_response
        if (
            current_transaction is not None
            and current_transaction.status == "completed"
        ):
            return _reject_callback(
                session,
                transaction=current_transaction,
                correlation_id=current_transaction.correlation_id,
                reason_code="completed_transaction_replay",
                terminalize_transaction=False,
            )
        return _reject_callback(
            session,
            transaction=current_transaction,
            correlation_id=transaction.correlation_id,
            reason_code="callback_already_claimed",
            error="login_callback_in_progress",
            status_code=status.HTTP_409_CONFLICT,
            terminalize_transaction=False,
        )

    try:
        token_response = provider.exchange_code(
            code=code,
            redirect_uri=transaction.redirect_uri,
            code_verifier=transaction.pkce_verifier,
        )
    except CustomerIdentityProviderUnavailable:
        return _provider_unavailable_callback(
            session,
            transaction=transaction,
            reason_code="token_exchange_unavailable",
        )
    except CustomerIdentityProviderError:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="code_exchange_failed",
        )

    try:
        identity_claims = provider.validate_id_token(
            token_response.id_token,
            expected_audience=provider.client_id,
        )
    except CustomerIdentityProviderUnavailable:
        return _provider_unavailable_callback(
            session,
            transaction=transaction,
            reason_code="token_validation_unavailable",
        )
    except CustomerIdentityProviderError:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="invalid_id_token",
        )

    try:
        discovery = provider.discover()
    except CustomerIdentityProviderUnavailable:
        return _provider_unavailable_callback(
            session,
            transaction=transaction,
            reason_code="discovery_unavailable",
        )
    except CustomerIdentityProviderError:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="discovery_failed",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    if identity_claims.issuer != discovery.issuer:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="issuer_mismatch",
        )
    if _secret_hash(identity_claims.nonce) != transaction.nonce_hash:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="nonce_mismatch",
        )

    try:
        identity_status = provider.identity_status(
            issuer=identity_claims.issuer,
            subject=identity_claims.subject,
        )
    except CustomerIdentityProviderUnavailable:
        return _provider_unavailable_callback(
            session,
            transaction=transaction,
            reason_code="identity_status_unavailable",
        )
    except CustomerIdentityProviderError:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="identity_status_invalid",
        )
    if identity_status != "active":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="identity_status_inactive",
        )
    if transaction.purpose == "recent_authentication":
        return _complete_recent_authentication(
            session,
            transaction=transaction,
            identity_claims=identity_claims,
            request=request,
            now=current_time,
        )
    if transaction.purpose != "login":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="transaction_purpose_invalid",
        )

    transaction_id = transaction.id
    correlation_id = transaction.correlation_id
    external_identity = session.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == identity_claims.issuer,
            ExternalIdentity.subject == identity_claims.subject,
        )
    ).first()
    verified_email_authentication_time = (
        _validated_recent_email_authentication_time(
            identity_claims,
            transaction=transaction,
            now=current_time,
        )
    )
    if verified_email_authentication_time is None:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="authentication_method_not_satisfied",
        )
    onboarding_created = external_identity is None
    if external_identity is None:
        legacy_user = _legacy_user_matching_email(
            session,
            identity_claims.email,
        )
        if legacy_user is not None:
            return _require_explicit_account_link(
                session,
                transaction=transaction,
                identity_claims=identity_claims,
            )
        try:
            user, membership, external_identity = _create_personal_onboarding(
                session,
                identity_claims=identity_claims,
                failure_step=personal_onboarding_failure_step,
            )
        except (PersonalOnboardingInjectedFailure, SQLAlchemyError):
            return _rollback_personal_onboarding(
                session,
                transaction_id=transaction_id,
                correlation_id=correlation_id,
            )
    elif external_identity.status != "active":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="external_identity_not_active",
        )
    else:
        user = session.get(User, external_identity.user_id)
        membership = session.exec(
            select(OrganizationMember)
            .where(
                OrganizationMember.user_id == external_identity.user_id,
                OrganizationMember.status == "active",
            )
            .order_by(OrganizationMember.created_at, OrganizationMember.id)
        ).first()
    if user is None or user.status != "active" or membership is None:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="account_scope_not_active",
        )
    workspace = session.get(Workspace, membership.workspace_id)
    organization = session.get(Organization, membership.organization_id)
    if (
        workspace is None
        or organization is None
        or workspace.status != "active"
        or organization.status != "active"
        or workspace.organization_id != organization.id
    ):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="account_scope_not_active",
        )

    session_secret = secrets.token_urlsafe(32)
    now = current_time
    recent_authenticated_at = verified_email_authentication_time
    device_session = DeviceSession(
        user_id=user.id,
        active_workspace_id=workspace.id,
        active_organization_id=organization.id,
        secret_hash=_secret_hash(session_secret),
        secret_rotated_at=now,
        recent_authenticated_at=recent_authenticated_at,
        device_description=coarse_device_description(
            request.headers.get("user-agent")
        ),
        idle_expires_at=now + timedelta(seconds=USER_SESSION_IDLE_SECONDS),
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    try:
        session.add(device_session)
        session.flush()
        if onboarding_created:
            _inject_personal_onboarding_failure(
                personal_onboarding_failure_step,
                "device_session",
            )
        transaction.status = "completed"
        transaction.completed_session_id = device_session.id
        transaction.completed_at = now
        session.add(transaction)
        if onboarding_created:
            record_identity_audit_event(
                session,
                event_type="onboarding_success",
                outcome="success",
                reason_code="personal_account_created",
                correlation_id=transaction.correlation_id,
                user_id=user.id,
                external_identity_id=external_identity.id,
                device_session_id=device_session.id,
                commit=False,
            )
        record_identity_audit_event(
            session,
            event_type="session_created",
            outcome="success",
            reason_code="oidc_callback",
            correlation_id=transaction.correlation_id,
            related_correlation_id=(
                external_identity.account_link_correlation_id
            ),
            user_id=user.id,
            external_identity_id=external_identity.id,
            device_session_id=device_session.id,
            commit=False,
        )
        record_identity_audit_event(
            session,
            event_type="login_success",
            outcome="success",
            reason_code=(
                "personal_account_onboarding"
                if onboarding_created
                else "linked_external_identity"
            ),
            correlation_id=transaction.correlation_id,
            related_correlation_id=(
                external_identity.account_link_correlation_id
            ),
            user_id=user.id,
            external_identity_id=external_identity.id,
            device_session_id=device_session.id,
            commit=False,
        )
        session.commit()
    except (PersonalOnboardingInjectedFailure, SQLAlchemyError):
        if not onboarding_created:
            raise
        return _rollback_personal_onboarding(
            session,
            transaction_id=transaction_id,
            correlation_id=correlation_id,
        )

    response = RedirectResponse(transaction.return_to, status_code=303)
    response.set_cookie(
        key=USER_SESSION_COOKIE,
        value=f"{device_session.id}.{session_secret}",
        max_age=USER_SESSION_IDLE_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        key=LOGIN_BROWSER_COOKIE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.headers["X-Correlation-ID"] = transaction.correlation_id
    return response


def _complete_recent_authentication(
    session: Session,
    *,
    transaction: LoginTransaction,
    identity_claims: ValidatedExternalIdentity,
    request: Request,
    now: datetime,
) -> Response:
    if (
        identity_claims.authentication_context
        != RECENT_AUTHENTICATION_EMAIL_ACR
    ):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="authentication_method_not_satisfied",
        )
    authenticated_at = identity_claims.authenticated_at
    allowed_clock_skew = timedelta(
        seconds=RECENT_AUTHENTICATION_CLOCK_SKEW_SECONDS
    )
    if (
        authenticated_at is None
        or _as_utc(authenticated_at)
        < _as_utc(transaction.created_at) - allowed_clock_skew
        or _as_utc(authenticated_at) > now + allowed_clock_skew
    ):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="authentication_not_fresh",
        )
    verified_authenticated_at = _as_utc(authenticated_at)

    cookie_value = request.cookies.get(USER_SESSION_COOKIE, "")
    try:
        resolved_credential = resolve_user_session_credential(
            session,
            cookie_value=cookie_value,
            now=now,
        )
    except UserSessionCredentialRejected:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="device_session_not_valid",
        )
    device_session = resolved_credential.device_session
    if (
        transaction.requested_session_id is None
        or device_session.id != transaction.requested_session_id
    ):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="device_session_mismatch",
        )

    external_identity = session.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == identity_claims.issuer,
            ExternalIdentity.subject == identity_claims.subject,
        )
    ).first()
    user = session.get(User, device_session.user_id)
    if (
        external_identity is None
        or external_identity.status != "active"
        or external_identity.user_id != device_session.user_id
        or user is None
        or user.status != "active"
    ):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="external_identity_mismatch",
        )

    previous_current_hash = device_session.secret_hash
    new_secret = secrets.token_urlsafe(32)
    result = session.execute(
        update(DeviceSession)
        .where(
            DeviceSession.id == device_session.id,
            DeviceSession.status == "active",
            DeviceSession.secret_hash == previous_current_hash,
        )
        .values(
            secret_hash=hash_session_secret(new_secret),
            previous_secret_hash=previous_current_hash,
            previous_secret_valid_until=now
            + timedelta(seconds=USER_SESSION_PREVIOUS_SECRET_SECONDS),
            secret_rotated_at=now,
            recent_authenticated_at=verified_authenticated_at,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        session.expire_all()
        current_transaction = session.get(LoginTransaction, transaction.id)
        return _reject_callback(
            session,
            transaction=current_transaction,
            correlation_id=transaction.correlation_id,
            reason_code="device_session_changed",
        )

    transaction.status = "completed"
    transaction.completed_session_id = device_session.id
    transaction.completed_at = now
    external_identity.last_confirmed_status = "active"
    external_identity.updated_at = now
    session.add(transaction)
    session.add(external_identity)
    record_identity_audit_event(
        session,
        event_type="session_credential_rotated",
        outcome="success",
        reason_code="recent_authentication",
        correlation_id=transaction.correlation_id,
        user_id=device_session.user_id,
        external_identity_id=external_identity.id,
        device_session_id=device_session.id,
        commit=False,
    )
    record_identity_audit_event(
        session,
        event_type="recent_authentication_succeeded",
        outcome="success",
        reason_code="email_verification_completed",
        correlation_id=transaction.correlation_id,
        user_id=device_session.user_id,
        external_identity_id=external_identity.id,
        device_session_id=device_session.id,
        commit=False,
    )
    session.commit()

    response = _recent_authentication_redirect(
        transaction,
        result="confirmed",
    )
    response.set_cookie(
        key=USER_SESSION_COOKIE,
        value=f"{device_session.id}.{new_secret}",
        max_age=USER_SESSION_IDLE_SECONDS,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        key=LOGIN_BROWSER_COOKIE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.headers["X-Correlation-ID"] = transaction.correlation_id
    return response


def _validated_recent_email_authentication_time(
    identity_claims: ValidatedExternalIdentity,
    *,
    transaction: LoginTransaction,
    now: datetime,
) -> datetime | None:
    if (
        identity_claims.authentication_context
        != RECENT_AUTHENTICATION_EMAIL_ACR
        or identity_claims.authenticated_at is None
    ):
        return None
    authenticated_at = _as_utc(identity_claims.authenticated_at)
    allowed_clock_skew = timedelta(
        seconds=RECENT_AUTHENTICATION_CLOCK_SKEW_SECONDS
    )
    if (
        authenticated_at
        < _as_utc(transaction.created_at) - allowed_clock_skew
        or authenticated_at > now + allowed_clock_skew
    ):
        return None
    return authenticated_at


def reject_malformed_login_callback(session: Session) -> Response:
    return _reject_callback(
        session,
        transaction=None,
        correlation_id=secrets.token_hex(16),
        reason_code="protocol_parameters_missing",
    )


def reject_provider_callback(
    session: Session,
    *,
    request: Request,
    state_value: str,
    provider_error: str,
    now: datetime | None = None,
) -> Response:
    current_time = _as_utc(now) if now is not None else utc_now()
    transaction = session.exec(
        select(LoginTransaction).where(
            LoginTransaction.state_hash == _secret_hash(state_value)
        )
    ).first()
    if transaction is None:
        return _reject_callback(
            session,
            transaction=None,
            correlation_id=secrets.token_hex(16),
            reason_code="state_not_found",
        )
    if transaction.status == "completed":
        completed_response = _completed_session_response(
            request,
            session,
            transaction,
            now=current_time,
        )
        if completed_response is not None:
            return completed_response
    if transaction.status != "pending":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="transaction_not_pending",
        )
    if _as_utc(transaction.expires_at) <= current_time:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="transaction_expired",
        )
    if not _has_browser_binding(request, transaction):
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="browser_binding_mismatch",
        )
    if provider_error in {"temporarily_unavailable", "server_error"}:
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code=f"provider_{provider_error}",
            event_type="identity_provider_unavailable",
            error="identity_provider_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if provider_error != "access_denied":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="provider_error",
        )
    return _reject_callback(
        session,
        transaction=transaction,
        correlation_id=transaction.correlation_id,
        reason_code="provider_cancelled",
    )


def _create_personal_onboarding(
    session: Session,
    *,
    identity_claims: ValidatedExternalIdentity,
    failure_step: str | None,
) -> tuple[User, OrganizationMember, ExternalIdentity]:
    user = User(
        email=identity_claims.email,
        display_name=identity_claims.email or "AI-SCDC User",
    )
    session.add(user)
    session.flush()
    _inject_personal_onboarding_failure(failure_step, "user")

    account = Organization(
        name="Personal Account",
        account_kind=AccountKind.PERSONAL,
        personal_owner_user_id=user.id,
    )
    session.add(account)
    session.flush()
    _inject_personal_onboarding_failure(failure_step, "account")

    workspace = Workspace(
        organization_id=account.id,
        name="Default Workspace",
    )
    session.add(workspace)
    session.flush()
    _inject_personal_onboarding_failure(failure_step, "workspace")

    membership = OrganizationMember(
        organization_id=account.id,
        workspace_id=workspace.id,
        user_id=user.id,
        role=WorkspaceRole.OWNER,
    )
    session.add(membership)
    session.flush()
    _inject_personal_onboarding_failure(failure_step, "membership")

    external_identity = ExternalIdentity(
        issuer=identity_claims.issuer,
        subject=identity_claims.subject,
        user_id=user.id,
        email=identity_claims.email,
    )
    session.add(external_identity)
    session.flush()
    _inject_personal_onboarding_failure(failure_step, "external_identity")
    return user, membership, external_identity


def _legacy_user_matching_email(
    session: Session,
    email: str | None,
) -> User | None:
    normalized_email = (email or "").strip().lower()
    if normalized_email == "":
        return None
    return session.exec(
        select(User)
        .join(
            OrganizationMember,
            OrganizationMember.user_id == User.id,
        )
        .join(
            Organization,
            Organization.id == OrganizationMember.organization_id,
        )
        .where(
            func.lower(User.email) == normalized_email,
            Organization.account_kind == AccountKind.LEGACY,
        )
        .order_by(User.created_at, User.id)
    ).first()


def _require_explicit_account_link(
    session: Session,
    *,
    transaction: LoginTransaction,
    identity_claims: ValidatedExternalIdentity,
) -> JSONResponse:
    transaction_id = transaction.id
    transaction_correlation_id = transaction.correlation_id
    recovery = session.exec(
        select(AccountLinkRecovery).where(
            AccountLinkRecovery.issuer == identity_claims.issuer,
            AccountLinkRecovery.subject == identity_claims.subject,
        )
    ).first()
    if recovery is None:
        recovery = AccountLinkRecovery(
            issuer=identity_claims.issuer,
            subject=identity_claims.subject,
            verified_email=(identity_claims.email or "").strip().lower(),
            correlation_id=transaction.correlation_id,
        )
        session.add(recovery)
    try:
        return _commit_account_link_required(
            session,
            transaction=transaction,
            recovery=recovery,
        )
    except IntegrityError:
        session.rollback()
        recovery = session.exec(
            select(AccountLinkRecovery).where(
                AccountLinkRecovery.issuer == identity_claims.issuer,
                AccountLinkRecovery.subject == identity_claims.subject,
            )
        ).first()
        transaction = session.get(LoginTransaction, transaction_id)
        if recovery is None or transaction is None:
            return _reject_callback(
                session,
                transaction=transaction,
                correlation_id=transaction_correlation_id,
                reason_code="account_link_recovery_persistence_failed",
                error="login_failed",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return _commit_account_link_required(
            session,
            transaction=transaction,
            recovery=recovery,
        )


def _commit_account_link_required(
    session: Session,
    *,
    transaction: LoginTransaction,
    recovery: AccountLinkRecovery,
) -> JSONResponse:
    transaction.status = "rejected"
    session.add(transaction)
    record_identity_audit_event(
        session,
        event_type="account_link_required",
        outcome="failure",
        reason_code="legacy_email_collision",
        correlation_id=recovery.correlation_id,
        commit=False,
    )
    session.commit()
    return _safe_failure_response(
        error="account_link_required",
        correlation_id=recovery.correlation_id,
        status_code=status.HTTP_409_CONFLICT,
    )


def _inject_personal_onboarding_failure(
    configured_step: str | None,
    completed_step: str,
) -> None:
    if configured_step == completed_step:
        raise PersonalOnboardingInjectedFailure(completed_step)


def _rollback_personal_onboarding(
    session: Session,
    *,
    transaction_id: str,
    correlation_id: str,
) -> JSONResponse:
    session.rollback()
    transaction = session.get(LoginTransaction, transaction_id)
    if transaction is not None and transaction.status == "processing":
        transaction.status = "rejected"
        session.add(transaction)
    record_identity_audit_event(
        session,
        event_type="onboarding_rollback",
        outcome="failure",
        reason_code="persistence_failed",
        correlation_id=correlation_id,
    )
    return _safe_failure_response(
        error="login_failed",
        correlation_id=correlation_id,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _has_browser_binding(
    request: Request,
    transaction: LoginTransaction,
) -> bool:
    cookie_value = request.cookies.get(LOGIN_BROWSER_COOKIE, "")
    transaction_id, separator, browser_secret = cookie_value.partition(".")
    return not (
        separator == ""
        or transaction_id != transaction.id
        or _secret_hash(browser_secret) != transaction.browser_binding_hash
    )


def _has_completed_session(
    request: Request,
    session: Session,
    transaction: LoginTransaction,
    *,
    now: datetime,
) -> bool:
    if transaction.completed_session_id is None:
        return False
    cookie_value = request.cookies.get(USER_SESSION_COOKIE, "")
    resolved_credential = resolve_user_session_credential(
        session,
        cookie_value=cookie_value,
        now=now,
    )
    return (
        resolved_credential.device_session.id
        == transaction.completed_session_id
    )


def _completed_session_response(
    request: Request,
    session: Session,
    transaction: LoginTransaction,
    *,
    now: datetime,
) -> Response | None:
    try:
        has_completed_session = _has_completed_session(
            request,
            session,
            transaction,
            now=now,
        )
    except UserSessionCredentialRejected as exc:
        if exc.correlation_id is None:
            return None
        return _safe_failure_response(
            error="login_callback_rejected",
            correlation_id=exc.correlation_id,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not has_completed_session:
        return None
    response = (
        _recent_authentication_redirect(
            transaction,
            result="confirmed",
        )
        if transaction.purpose == "recent_authentication"
        else RedirectResponse(transaction.return_to, status_code=303)
    )
    response.headers["X-Correlation-ID"] = transaction.correlation_id
    return response


def _recent_authentication_redirect(
    transaction: LoginTransaction,
    *,
    result: str,
) -> RedirectResponse:
    return _recent_authentication_return_response(
        transaction.return_to,
        result=result,
        correlation_id=transaction.correlation_id,
    )


def _recent_authentication_return_response(
    return_to: str,
    *,
    result: str,
    correlation_id: str,
) -> RedirectResponse:
    separator = "&" if "?" in return_to else "?"
    location = (
        f"{return_to}{separator}"
        f"{urlencode({'reauthentication': result})}"
    )
    response = RedirectResponse(location, status_code=303)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


def _claim_login_transaction(
    session: Session,
    transaction: LoginTransaction,
) -> bool:
    result = session.execute(
        update(LoginTransaction)
        .where(
            LoginTransaction.id == transaction.id,
            LoginTransaction.status == "pending",
        )
        .values(status="processing")
        .execution_options(synchronize_session=False)
    )
    session.commit()
    if result.rowcount != 1:
        return False
    session.refresh(transaction)
    return True


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pkce_s256(code_verifier: str) -> str:
    digest = sha256(code_verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _secret_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _safe_failure_response(
    *,
    error: str,
    correlation_id: str,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> JSONResponse:
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "correlation_id": correlation_id,
        },
    )
    response.headers["X-Correlation-ID"] = correlation_id
    return response


def _reject_callback(
    session: Session,
    *,
    transaction: LoginTransaction | None,
    correlation_id: str,
    reason_code: str,
    event_type: str = "callback_rejected",
    error: str = "login_callback_rejected",
    status_code: int = status.HTTP_400_BAD_REQUEST,
    terminalize_transaction: bool = True,
) -> Response:
    is_recent_authentication = (
        transaction is not None
        and transaction.purpose == "recent_authentication"
    )
    if (
        terminalize_transaction
        and transaction is not None
        and transaction.status in {"pending", "processing"}
    ):
        transaction.status = "rejected"
        session.add(transaction)
    device_session = (
        session.get(DeviceSession, transaction.requested_session_id)
        if is_recent_authentication
        and transaction is not None
        and transaction.requested_session_id is not None
        else None
    )
    record_identity_audit_event(
        session,
        event_type=(
            "recent_authentication_failed"
            if is_recent_authentication and event_type == "callback_rejected"
            else event_type
        ),
        outcome="failure",
        reason_code=reason_code,
        correlation_id=correlation_id,
        user_id=(
            device_session.user_id
            if device_session is not None
            else None
        ),
        device_session_id=(
            device_session.id
            if device_session is not None
            else None
        ),
    )
    if is_recent_authentication and transaction is not None:
        result = (
            "provider_unavailable"
            if error == "identity_provider_unavailable"
            else "cancelled"
            if reason_code == "provider_cancelled"
            else "failed"
        )
        response = _recent_authentication_redirect(
            transaction,
            result=result,
        )
        response.delete_cookie(
            key=LOGIN_BROWSER_COOKIE,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.headers["X-Correlation-ID"] = correlation_id
        return response
    return _safe_failure_response(
        error=error,
        correlation_id=correlation_id,
        status_code=status_code,
    )


def _provider_unavailable_callback(
    session: Session,
    *,
    transaction: LoginTransaction,
    reason_code: str,
) -> Response:
    return _reject_callback(
        session,
        transaction=transaction,
        correlation_id=transaction.correlation_id,
        reason_code=reason_code,
        event_type="identity_provider_unavailable",
        error="identity_provider_unavailable",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
