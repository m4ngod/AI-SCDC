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
    _upgrade_sqlite_cloud_run_phase_9_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10a_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10b_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10d_columns(engine)
    _upgrade_sqlite_cloud_run_phase_12a_columns(engine)
    SQLModel.metadata.create_all(engine)
    _upgrade_sqlite_repository_phase_7_columns(engine)
    _upgrade_sqlite_cloud_run_phase_8_columns(engine)
    _upgrade_sqlite_local_test_run_nullable_patch_artifact(engine)
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

        for column_name in (
            "provider",
            "github_owner",
            "github_repo",
            "github_credential_id",
            "connection_status",
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_repository_{column_name} "
                    f"ON repository ({column_name})"
                )
            )


def _upgrade_sqlite_cloud_run_phase_8_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "sandbox_profile_id": "VARCHAR",
        "patch_command_key": "VARCHAR",
        "test_command_keys": "JSON",
        "command_results": "JSON",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cloud_run_sandbox_profile_id "
                "ON cloud_run (sandbox_profile_id)"
            )
        )


def _upgrade_sqlite_cloud_run_phase_9_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "cancel_requested": "BOOLEAN NOT NULL DEFAULT 0",
        "cancel_requested_at": "DATETIME",
        "cancelled_at": "DATETIME",
        "worker_id": "VARCHAR",
        "claimed_at": "DATETIME",
        "completed_at": "DATETIME",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cloud_run_cancel_requested "
                "ON cloud_run (cancel_requested)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_cloud_run_worker_id "
                "ON cloud_run (worker_id)"
            )
        )


def _upgrade_sqlite_cloud_run_phase_10a_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "queue_provider": "VARCHAR NOT NULL DEFAULT 'local_db'",
        "remote_worker_kind": "VARCHAR",
        "lease_id": "VARCHAR",
        "lease_expires_at": "DATETIME",
        "heartbeat_at": "DATETIME",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
        "max_attempts": "INTEGER NOT NULL DEFAULT 3",
        "last_queue_error": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )

        for column_name in (
            "queue_provider",
            "remote_worker_kind",
            "lease_id",
            "lease_expires_at",
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_{column_name} "
                    f"ON cloud_run ({column_name})"
                )
            )


def _upgrade_sqlite_cloud_run_phase_10b_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "queue_message_id": "VARCHAR",
        "queue_receipt": "VARCHAR",
        "runtime_provider": "VARCHAR",
        "runtime_job_id": "VARCHAR",
        "storage_provider": "VARCHAR",
        "artifact_manifest_uri": "VARCHAR",
        "log_stream_uri": "VARCHAR",
        "external_status": "VARCHAR",
        "external_error": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )

        for column_name in (
            "queue_message_id",
            "runtime_provider",
            "runtime_job_id",
            "storage_provider",
            "external_status",
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_{column_name} "
                    f"ON cloud_run ({column_name})"
                )
            )


def _upgrade_sqlite_cloud_run_phase_10d_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "callback_token_hash": "VARCHAR",
        "callback_token_expires_at": "DATETIME",
        "callback_token_used_at": "DATETIME",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )

        for column_name in ("callback_token_hash", "callback_token_expires_at"):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_{column_name} "
                    f"ON cloud_run ({column_name})"
                )
            )


def _upgrade_sqlite_cloud_run_phase_12a_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "artifact_manifest_sha256": "VARCHAR",
        "artifact_manifest_size_bytes": "INTEGER",
        "artifact_manifest_content_type": "VARCHAR",
        "log_stream_sha256": "VARCHAR",
        "log_stream_size_bytes": "INTEGER",
        "log_stream_content_type": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(text("PRAGMA table_info(cloud_run)")).mappings()
        }
        for column_name, column_type in cloud_run_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE cloud_run ADD COLUMN {column_name} {column_type}")
                )


def _upgrade_sqlite_local_test_run_nullable_patch_artifact(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    raw_connection = engine.raw_connection()
    try:
        raw_connection.rollback()
        cursor = raw_connection.cursor()
        existing_tables = {
            row[0]
            for row in cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "local_test_run" not in existing_tables:
            return

        columns = {
            row[1]: row
            for row in cursor.execute("PRAGMA table_info(local_test_run)").fetchall()
        }
        patch_artifact_column = columns.get("patch_artifact_id")
        if patch_artifact_column is None or patch_artifact_column[3] == 0:
            return

        foreign_keys = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
        legacy_alter_table = cursor.execute(
            "PRAGMA legacy_alter_table"
        ).fetchone()[0]
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("PRAGMA legacy_alter_table=ON")
        try:
            cursor.execute(
                "ALTER TABLE local_test_run "
                "RENAME TO local_test_run_notnull_legacy"
            )
            cursor.execute(
                """
                CREATE TABLE local_test_run (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    workspace_id VARCHAR NOT NULL,
                    project_id VARCHAR NOT NULL,
                    task_id VARCHAR NOT NULL,
                    local_run_id VARCHAR NOT NULL,
                    patch_artifact_id VARCHAR,
                    status VARCHAR NOT NULL,
                    commands JSON NOT NULL,
                    command_results JSON NOT NULL,
                    failure_reason VARCHAR,
                    started_at DATETIME NOT NULL,
                    completed_at DATETIME,
                    created_at DATETIME NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES project (id),
                    FOREIGN KEY(task_id) REFERENCES task (id),
                    FOREIGN KEY(local_run_id) REFERENCES local_task_run (id),
                    FOREIGN KEY(patch_artifact_id) REFERENCES patch_artifact (id)
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO local_test_run (
                    id,
                    workspace_id,
                    project_id,
                    task_id,
                    local_run_id,
                    patch_artifact_id,
                    status,
                    commands,
                    command_results,
                    failure_reason,
                    started_at,
                    completed_at,
                    created_at
                )
                SELECT
                    id,
                    workspace_id,
                    project_id,
                    task_id,
                    local_run_id,
                    patch_artifact_id,
                    status,
                    commands,
                    command_results,
                    failure_reason,
                    started_at,
                    completed_at,
                    created_at
                FROM local_test_run_notnull_legacy
                """
            )
            cursor.execute("DROP TABLE local_test_run_notnull_legacy")
            for column_name in (
                "workspace_id",
                "project_id",
                "task_id",
                "local_run_id",
                "patch_artifact_id",
                "status",
                "created_at",
            ):
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_local_test_run_{column_name} "
                    f"ON local_test_run ({column_name})"
                )
            raw_connection.commit()
        except Exception:
            raw_connection.rollback()
            raise
        finally:
            cursor.execute(f"PRAGMA legacy_alter_table={int(legacy_alter_table or 0)}")
            cursor.execute(f"PRAGMA foreign_keys={int(foreign_keys or 0)}")
    finally:
        raw_connection.close()


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
    with Session(engine) as session:
        yield session


def get_session_dependency() -> Generator[Session, None, None]:
    raise RuntimeError("Database session dependency must be overridden by create_app().")
