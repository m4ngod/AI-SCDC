from collections.abc import AsyncGenerator, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from ai_company_api.db.session import get_session_dependency
from ai_company_api.models.entities import (
    Organization,
    OrganizationMember,
    DeviceSession,
    User,
    Workspace,
    WorkspaceRole,
)
from ai_company_api.services.auth_policy import HumanCredentialType
from ai_company_api.services.browser_request_protection import (
    enforce_cookie_request_protection,
)
from ai_company_api.services.user_session_credentials import (
    USER_SESSION_COOKIE,
    USER_SESSION_PREVIOUS_SECRET_SECONDS,
    USER_SESSION_ROTATION_SECONDS,
    UserSessionCredentialRejected,
    hash_session_secret,
    resolve_user_session_credential,
)
from ai_company_api.services.identity_audit import record_identity_audit_event


DEV_USER_ID = "dev_user"
DEV_WORKSPACE_ID = "dev_workspace"
DEV_ORGANIZATION_ID = "dev_organization"
DEV_AUTH_MODE = "dev"
API_TOKEN_AUTH_MODE = "api_token"
USER_SESSION_AUTH_MODE = "user_session"
DEV_AUTH_HEADER_PREFIX = "x-ai-scdc-"

_current_auth_context: ContextVar["AuthContext | None"] = ContextVar(
    "ai_scdc_auth_context",
    default=None,
)


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    workspace_id: str
    organization_id: str
    roles: frozenset[WorkspaceRole]
    auth_mode: str
    device_session_id: str | None = None

    def has_any_role(self, roles: Iterable[WorkspaceRole]) -> bool:
        return bool(self.roles.intersection(set(roles)))


@contextmanager
def auth_context_scope(
    context: AuthContext | None,
) -> Iterator[AuthContext | None]:
    token = _current_auth_context.set(context)
    try:
        yield context
    finally:
        _current_auth_context.reset(token)


def get_current_auth_context() -> AuthContext | None:
    return _current_auth_context.get()


def require_current_auth_context() -> AuthContext:
    context = get_current_auth_context()
    if context is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required",
        )
    return context


def current_user_id() -> str:
    return require_current_auth_context().user_id


def current_workspace_id() -> str:
    return require_current_auth_context().workspace_id


def current_organization_id() -> str:
    return require_current_auth_context().organization_id


def enforce_workspace_access(
    workspace_id: str,
    *,
    detail: str = "Resource not found",
) -> None:
    context = get_current_auth_context()
    if context is not None and workspace_id != context.workspace_id:
        raise HTTPException(status_code=404, detail=detail)


def require_workspace_role(
    allowed_roles: Iterable[WorkspaceRole],
    *,
    detail: str = "Insufficient workspace role",
) -> AuthContext:
    context = require_current_auth_context()
    if not context.has_any_role(allowed_roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    return context


def hash_api_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


async def get_auth_context_dependency(
    request: Request,
    session: Session = Depends(get_session_dependency),
) -> AsyncGenerator[AuthContext | None, None]:
    async for context in _auth_context_dependency(
        request,
        session,
        allow_workspace_selection_recovery=False,
    ):
        yield context


async def get_workspace_selection_auth_context_dependency(
    request: Request,
    session: Session = Depends(get_session_dependency),
) -> AsyncGenerator[AuthContext | None, None]:
    async for context in _auth_context_dependency(
        request,
        session,
        allow_workspace_selection_recovery=True,
    ):
        yield context


async def _auth_context_dependency(
    request: Request,
    session: Session,
    *,
    allow_workspace_selection_recovery: bool,
) -> AsyncGenerator[AuthContext | None, None]:
    if _is_worker_callback_path(request.url.path):
        with auth_context_scope(None):
            yield None
        return

    context = _resolve_auth_context(
        request,
        session,
        allow_workspace_selection_recovery=(
            allow_workspace_selection_recovery
        ),
    )
    if context.auth_mode == USER_SESSION_AUTH_MODE:
        enforce_cookie_request_protection(
            request,
            session,
            now=request.app.state.identity_clock(),
        )
    with auth_context_scope(context):
        yield context


def _resolve_auth_context(
    request: Request,
    session: Session,
    *,
    allow_workspace_selection_recovery: bool = False,
) -> AuthContext:
    authentication_policy = request.app.state.authentication_policy
    accepted_credentials = authentication_policy.accepted_human_credentials

    if (
        USER_SESSION_COOKIE in request.cookies
        and _has_bearer_credential(request)
    ):
        correlation_id = _record_authentication_failure(
            session,
            reason_code="ambiguous_credentials",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ambiguous_credentials",
            headers={"X-Correlation-ID": correlation_id},
        )

    if "authorization" in request.headers:
        if HumanCredentialType.WORKSPACE_API_TOKEN not in accepted_credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Workspace API token authentication is not allowed",
            )
        return _api_token_auth_context_or_audit(request, session)

    if _has_dev_auth_headers(request):
        if HumanCredentialType.DEV_AUTH not in accepted_credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Dev Auth is not allowed",
            )
        return _dev_auth_context(request)

    if USER_SESSION_COOKIE in request.cookies:
        if HumanCredentialType.USER_SESSION not in accepted_credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User Session authentication is not allowed",
            )
        return _user_session_auth_context_or_fail_closed(
            request,
            session,
            allow_workspace_selection_recovery=(
                allow_workspace_selection_recovery
            ),
        )

    if (
        HumanCredentialType.DEV_AUTH
        in accepted_credentials
    ):
        return _dev_auth_context(request)
    if (
        HumanCredentialType.USER_SESSION
        in accepted_credentials
    ):
        return _user_session_auth_context_or_fail_closed(
            request,
            session,
            allow_workspace_selection_recovery=(
                allow_workspace_selection_recovery
            ),
        )
    if (
        HumanCredentialType.WORKSPACE_API_TOKEN
        in accepted_credentials
    ):
        return _api_token_auth_context_or_audit(request, session)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Authentication policy cannot resolve human credentials",
    )


def _has_bearer_credential(request: Request) -> bool:
    authorization = request.headers.get("authorization")
    if authorization is None:
        return False
    scheme, _separator, _credential = authorization.strip().partition(" ")
    return scheme.casefold() == "bearer"


def _has_dev_auth_headers(request: Request) -> bool:
    return any(
        header_name.lower().startswith(DEV_AUTH_HEADER_PREFIX)
        for header_name in request.headers
    )


def _dev_auth_context(request: Request) -> AuthContext:
    return AuthContext(
        user_id=_dev_header_value(request, "user-id", DEV_USER_ID),
        workspace_id=_dev_header_value(request, "workspace-id", DEV_WORKSPACE_ID),
        organization_id=_dev_header_value(
            request,
            "organization-id",
            DEV_ORGANIZATION_ID,
        ),
        roles=_parse_roles(request.headers.get(f"{DEV_AUTH_HEADER_PREFIX}roles")),
        auth_mode=DEV_AUTH_MODE,
    )


def _dev_header_value(request: Request, name: str, default: str) -> str:
    value = request.headers.get(f"{DEV_AUTH_HEADER_PREFIX}{name}", default).strip()
    if value == "":
        raise HTTPException(status_code=400, detail=f"Invalid dev auth {name}")
    return value


def _parse_roles(value: str | None) -> frozenset[WorkspaceRole]:
    if value is None or value.strip() == "":
        return frozenset({WorkspaceRole.OWNER})
    roles: set[WorkspaceRole] = set()
    for raw_role in value.split(","):
        role = raw_role.strip()
        if role == "":
            continue
        try:
            roles.add(WorkspaceRole(role))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid workspace role") from exc
    if not roles:
        raise HTTPException(status_code=400, detail="Invalid workspace role")
    return frozenset(roles)


def _api_token_auth_context(request: Request, session: Session) -> AuthContext:
    token = _bearer_token(request)
    token_hash = hash_api_token(token)
    member = session.exec(
        select(OrganizationMember).where(
            OrganizationMember.api_token_hash == token_hash,
            OrganizationMember.status == "active",
        )
    ).first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )
    _ensure_active_membership_scope(session, member)
    return AuthContext(
        user_id=member.user_id,
        workspace_id=member.workspace_id,
        organization_id=member.organization_id,
        roles=frozenset({_role_value(member.role)}),
        auth_mode=API_TOKEN_AUTH_MODE,
    )


def _api_token_auth_context_or_audit(
    request: Request,
    session: Session,
) -> AuthContext:
    try:
        return _api_token_auth_context(request, session)
    except HTTPException as exc:
        reason_code = (
            "workspace_api_token_required"
            if exc.detail == "Bearer API token is required"
            else "invalid_workspace_api_token"
        )
        correlation_id = _record_authentication_failure(
            session,
            reason_code=reason_code,
        )
        headers = dict(exc.headers or {})
        headers["X-Correlation-ID"] = correlation_id
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers=headers,
        ) from None


def _user_session_auth_context(
    request: Request,
    session: Session,
    *,
    allow_workspace_selection_recovery: bool = False,
) -> AuthContext:
    now = _as_utc(request.app.state.identity_clock())
    cookie_value = request.cookies.get(USER_SESSION_COOKIE, "")
    try:
        resolved_credential = resolve_user_session_credential(
            session,
            cookie_value=cookie_value,
            now=now,
        )
    except UserSessionCredentialRejected as exc:
        correlation_id = exc.correlation_id
        if correlation_id is None:
            correlation_id = _record_authentication_failure(
                session,
                reason_code=exc.reason_code,
            )
        _invalid_user_session(correlation_id=correlation_id)
    device_session = resolved_credential.device_session
    if (
        resolved_credential.uses_current_secret
        and _as_utc(device_session.secret_rotated_at)
        + timedelta(seconds=USER_SESSION_ROTATION_SECONDS)
        <= now
    ):
        device_session = _rotate_user_session_credential(
            session,
            request=request,
            device_session=device_session,
            presented_secret_hash=(
                resolved_credential.presented_secret_hash
            ),
            now=now,
        )
    user = session.get(User, device_session.user_id)
    request.state.authenticated_device_session = device_session
    if user is None or user.status != "active":
        correlation_id = _record_authentication_failure(
            session,
            reason_code="user_session_user_inactive",
            user_id=device_session.user_id,
            device_session_id=device_session.id,
        )
        _invalid_user_session(correlation_id=correlation_id)
    workspace = session.get(Workspace, device_session.active_workspace_id)
    organization = session.get(
        Organization,
        device_session.active_organization_id,
    )
    member = session.exec(
        select(OrganizationMember).where(
            OrganizationMember.user_id == device_session.user_id,
            OrganizationMember.workspace_id == device_session.active_workspace_id,
            OrganizationMember.organization_id
            == device_session.active_organization_id,
            OrganizationMember.status == "active",
        )
    ).first()
    if (
        workspace is None
        or organization is None
        or member is None
        or workspace.status != "active"
        or organization.status != "active"
        or workspace.organization_id != organization.id
    ):
        request.state.workspace_selection_required = True
        if not allow_workspace_selection_recovery:
            correlation_id = secrets.token_hex(16)
            record_identity_audit_event(
                session,
                event_type="workspace_authorization_denied",
                outcome="failure",
                reason_code="workspace_access_lost",
                correlation_id=correlation_id,
                user_id=device_session.user_id,
                device_session_id=device_session.id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="workspace_access_lost",
                headers={"X-Correlation-ID": correlation_id},
            )
        context = AuthContext(
            user_id=user.id,
            workspace_id=device_session.active_workspace_id,
            organization_id=device_session.active_organization_id,
            roles=frozenset(),
            auth_mode=USER_SESSION_AUTH_MODE,
            device_session_id=device_session.id,
        )
    else:
        request.state.workspace_selection_required = False
        context = AuthContext(
            user_id=user.id,
            workspace_id=workspace.id,
            organization_id=organization.id,
            roles=frozenset({_role_value(member.role)}),
            auth_mode=USER_SESSION_AUTH_MODE,
            device_session_id=device_session.id,
        )
    if _as_utc(device_session.last_seen_at) + timedelta(hours=1) <= now:
        correlation_id = secrets.token_hex(16)
        result = session.execute(
            update(DeviceSession)
            .where(
                DeviceSession.id == device_session.id,
                DeviceSession.status == "active",
                DeviceSession.last_seen_at == device_session.last_seen_at,
            )
            .values(
                last_seen_at=now,
                idle_expires_at=now + timedelta(days=30),
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            record_identity_audit_event(
                session,
                event_type="session_activity_renewed",
                outcome="success",
                reason_code="hourly_activity_checkpoint",
                correlation_id=correlation_id,
                user_id=device_session.user_id,
                device_session_id=device_session.id,
                commit=False,
            )
            session.commit()
            request.state.identity_correlation_id = correlation_id
        else:
            session.rollback()
    return context


def _invalid_user_session(*, correlation_id: str | None = None) -> None:
    headers = (
        {"X-Correlation-ID": correlation_id}
        if correlation_id is not None
        else None
    )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="User Session is not valid",
        headers=headers,
    )


def _record_authentication_failure(
    session: Session,
    *,
    reason_code: str,
    user_id: str | None = None,
    device_session_id: str | None = None,
) -> str:
    correlation_id = secrets.token_hex(16)
    record_identity_audit_event(
        session,
        event_type="authentication_failure",
        outcome="failure",
        reason_code=reason_code,
        correlation_id=correlation_id,
        user_id=user_id,
        device_session_id=device_session_id,
    )
    return correlation_id


def _user_session_auth_context_or_fail_closed(
    request: Request,
    session: Session,
    *,
    allow_workspace_selection_recovery: bool = False,
) -> AuthContext:
    try:
        if request.app.state.user_session_database_failure:
            raise SQLAlchemyError("Injected User Session database failure")
        return _user_session_auth_context(
            request,
            session,
            allow_workspace_selection_recovery=(
                allow_workspace_selection_recovery
            ),
        )
    except SQLAlchemyError:
        correlation_id = secrets.token_hex(16)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User Session database is unavailable",
            headers={"X-Correlation-ID": correlation_id},
        ) from None


def _rotate_user_session_credential(
    session: Session,
    *,
    request: Request,
    device_session: DeviceSession,
    presented_secret_hash: str,
    now: datetime,
) -> DeviceSession:
    new_secret = secrets.token_urlsafe(32)
    new_secret_hash = hash_session_secret(new_secret)
    correlation_id = secrets.token_hex(16)
    update_values: dict[str, object] = {
        "secret_hash": new_secret_hash,
        "previous_secret_hash": presented_secret_hash,
        "previous_secret_valid_until": now
        + timedelta(seconds=USER_SESSION_PREVIOUS_SECRET_SECONDS),
        "secret_rotated_at": now,
        "updated_at": now,
    }
    if _as_utc(device_session.last_seen_at) + timedelta(hours=1) <= now:
        update_values["last_seen_at"] = now
        update_values["idle_expires_at"] = now + timedelta(days=30)
    result = session.execute(
        update(DeviceSession)
        .where(
            DeviceSession.id == device_session.id,
            DeviceSession.status == "active",
            DeviceSession.secret_hash == presented_secret_hash,
            DeviceSession.secret_rotated_at == device_session.secret_rotated_at,
        )
        .values(**update_values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        session.expire_all()
        current = session.get(DeviceSession, device_session.id)
        if (
            current is not None
            and current.status == "active"
            and current.previous_secret_hash is not None
            and hmac.compare_digest(
                current.previous_secret_hash,
                presented_secret_hash,
            )
            and current.previous_secret_valid_until is not None
            and _as_utc(current.previous_secret_valid_until) >= now
        ):
            return current
        _invalid_user_session()
    record_identity_audit_event(
        session,
        event_type="session_credential_rotated",
        outcome="success",
        reason_code="scheduled_rotation",
        correlation_id=correlation_id,
        user_id=device_session.user_id,
        device_session_id=device_session.id,
        commit=False,
    )
    session.commit()
    request.state.user_session_cookie_rotation = (
        device_session.id,
        new_secret,
    )
    request.state.identity_correlation_id = correlation_id
    session.expire_all()
    current = session.get(DeviceSession, device_session.id)
    if current is None:
        _invalid_user_session()
    return current


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ensure_active_membership_scope(
    session: Session,
    member: OrganizationMember,
) -> None:
    user = session.get(User, member.user_id)
    workspace = session.get(Workspace, member.workspace_id)
    organization = session.get(Organization, member.organization_id)
    if (
        user is None
        or workspace is None
        or organization is None
        or user.status != "active"
        or workspace.status != "active"
        or organization.status != "active"
        or workspace.organization_id != organization.id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
        )


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer API token is required",
        )
    token = authorization.removeprefix(prefix).strip()
    if token == "":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer API token is required",
        )
    return token


def _role_value(role: WorkspaceRole | str) -> WorkspaceRole:
    if isinstance(role, WorkspaceRole):
        return role
    return WorkspaceRole(role)


def _is_worker_callback_path(path: str) -> bool:
    return path.startswith("/cloud-run-worker/")
