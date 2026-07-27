from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import update
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    DeviceSession,
    ExternalIdentity,
    LoginTransaction,
    Organization,
    OrganizationMember,
    User,
    Workspace,
    utc_now,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
    CustomerIdentityProviderError,
    CustomerIdentityProviderUnavailable,
    OidcAuthorizationRequest,
)
from ai_company_api.services.identity_audit import record_identity_audit_event


LOGIN_BROWSER_COOKIE = "__Host-ai_scdc_login"
USER_SESSION_COOKIE = "__Host-ai_scdc_session"
LOGIN_TRANSACTION_TTL_SECONDS = 600
USER_SESSION_IDLE_SECONDS = 30 * 24 * 60 * 60


def start_login(
    session: Session,
    *,
    provider: CustomerIdentityProvider,
    return_to: str,
    allowed_return_destinations: frozenset[str],
    public_origin: str,
    transaction_ttl_seconds: int = LOGIN_TRANSACTION_TTL_SECONDS,
) -> Response:
    correlation_id = secrets.token_hex(16)
    if return_to not in allowed_return_destinations:
        record_identity_audit_event(
            session,
            event_type="login_failure",
            outcome="failure",
            reason_code="return_destination_not_allowed",
            correlation_id=correlation_id,
        )
        return _safe_failure_response(
            error="login_failed",
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
        )
        return _safe_failure_response(
            error="identity_provider_unavailable",
            correlation_id=correlation_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except CustomerIdentityProviderError:
        record_identity_audit_event(
            session,
            event_type="login_failure",
            outcome="failure",
            reason_code="discovery_failed",
            correlation_id=correlation_id,
        )
        return _safe_failure_response(
            error="login_failed",
            correlation_id=correlation_id,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    authorization_request = OidcAuthorizationRequest(
        client_id=provider.client_id,
        redirect_uri=redirect_uri,
        state=state_value,
        nonce=nonce_value,
        code_challenge=_pkce_s256(code_verifier),
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
        )
        return _safe_failure_response(
            error="identity_provider_unavailable",
            correlation_id=correlation_id,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except CustomerIdentityProviderError:
        record_identity_audit_event(
            session,
            event_type="login_failure",
            outcome="failure",
            reason_code="authorization_failed",
            correlation_id=correlation_id,
        )
        return _safe_failure_response(
            error="login_failed",
            correlation_id=correlation_id,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    transaction = LoginTransaction(
        state_hash=_secret_hash(state_value),
        nonce_hash=_secret_hash(nonce_value),
        pkce_verifier=code_verifier,
        return_to=return_to,
        browser_binding_hash=_secret_hash(browser_secret),
        redirect_uri=redirect_uri,
        correlation_id=correlation_id,
        expires_at=utc_now() + timedelta(seconds=transaction_ttl_seconds),
    )
    session.add(transaction)
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
) -> Response:
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
        if _has_completed_session(request, session, transaction):
            response = RedirectResponse(transaction.return_to, status_code=303)
            response.headers["X-Correlation-ID"] = transaction.correlation_id
            return response
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
    if _as_utc(transaction.expires_at) <= utc_now():
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
            and _has_completed_session(request, session, current_transaction)
        ):
            response = RedirectResponse(
                current_transaction.return_to,
                status_code=303,
            )
            response.headers["X-Correlation-ID"] = (
                current_transaction.correlation_id
            )
            return response
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

    external_identity = session.exec(
        select(ExternalIdentity).where(
            ExternalIdentity.issuer == identity_claims.issuer,
            ExternalIdentity.subject == identity_claims.subject,
        )
    ).first()
    if external_identity is None or external_identity.status != "active":
        return _reject_callback(
            session,
            transaction=transaction,
            correlation_id=transaction.correlation_id,
            reason_code="external_identity_not_active",
        )
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
    now = utc_now()
    device_session = DeviceSession(
        user_id=user.id,
        active_workspace_id=workspace.id,
        active_organization_id=organization.id,
        secret_hash=_secret_hash(session_secret),
        idle_expires_at=now + timedelta(seconds=USER_SESSION_IDLE_SECONDS),
        last_seen_at=now,
    )
    session.add(device_session)
    session.flush()
    transaction.status = "completed"
    transaction.completed_session_id = device_session.id
    transaction.completed_at = now
    session.add(transaction)
    record_identity_audit_event(
        session,
        event_type="session_created",
        outcome="success",
        reason_code="oidc_callback",
        correlation_id=transaction.correlation_id,
        user_id=user.id,
        external_identity_id=external_identity.id,
        device_session_id=device_session.id,
        commit=False,
    )
    record_identity_audit_event(
        session,
        event_type="login_success",
        outcome="success",
        reason_code="linked_external_identity",
        correlation_id=transaction.correlation_id,
        user_id=user.id,
        external_identity_id=external_identity.id,
        device_session_id=device_session.id,
        commit=False,
    )
    session.commit()

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


def reject_malformed_login_callback(session: Session) -> Response:
    return _reject_callback(
        session,
        transaction=None,
        correlation_id=secrets.token_hex(16),
        reason_code="protocol_parameters_missing",
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
) -> bool:
    if transaction.completed_session_id is None:
        return False
    cookie_value = request.cookies.get(USER_SESSION_COOKIE, "")
    session_id, separator, session_secret = cookie_value.partition(".")
    if separator == "" or session_id != transaction.completed_session_id:
        return False
    device_session = session.get(DeviceSession, session_id)
    return bool(
        device_session is not None
        and device_session.status == "active"
        and _as_utc(device_session.idle_expires_at) > utc_now()
        and secrets.compare_digest(
            _secret_hash(session_secret),
            device_session.secret_hash,
        )
    )


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
) -> JSONResponse:
    if (
        terminalize_transaction
        and transaction is not None
        and transaction.status in {"pending", "processing"}
    ):
        transaction.status = "rejected"
        session.add(transaction)
    record_identity_audit_event(
        session,
        event_type=event_type,
        outcome="failure",
        reason_code=reason_code,
        correlation_id=correlation_id,
    )
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
) -> JSONResponse:
    return _reject_callback(
        session,
        transaction=transaction,
        correlation_id=transaction.correlation_id,
        reason_code=reason_code,
        event_type="identity_provider_unavailable",
        error="identity_provider_unavailable",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )
