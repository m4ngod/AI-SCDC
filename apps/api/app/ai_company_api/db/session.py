from collections.abc import Generator

from sqlalchemy import text
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
    _upgrade_sqlite_planner_run_metadata(engine)


def _upgrade_sqlite_planner_run_metadata(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    metadata_columns = {
        "model_route_id": "VARCHAR",
        "model_provider_name": "VARCHAR",
        "model_name": "VARCHAR",
        "fallback_reason": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(planner_run)")).mappings()
        }
        for column_name, column_type in metadata_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE planner_run ADD COLUMN {column_name} {column_type}")
                )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_planner_run_model_route_id "
                "ON planner_run (model_route_id)"
            )
        )


def session_generator(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def get_session_dependency() -> Generator[Session, None, None]:
    raise RuntimeError("Database session dependency must be overridden by create_app().")
