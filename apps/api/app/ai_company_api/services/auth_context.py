from collections.abc import AsyncGenerator, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from ai_company_api.db.session import get_session_dependency
from ai_company_api.models.entities import (
    Organization,
    OrganizationMember,
    User,
    Workspace,
    WorkspaceRole,
)


DEV_USER_ID = "dev_user"
DEV_WORKSPACE_ID = "dev_workspace"
DEV_ORGANIZATION_ID = "dev_organization"
DEV_AUTH_MODE = "dev"
API_TOKEN_AUTH_MODE = "api_token"
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

    def has_any_role(self, roles: Iterable[WorkspaceRole]) -> bool:
        return bool(self.roles.intersection(set(roles)))


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


def current_user_id(default: str = DEV_USER_ID) -> str:
    context = get_current_auth_context()
    return context.user_id if context is not None else default


def current_workspace_id(default: str = DEV_WORKSPACE_ID) -> str:
    context = get_current_auth_context()
    return context.workspace_id if context is not None else default


def current_organization_id(default: str = DEV_ORGANIZATION_ID) -> str:
    context = get_current_auth_context()
    return context.organization_id if context is not None else default


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
    if _is_worker_callback_path(request.url.path):
        token = _current_auth_context.set(None)
        try:
            yield None
        finally:
            _current_auth_context.reset(token)
        return

    context = _resolve_auth_context(request, session)
    token = _current_auth_context.set(context)
    try:
        yield context
    finally:
        _current_auth_context.reset(token)


def _resolve_auth_context(request: Request, session: Session) -> AuthContext:
    auth_mode = getattr(request.app.state, "auth_mode", DEV_AUTH_MODE)
    if auth_mode == DEV_AUTH_MODE:
        return _dev_auth_context(request)
    if auth_mode == API_TOKEN_AUTH_MODE:
        return _api_token_auth_context(request, session)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unsupported authentication mode",
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
