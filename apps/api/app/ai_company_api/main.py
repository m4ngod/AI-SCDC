from collections.abc import Generator

from fastapi import FastAPI
from sqlmodel import Session

from ai_company_api.api.routes import router
from ai_company_api.db.session import (
    build_engine,
    get_session_dependency,
    init_db,
    session_generator,
)
from ai_company_api.schemas.api import DevIdentity


def create_app(database_url: str = "sqlite:///./dev.db") -> FastAPI:
    engine = build_engine(database_url)
    init_db(engine)

    app = FastAPI(title="AI Company API")

    def session_dependency() -> Generator[Session, None, None]:
        yield from session_generator(engine)

    app.dependency_overrides[get_session_dependency] = session_dependency

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me")
    def me() -> DevIdentity:
        return DevIdentity(user_id="dev_user", workspace_id="dev_workspace")

    app.include_router(router)
    return app


app = create_app()
