from collections.abc import Generator
from contextlib import asynccontextmanager

from fastapi import FastAPI
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


DEV_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)
SECRET_REQUEST_FIELDS = {"secret_value"}
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
) -> FastAPI:
    engine = build_engine(database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        yield

    app = FastAPI(title="AI Company API", lifespan=lifespan)
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
    def me() -> DevIdentity:
        return DevIdentity(
            user_id="dev_user",
            workspace_id="dev_workspace",
            organization_id="dev_organization",
        )

    app.include_router(router)
    return app


app = create_app()
