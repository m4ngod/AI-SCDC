# Alembic Migrations

Phase 0 reserved this directory for future Alembic migrations.

The production identity slice now has a narrow additive schema migration and
dry-run path in the application startup boundary. It adds identity/session
tables and fields, explicit Legacy Account classification, and audit
correlation without destructive down migration. The migration is
provider-neutral, deterministic on retry, and exercised against a
representative legacy snapshot.

The rest of the API still initializes schema directly from SQLModel metadata.
A later phase can replace this mixed compatibility path with generated Alembic
revision history. Follow `docs/runbooks/production-identity-rollout.md` for
identity dry-run, apply, staged enablement, and rollback.
