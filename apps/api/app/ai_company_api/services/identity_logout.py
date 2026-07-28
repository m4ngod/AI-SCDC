from datetime import datetime, timedelta, timezone
import secrets
from urllib.parse import ParseResult, parse_qs, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import update
from sqlmodel import Session, select

from ai_company_api.models.entities import (
    DeviceSession,
    ProviderLogoutContinuation,
)
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
    CustomerIdentityProviderError,
    CustomerIdentityProviderUnavailable,
)
from ai_company_api.services.identity_audit import record_identity_audit_event
from ai_company_api.services.user_session_credentials import (
    delete_user_session_cookie,
    hash_session_secret,
)


PROVIDER_LOGOUT_COOKIE = "__Host-ai_scdc_provider_logout"
PROVIDER_LOGOUT_CONTINUATION_PATH = "/auth/logout/provider"
PROVIDER_LOGOUT_CONTINUATION_SECONDS = 120


def sign_out_current_device(
    session: Session,
    *,
    request: Request,
    provider: CustomerIdentityProvider,
    device_session: DeviceSession,
    now: datetime,
) -> JSONResponse:
    correlation_id = secrets.token_hex(16)
    continuation = _latest_provider_logout_continuation(
        session,
        device_session_id=device_session.id,
    )
    logout_hint = (
        continuation.sealed_provider_hint
        if continuation is not None
        else None
    )
    device_session.status = "revoked"
    device_session.revoked_at = now
    device_session.updated_at = now
    session.add(device_session)
    record_identity_audit_event(
        session,
        event_type="session_signed_out",
        outcome="success",
        reason_code="current_device_revoked",
        correlation_id=correlation_id,
        user_id=device_session.user_id,
        device_session_id=device_session.id,
        commit=False,
    )
    session.commit()
    request.state.user_session_cookie_rotation = None

    post_logout_redirect_uri = f"{request.app.state.public_origin}/"
    redirect_to, provider_outcome, provider_reason = _prepare_end_session_redirect(
        provider,
        post_logout_redirect_uri=post_logout_redirect_uri,
        logout_hint=logout_hint,
    )
    continuation_cookie: str | None = None
    if (
        redirect_to is not None
        and logout_hint is not None
        and continuation is not None
    ):
        continuation_secret = secrets.token_urlsafe(32)
        continuation.status = "prepared"
        continuation.correlation_id = correlation_id
        continuation.browser_secret_hash = (
            hash_session_secret(continuation_secret)
        )
        continuation.browser_secret_expires_at = (
            now
            + timedelta(
                seconds=PROVIDER_LOGOUT_CONTINUATION_SECONDS
            )
        )
        continuation.consumed_at = None
        continuation.updated_at = now
        session.add(continuation)
        session.commit()
        continuation_cookie = (
            f"{continuation.id}.{continuation_secret}"
        )
        redirect_to = PROVIDER_LOGOUT_CONTINUATION_PATH
    else:
        if continuation is not None:
            continuation.sealed_provider_hint = None
            continuation.status = "consumed"
            continuation.correlation_id = correlation_id
            continuation.browser_secret_hash = None
            continuation.browser_secret_expires_at = None
            continuation.consumed_at = now
            continuation.updated_at = now
            session.add(continuation)
        record_identity_audit_event(
            session,
            event_type="provider_logout",
            outcome=provider_outcome,
            reason_code=provider_reason,
            correlation_id=correlation_id,
            user_id=device_session.user_id,
            device_session_id=device_session.id,
            commit=False,
        )
        session.commit()

    response = JSONResponse({"redirect_to": redirect_to})
    delete_user_session_cookie(response, request)
    if continuation_cookie is not None:
        response.set_cookie(
            key=PROVIDER_LOGOUT_COOKIE,
            value=continuation_cookie,
            max_age=PROVIDER_LOGOUT_CONTINUATION_SECONDS,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
    response.headers["X-Correlation-ID"] = correlation_id
    return response


def continue_provider_logout(
    session: Session,
    *,
    request: Request,
    provider: CustomerIdentityProvider,
    now: datetime,
) -> RedirectResponse:
    fallback = f"{request.app.state.public_origin}/"
    cookie_value = request.cookies.get(PROVIDER_LOGOUT_COOKIE, "")
    try:
        continuation_id, continuation_secret = cookie_value.split(".", 1)
    except ValueError:
        return _provider_logout_redirect_response(fallback)

    continuation = session.get(
        ProviderLogoutContinuation,
        continuation_id,
    )
    if (
        continuation is None
        or continuation.status != "prepared"
        or continuation.sealed_provider_hint is None
        or continuation.browser_secret_hash is None
        or continuation.browser_secret_expires_at is None
        or continuation.consumed_at is not None
        or _as_utc(continuation.browser_secret_expires_at)
        <= _as_utc(now)
        or continuation.browser_secret_hash
        != hash_session_secret(continuation_secret)
    ):
        return _provider_logout_redirect_response(fallback)

    logout_hint = continuation.sealed_provider_hint
    device_session_id = continuation.device_session_id
    correlation_id = continuation.correlation_id
    redirect_to, provider_outcome, provider_reason = (
        _prepare_end_session_redirect(
            provider,
            post_logout_redirect_uri=fallback,
            logout_hint=logout_hint,
        )
    )
    result = session.execute(
        update(ProviderLogoutContinuation)
        .where(
            ProviderLogoutContinuation.id == continuation.id,
            ProviderLogoutContinuation.status == "prepared",
            ProviderLogoutContinuation.browser_secret_hash
            == continuation.browser_secret_hash,
            ProviderLogoutContinuation.consumed_at.is_(None),
        )
        .values(
            sealed_provider_hint=None,
            status="consumed",
            browser_secret_hash=None,
            consumed_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        return _provider_logout_redirect_response(fallback)

    device_session = session.get(DeviceSession, device_session_id)
    record_identity_audit_event(
        session,
        event_type="provider_logout",
        outcome=provider_outcome,
        reason_code=provider_reason,
        correlation_id=(
            correlation_id
            or secrets.token_hex(16)
        ),
        user_id=(
            device_session.user_id
            if device_session is not None
            else None
        ),
        device_session_id=device_session_id,
        commit=False,
    )
    session.commit()
    return _provider_logout_redirect_response(
        redirect_to if redirect_to is not None else fallback
    )


def _latest_provider_logout_continuation(
    session: Session,
    *,
    device_session_id: str,
) -> ProviderLogoutContinuation | None:
    return session.exec(
        select(ProviderLogoutContinuation)
        .where(
            ProviderLogoutContinuation.device_session_id
            == device_session_id,
            ProviderLogoutContinuation.status == "available",
            ProviderLogoutContinuation.sealed_provider_hint.is_not(
                None
            ),
        )
        .order_by(
            ProviderLogoutContinuation.created_at.desc(),
            ProviderLogoutContinuation.id.desc(),
        )
    ).first()


def _provider_logout_redirect_response(
    redirect_to: str,
) -> RedirectResponse:
    response = RedirectResponse(redirect_to, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.delete_cookie(
        key=PROVIDER_LOGOUT_COOKIE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


def _prepare_end_session_redirect(
    provider: CustomerIdentityProvider,
    *,
    post_logout_redirect_uri: str,
    logout_hint: str | None = None,
) -> tuple[str | None, str, str]:
    try:
        discovery = provider.discover()
        endpoint = discovery.end_session_endpoint
        if endpoint is None:
            return None, "success", "end_session_not_configured"
        if not _valid_end_session_endpoint(
            endpoint,
            issuer=discovery.issuer,
        ):
            return None, "failure", "end_session_destination_rejected"
        redirect_to = provider.end_session_url(
            post_logout_redirect_uri=post_logout_redirect_uri,
            logout_hint=logout_hint,
        )
        if (
            redirect_to is None
            or not _valid_end_session_redirect(
                redirect_to,
                endpoint=endpoint,
                post_logout_redirect_uri=post_logout_redirect_uri,
            )
        ):
            return None, "failure", "end_session_destination_rejected"
        return (
            redirect_to,
            "success",
            "end_session_redirect_prepared",
        )
    except CustomerIdentityProviderUnavailable:
        return None, "failure", "provider_unavailable"
    except CustomerIdentityProviderError:
        return None, "failure", "provider_error"
    except (TypeError, ValueError, UnicodeError):
        return None, "failure", "end_session_destination_rejected"


def _valid_end_session_endpoint(endpoint: str, *, issuer: str) -> bool:
    parsed_endpoint = urlparse(endpoint)
    parsed_issuer = urlparse(issuer)
    return bool(
        parsed_endpoint.scheme == "https"
        and parsed_endpoint.netloc
        and parsed_endpoint.username is None
        and parsed_endpoint.password is None
        and parsed_endpoint.fragment == ""
        and parsed_endpoint.query == ""
        and parsed_endpoint.path
        and _origin(parsed_endpoint) == _origin(parsed_issuer)
    )


def _valid_end_session_redirect(
    redirect_to: str,
    *,
    endpoint: str,
    post_logout_redirect_uri: str,
) -> bool:
    parsed_redirect = urlparse(redirect_to)
    parsed_endpoint = urlparse(endpoint)
    return bool(
        parsed_redirect.scheme == "https"
        and parsed_redirect.netloc
        and parsed_redirect.username is None
        and parsed_redirect.password is None
        and parsed_redirect.fragment == ""
        and _origin(parsed_redirect) == _origin(parsed_endpoint)
        and parsed_redirect.path == parsed_endpoint.path
        and parse_qs(
            parsed_redirect.query,
            keep_blank_values=True,
        ).get("post_logout_redirect_uri")
        == [post_logout_redirect_uri]
    )


def _origin(parsed: ParseResult) -> tuple[str, str]:
    return parsed.scheme.casefold(), parsed.netloc.casefold()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
