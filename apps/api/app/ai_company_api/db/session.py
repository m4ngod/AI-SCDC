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
    _upgrade_sqlite_repository_phase_7_columns(engine)
    _upgrade_sqlite_planner_run_metadata(engine)
    _upgrade_sqlite_task_execution_constraints(engine)
    _upgrade_sqlite_patch_review_uniqueness(engine)


def _upgrade_sqlite_repository_phase_7_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    repository_columns = {
        "provider": "VARCHAR DEFAULT 'local'",
        "repo_url": "VARCHAR DEFAULT ''",
        "github_owner": "VARCHAR",
        "github_repo": "VARCHAR",
        "github_credential_id": "VARCHAR",
        "connection_status": "VARCHAR DEFAULT 'active'",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "repository" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(repository)")).mappings()
        }
        for column_name, column_type in repository_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE repository ADD COLUMN {column_name} {column_type}")
                )


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


def _upgrade_sqlite_task_execution_constraints(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    constraint_columns = {
        "allowed_paths": "JSON",
        "required_tests": "JSON",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "task" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(task)")).mappings()
        }
        for column_name, column_type in constraint_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE task ADD COLUMN {column_name} {column_type}")
                )


def _upgrade_sqlite_patch_review_uniqueness(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "patch_review" not in existing_tables:
            return

        columns = ("patch_artifact_id", "reviewer_kind")
        if _sqlite_has_unique_index(connection, "patch_review", columns):
            return

        _reclassify_sqlite_patch_review_duplicates(connection)

        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_patch_review_artifact_reviewer_kind "
                "ON patch_review (patch_artifact_id, reviewer_kind)"
            )
        )


def _reclassify_sqlite_patch_review_duplicates(connection) -> None:
    duplicate_candidates = list(
        connection.execute(
            text(
                """
                SELECT
                    id,
                    patch_artifact_id,
                    reviewer_kind,
                    ROW_NUMBER() OVER (
                        PARTITION BY patch_artifact_id, reviewer_kind
                        ORDER BY created_at, id
                    ) AS duplicate_position
                FROM patch_review
                ORDER BY patch_artifact_id, reviewer_kind, created_at, id
                """
            )
        ).mappings()
    )
    if not duplicate_candidates:
        return

    reviewer_kinds_by_artifact: dict[str, set[str]] = {}
    for row in duplicate_candidates:
        artifact_id = str(row["patch_artifact_id"])
        reviewer_kinds_by_artifact.setdefault(artifact_id, set()).add(
            str(row["reviewer_kind"])
        )

    for row in duplicate_candidates:
        if row["duplicate_position"] == 1:
            continue

        artifact_id = str(row["patch_artifact_id"])
        reviewer_kinds = reviewer_kinds_by_artifact[artifact_id]
        base_reviewer_kind = (
            f"legacy_duplicate:{row['reviewer_kind']}:{row['id']}"
        )
        reviewer_kind = base_reviewer_kind
        suffix = 2
        while reviewer_kind in reviewer_kinds:
            reviewer_kind = f"{base_reviewer_kind}:{suffix}"
            suffix += 1

        connection.execute(
            text(
                """
                UPDATE patch_review
                SET reviewer_kind = :reviewer_kind
                WHERE id = :id
                """
            ),
            {
                "id": row["id"],
                "reviewer_kind": reviewer_kind,
            },
        )
        reviewer_kinds.add(reviewer_kind)


def _sqlite_has_unique_index(connection, table_name: str, columns: tuple[str, ...]) -> bool:
    for row in connection.execute(text(f"PRAGMA index_list({table_name})")).mappings():
        if row["unique"] != 1:
            continue

        index_name = str(row["name"]).replace('"', '""')
        indexed_columns = tuple(
            column["name"]
            for column in connection.execute(
                text(f'PRAGMA index_info("{index_name}")')
            ).mappings()
        )
        if indexed_columns == columns:
            return True

    return False


def session_generator(engine) -> Generator[Session, None, None]:
    init_db(engine)
    with Session(engine) as session:
        yield session


def get_session_dependency() -> Generator[Session, None, None]:
    raise RuntimeError("Database session dependency must be overridden by create_app().")
