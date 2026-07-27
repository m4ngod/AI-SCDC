from datetime import datetime
import secrets
from urllib.parse import ParseResult, parse_qs, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlmodel import Session

from ai_company_api.models.entities import DeviceSession
from ai_company_api.services.customer_identity_provider import (
    CustomerIdentityProvider,
    CustomerIdentityProviderError,
    CustomerIdentityProviderUnavailable,
)
from ai_company_api.services.identity_audit import record_identity_audit_event
from ai_company_api.services.user_session_credentials import USER_SESSION_COOKIE


def sign_out_current_device(
    session: Session,
    *,
    request: Request,
    provider: CustomerIdentityProvider,
    device_session: DeviceSession,
    now: datetime,
) -> JSONResponse:
    correlation_id = secrets.token_hex(16)
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
    )
    record_identity_audit_event(
        session,
        event_type="provider_logout",
        outcome=provider_outcome,
        reason_code=provider_reason,
        correlation_id=correlation_id,
        user_id=device_session.user_id,
        device_session_id=device_session.id,
    )

    response = JSONResponse({"redirect_to": redirect_to})
    response.delete_cookie(
        key=USER_SESSION_COOKIE,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.headers["X-Correlation-ID"] = correlation_id
    return response


def _prepare_end_session_redirect(
    provider: CustomerIdentityProvider,
    *,
    post_logout_redirect_uri: str,
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
