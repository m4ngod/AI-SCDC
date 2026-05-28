from collections.abc import Generator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
