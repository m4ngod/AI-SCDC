from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import datetime
import os
from typing import Callable

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlmodel import Session
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_company_api.api.identity_routes import router as identity_router
from ai_company_api.api.routes import router
from ai_company_api.db.session import (
    build_engine,
    get_session_dependency,
    init_db,
    session_generator,
)
from ai_company_api.schemas.api import CurrentAccount, CurrentWorkspace, DevIdentity
from ai_company_api.models.entities import (
    AccountKind,
    Organization,
    User,
    Workspace,
    utc_now,
)
from ai_company_api.services.auth_context import (
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    AuthContext,
    get_auth_context_dependency,
)
from ai_company_api.services.auth_policy import (
    AUTHENTICATION_ENVIRONMENT_ENV,
    AuthenticationEnvironment,
    AuthenticationPolicy,
    HumanCredentialType,
    authentication_policy_for_environment,
)
from ai_company_api.services.customer_identity_provider import CustomerIdentityProvider
from ai_company_api.services.identity_login import (
    PERSONAL_ONBOARDING_FAILURE_STEPS,
)
from ai_company_api.services.user_session_credentials import (
    USER_SESSION_COOKIE,
    USER_SESSION_IDLE_SECONDS,
)


DEV_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
SECRET_REQUEST_FIELDS = {"secret_value", "token"}
REDACTED_SECRET_INPUT = "[redacted]"


class UserSessionResponseStateMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def apply_response_state(message: Message) -> None:
            if message["type"] == "http.response.start":
                state = scope.get("state", {})
                rotation = state.get("user_session_cookie_rotation")
                if rotation is not None:
                    device_session_id, session_secret = rotation
                    cookie_response = Response()
                    cookie_response.set_cookie(
                        key=USER_SESSION_COOKIE,
                        value=f"{device_session_id}.{session_secret}",
                        max_age=USER_SESSION_IDLE_SECONDS,
                        secure=True,
                        httponly=True,
                        samesite="lax",
                        path="/",
                    )
                    headers = MutableHeaders(scope=message)
                    for name, value in cookie_response.raw_headers:
                        if name == b"set-cookie":
                            headers.append(
                                "set-cookie",
                                value.decode("latin-1"),
                            )
                correlation_id = state.get("identity_correlation_id")
                headers = MutableHeaders(scope=message)
                if (
                    correlation_id is not None
                    and "x-correlation-id" not in headers
                ):
                    headers["x-correlation-id"] = correlation_id
            await send(message)

        await self.app(scope, receive, apply_response_state)


class AICompanyFastAPI(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        return UserSessionResponseStateMiddleware(
            super().build_middleware_stack(),
        )


def redact_secret_validation_input(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: REDACTED_SECRET_INPUT
            if str(key).lower() in SECRET_REQUEST_FIELDS
            else redact_secret_validation_input(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_validation_input(item) for item in value]
    return value


def validation_error_contains_secret_field(error: dict[str, object]) -> bool:
    location = error.get("loc", ())
    return any(str(part).lower() in SECRET_REQUEST_FIELDS for part in location)


def redact_validation_errors(
    errors: list[dict[str, object]],
) -> list[dict[str, object]]:
    redacted_errors = []
    for error in errors:
        redacted_error = dict(error)
        if "input" in redacted_error:
            if validation_error_contains_secret_field(redacted_error):
                redacted_error["input"] = REDACTED_SECRET_INPUT
            else:
                redacted_error["input"] = redact_secret_validation_input(
                    redacted_error["input"],
                )
        redacted_errors.append(redacted_error)
    return redacted_errors


def create_app(
    database_url: str = "sqlite:///./dev.db",
    cors_origins: tuple[str, ...] = DEV_CORS_ORIGINS,
    authentication_policy: AuthenticationPolicy | None = None,
    customer_identity_provider: CustomerIdentityProvider | None = None,
    allowed_login_return_destinations: frozenset[str] = frozenset({"/"}),
    public_origin: str = "https://localhost",
    identity_audit_observer_enabled: bool = False,
    login_transaction_ttl_seconds: int = 600,
    personal_onboarding_failure_step: str | None = None,
    identity_operator_user_ids: frozenset[str] = frozenset(),
    identity_clock: Callable[[], datetime] = utc_now,
    user_session_database_failure: bool = False,
) -> FastAPI:
    resolved_authentication_policy = (
        authentication_policy
        if authentication_policy is not None
        else authentication_policy_for_environment(
            os.getenv(AUTHENTICATION_ENVIRONMENT_ENV)
        )
    )
    if (
        HumanCredentialType.USER_SESSION
        in resolved_authentication_policy.accepted_human_credentials
        and customer_identity_provider is None
    ):
        raise ValueError(
            "Customer Identity Provider is required when User Sessions are enabled"
        )
    if (
        identity_audit_observer_enabled
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "Identity Audit observer is allowed only in the test environment"
        )
    if personal_onboarding_failure_step is not None:
        if (
            resolved_authentication_policy.environment
            != AuthenticationEnvironment.TEST
        ):
            raise ValueError(
                "Personal onboarding failure injection is allowed only "
                "in the test environment"
            )
        if (
            personal_onboarding_failure_step
            not in PERSONAL_ONBOARDING_FAILURE_STEPS
        ):
            raise ValueError("Unsupported Personal onboarding failure step")
    if (
        user_session_database_failure
        and resolved_authentication_policy.environment
        != AuthenticationEnvironment.TEST
    ):
        raise ValueError(
            "User Session database failure injection is allowed only "
            "in the test environment"
        )
    engine = build_engine(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        if (
            HumanCredentialType.DEV_AUTH
            in resolved_authentication_policy.accepted_human_credentials
        ):
            _ensure_dev_auth_scope(engine)
        yield

    app = AICompanyFastAPI(title="AI Company API", lifespan=lifespan)

    app.state.authentication_policy = resolved_authentication_policy
    app.state.customer_identity_provider = customer_identity_provider
    app.state.allowed_login_return_destinations = allowed_login_return_destinations
    app.state.public_origin = public_origin.rstrip("/")
    app.state.identity_audit_observer_enabled = identity_audit_observer_enabled
    app.state.login_transaction_ttl_seconds = login_transaction_ttl_seconds
    app.state.personal_onboarding_failure_step = (
        personal_onboarding_failure_step
    )
    app.state.identity_operator_user_ids = identity_operator_user_ids
    app.state.identity_clock = identity_clock
    app.state.user_session_database_failure = user_session_database_failure
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def redact_secret_validation_errors(
        _request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder({"detail": redact_validation_errors(exc.errors())}),
        )

    def session_dependency() -> Generator[Session, None, None]:
        yield from session_generator(engine)

    app.dependency_overrides[get_session_dependency] = session_dependency

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me")
    def me(
        auth: AuthContext = Depends(get_auth_context_dependency),
        session: Session = Depends(get_session_dependency),
    ) -> DevIdentity:
        account = session.get(Organization, auth.organization_id)
        workspace = session.get(Workspace, auth.workspace_id)
        return DevIdentity(
            user_id=auth.user_id,
            workspace_id=auth.workspace_id,
            organization_id=auth.organization_id,
            roles=sorted(role.value for role in auth.roles),
            auth_mode=auth.auth_mode,
            current_account=CurrentAccount(
                id=auth.organization_id,
                name=account.name if account is not None else auth.organization_id,
                kind=(
                    account.account_kind
                    if account is not None
                    else AccountKind.LEGACY
                ),
            ),
            current_workspace=CurrentWorkspace(
                id=auth.workspace_id,
                name=workspace.name if workspace is not None else auth.workspace_id,
            ),
        )

    app.include_router(identity_router)
    app.include_router(router, dependencies=[Depends(get_auth_context_dependency)])
    return app


def _ensure_dev_auth_scope(engine) -> None:
    with Session(engine) as session:
        if session.get(User, DEV_USER_ID) is None:
            session.add(
                User(
                    id=DEV_USER_ID,
                    email="dev@localhost",
                    display_name="Local developer",
                )
            )
        if session.get(Organization, DEV_ORGANIZATION_ID) is None:
            session.add(
                Organization(
                    id=DEV_ORGANIZATION_ID,
                    name="Local development account",
                )
            )
        if session.get(Workspace, DEV_WORKSPACE_ID) is None:
            session.add(
                Workspace(
                    id=DEV_WORKSPACE_ID,
                    organization_id=DEV_ORGANIZATION_ID,
                    name="Local development workspace",
                )
            )
        session.commit()


app = create_app()
