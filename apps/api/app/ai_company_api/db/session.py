from collections.abc import Generator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine


def build_engine(database_url: str):
    connect_args = {}
    engine_args = {}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    if database_url == "sqlite://":
        engine_args["poolclass"] = StaticPool

    return create_engine(database_url, connect_args=connect_args, **engine_args)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_generator(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def get_session_dependency() -> Generator[Session, None, None]:
    raise RuntimeError("Database session dependency must be overridden by create_app().")
