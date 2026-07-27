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
    _upgrade_sqlite_account_classification(engine)
    _upgrade_sqlite_identity_recovery_columns(engine)
    _upgrade_sqlite_user_session_columns(engine)
    _upgrade_sqlite_cloud_run_phase_9_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10a_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10b_columns(engine)
    _upgrade_sqlite_cloud_run_phase_10d_columns(engine)
    _upgrade_sqlite_cloud_run_phase_12a_columns(engine)
    _upgrade_sqlite_cloud_run_stored_object_phase_12d_columns(engine)
    _upgrade_sqlite_cloud_run_phase_13c_cost_columns(engine)
    _upgrade_sqlite_usage_ledger_phase_13c_columns(engine)
    _upgrade_sqlite_usage_ledger_phase_13c_unique_cloud_run_usage(engine)
    SQLModel.metadata.create_all(engine)
    _upgrade_sqlite_repository_phase_7_columns(engine)
    _upgrade_sqlite_cloud_run_phase_8_columns(engine)
    _upgrade_sqlite_local_test_run_nullable_patch_artifact(engine)
    _upgrade_sqlite_planner_run_metadata(engine)
    _upgrade_sqlite_task_execution_constraints(engine)
    _upgrade_sqlite_patch_review_uniqueness(engine)


def _upgrade_sqlite_user_session_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "device_session" not in existing_tables:
            return
        existing_columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(device_session)")
            ).mappings()
        }
        columns = {
            "previous_secret_hash": "VARCHAR",
            "previous_secret_valid_until": "DATETIME",
            "secret_rotated_at": "DATETIME",
            "recent_authenticated_at": "DATETIME",
            "device_description": "VARCHAR",
        }
        for column_name, column_type in columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        f"ALTER TABLE device_session "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
        connection.execute(
            text(
                "UPDATE device_session "
                "SET secret_rotated_at = created_at "
                "WHERE secret_rotated_at IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE device_session "
                "SET device_description = "
                "'Unknown browser on Unknown device' "
                "WHERE device_description IS NULL "
                "OR device_description = ''"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_device_session_previous_secret_valid_until "
                "ON device_session (previous_secret_valid_until)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_device_session_secret_rotated_at "
                "ON device_session (secret_rotated_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_device_session_recent_authenticated_at "
                "ON device_session (recent_authenticated_at)"
            )
        )

        if "login_transaction" not in existing_tables:
            return
        login_transaction_columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(login_transaction)")
            ).mappings()
        }
        if "purpose" not in login_transaction_columns:
            connection.execute(
                text(
                    "ALTER TABLE login_transaction "
                    "ADD COLUMN purpose VARCHAR NOT NULL DEFAULT 'login'"
                )
            )
        if "requested_session_id" not in login_transaction_columns:
            connection.execute(
                text(
                    "ALTER TABLE login_transaction "
                    "ADD COLUMN requested_session_id VARCHAR"
                )
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_login_transaction_purpose "
                "ON login_transaction (purpose)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_login_transaction_requested_session_id "
                "ON login_transaction (requested_session_id)"
            )
        )


def _upgrade_sqlite_identity_recovery_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    table_columns = {
        "external_identity": {
            "account_link_correlation_id": "VARCHAR",
        },
        "identity_audit_event": {
            "related_correlation_id": "VARCHAR",
            "actor_user_id": "VARCHAR",
            "operator_reason": "VARCHAR",
        },
    }
    indexed_columns = {
        "external_identity": ("account_link_correlation_id",),
        "identity_audit_event": (
            "related_correlation_id",
            "actor_user_id",
        ),
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        for table_name, columns in table_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    text(f"PRAGMA table_info({table_name})")
                ).mappings()
            }
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {column_type}"
                        )
                    )
            for column_name in indexed_columns[table_name]:
                connection.execute(
                    text(
                        f"CREATE INDEX IF NOT EXISTS "
                        f"ix_{table_name}_{column_name} "
                        f"ON {table_name} ({column_name})"
                    )
                )


def _upgrade_sqlite_account_classification(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "organization" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(organization)")
            ).mappings()
        }
        if "account_kind" not in existing_columns:
            connection.execute(
                text(
                    "ALTER TABLE organization "
                    "ADD COLUMN account_kind VARCHAR NOT NULL DEFAULT 'legacy'"
                )
            )
        if "personal_owner_user_id" not in existing_columns:
            connection.execute(
                text(
                    "ALTER TABLE organization "
                    "ADD COLUMN personal_owner_user_id VARCHAR"
                )
            )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_organization_account_kind "
                "ON organization (account_kind)"
            )
        )
        if not _sqlite_has_unique_index(
            connection,
            "organization",
            ("personal_owner_user_id",),
        ):
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_organization_personal_owner_user "
                    "ON organization (personal_owner_user_id) "
                    "WHERE personal_owner_user_id IS NOT NULL"
                )
            )


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


def _upgrade_sqlite_cloud_run_stored_object_phase_12d_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    stored_object_columns = {
        "expires_at": "DATETIME",
        "retention_policy": "VARCHAR",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "cloud_run_stored_object" not in existing_tables:
            return

        existing_columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(cloud_run_stored_object)")
            ).mappings()
        }
        for column_name, column_type in stored_object_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE cloud_run_stored_object "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        for column_name in ("expires_at", "retention_policy"):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_stored_object_{column_name} "
                    f"ON cloud_run_stored_object ({column_name})"
                )
            )


def _upgrade_sqlite_cloud_run_phase_13c_cost_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    cloud_run_columns = {
        "budget_reservation_id": "VARCHAR",
        "estimated_cost_cents": "INTEGER NOT NULL DEFAULT 0",
        "actual_cost_cents": "INTEGER NOT NULL DEFAULT 0",
        "cost_summary_json": "JSON NOT NULL DEFAULT '{}'",
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
            "budget_reservation_id",
            "estimated_cost_cents",
            "actual_cost_cents",
        ):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_cloud_run_{column_name} "
                    f"ON cloud_run ({column_name})"
                )
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


def _upgrade_sqlite_usage_ledger_phase_13c_columns(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    usage_columns = {
        "cloud_run_id": "VARCHAR",
        "quantity": "INTEGER NOT NULL DEFAULT 0",
        "unit_name": "VARCHAR DEFAULT ''",
    }

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "usage_ledger_entry" not in existing_tables:
            return

        if _sqlite_usage_ledger_needs_phase_13c_rebuild(connection):
            _rebuild_sqlite_usage_ledger_entry(connection)

        existing_columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(usage_ledger_entry)")
            ).mappings()
        }
        for column_name, column_type in usage_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        "ALTER TABLE usage_ledger_entry "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )

        for column_name in ("cloud_run_id", "quantity"):
            connection.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_usage_ledger_entry_{column_name} "
                    f"ON usage_ledger_entry ({column_name})"
                )
            )


def _upgrade_sqlite_usage_ledger_phase_13c_unique_cloud_run_usage(engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        existing_tables = {
            row["name"]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).mappings()
        }
        if "usage_ledger_entry" not in existing_tables:
            return

        connection.execute(
            text(
                """
                DELETE FROM usage_ledger_entry
                WHERE cloud_run_id IS NOT NULL
                  AND rowid NOT IN (
                    SELECT MIN(rowid)
                    FROM usage_ledger_entry
                    WHERE cloud_run_id IS NOT NULL
                    GROUP BY cloud_run_id, usage_type
                  )
                """
            )
        )

        if _sqlite_has_unique_index(
            connection,
            "usage_ledger_entry",
            ("cloud_run_id", "usage_type"),
        ):
            return

        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_usage_ledger_cloud_run_usage_type "
                "ON usage_ledger_entry (cloud_run_id, usage_type) "
                "WHERE cloud_run_id IS NOT NULL"
            )
        )


def _sqlite_usage_ledger_needs_phase_13c_rebuild(connection) -> bool:
    from ai_company_api.models.entities import UsageType

    create_sql = connection.execute(
        text(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'usage_ledger_entry'
            """
        )
    ).scalar_one_or_none()
    if create_sql is None:
        return False
    normalized_sql = str(create_sql).lower()
    if "check" not in normalized_sql or "usage_type" not in normalized_sql:
        return False
    return any(usage_type.value not in normalized_sql for usage_type in UsageType)


def _rebuild_sqlite_usage_ledger_entry(connection) -> None:
    legacy_table_name = _sqlite_legacy_table_name(
        connection,
        "usage_ledger_entry_phase13c_legacy",
    )
    connection.execute(
        text(
            "ALTER TABLE usage_ledger_entry "
            f"RENAME TO {_sqlite_quote_identifier(legacy_table_name)}"
        )
    )
    _drop_sqlite_indexes_for_table(connection, legacy_table_name)

    usage_table = SQLModel.metadata.tables["usage_ledger_entry"]
    usage_table.create(connection, checkfirst=False)

    legacy_columns = [
        row["name"]
        for row in connection.execute(
            text(f"PRAGMA table_info({_sqlite_quote_identifier(legacy_table_name)})")
        ).mappings()
    ]
    current_columns = [
        row["name"]
        for row in connection.execute(
            text("PRAGMA table_info(usage_ledger_entry)")
        ).mappings()
    ]
    rebuild_defaults = {
        "cloud_run_id": "NULL",
        "quantity": "0",
        "unit_name": "''",
        "unit_price_cents": "0",
        "amount_cents": "0",
        "prompt_tokens": "0",
        "completion_tokens": "0",
        "total_tokens": "0",
        "raw_usage_json": "'{}'",
        "created_at": "CURRENT_TIMESTAMP",
    }
    insert_columns: list[str] = []
    select_expressions: list[str] = []
    for column_name in current_columns:
        if column_name in legacy_columns:
            insert_columns.append(column_name)
            select_expressions.append(_sqlite_quote_identifier(column_name))
        elif column_name in rebuild_defaults:
            insert_columns.append(column_name)
            select_expressions.append(rebuild_defaults[column_name])

    quoted_insert_columns = ", ".join(
        _sqlite_quote_identifier(column_name) for column_name in insert_columns
    )
    select_sql = ", ".join(select_expressions)
    connection.execute(
        text(
            f"INSERT INTO usage_ledger_entry ({quoted_insert_columns}) "
            f"SELECT {select_sql} "
            f"FROM {_sqlite_quote_identifier(legacy_table_name)}"
        )
    )
    connection.execute(
        text(f"DROP TABLE {_sqlite_quote_identifier(legacy_table_name)}")
    )


def _drop_sqlite_indexes_for_table(connection, table_name: str) -> None:
    index_rows = list(connection.execute(
        text(f"PRAGMA index_list({_sqlite_quote_identifier(table_name)})")
    ).mappings())
    for row in index_rows:
        if row.get("origin") == "pk":
            continue
        connection.execute(
            text(f"DROP INDEX IF EXISTS {_sqlite_quote_identifier(str(row['name']))}")
        )


def _sqlite_legacy_table_name(connection, base_name: str) -> str:
    existing_tables = {
        row["name"]
        for row in connection.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).mappings()
    }
    if base_name not in existing_tables:
        return base_name
    suffix = 2
    while f"{base_name}_{suffix}" in existing_tables:
        suffix += 1
    return f"{base_name}_{suffix}"


def _sqlite_quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) + chr(34))}"'


def session_generator(engine) -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def get_session_dependency() -> Generator[Session, None, None]:
    raise RuntimeError("Database session dependency must be overridden by create_app().")
