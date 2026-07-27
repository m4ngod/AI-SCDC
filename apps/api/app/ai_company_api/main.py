from collections.abc import Generator
from contextlib import asynccontextmanager
import os

from fastapi import Depends, FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session

from ai_company_api.api.routes import router
from ai_company_api.db.session import (
    build_engine,
    get_session_dependency,
    init_db,
    session_generator,
)
from ai_company_api.schemas.api import DevIdentity
from ai_company_api.models.entities import Organization, User, Workspace
from ai_company_api.services.auth_context import (
    DEV_ORGANIZATION_ID,
    DEV_USER_ID,
    DEV_WORKSPACE_ID,
    AuthContext,
    get_auth_context_dependency,
)
from ai_company_api.services.auth_policy import (
    AUTHENTICATION_ENVIRONMENT_ENV,
    AuthenticationPolicy,
    HumanCredentialType,
    authentication_policy_for_environment,
)


DEV_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
SECRET_REQUEST_FIELDS = {"secret_value", "token"}
REDACTED_SECRET_INPUT = "[redacted]"


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
) -> FastAPI:
    resolved_authentication_policy = (
        authentication_policy
        if authentication_policy is not None
        else authentication_policy_for_environment(
            os.getenv(AUTHENTICATION_ENVIRONMENT_ENV)
        )
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

    app = FastAPI(title="AI Company API", lifespan=lifespan)
    app.state.authentication_policy = resolved_authentication_policy
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
    def me(auth: AuthContext = Depends(get_auth_context_dependency)) -> DevIdentity:
        return DevIdentity(
            user_id=auth.user_id,
            workspace_id=auth.workspace_id,
            organization_id=auth.organization_id,
            roles=sorted(role.value for role in auth.roles),
            auth_mode=auth.auth_mode,
        )

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
