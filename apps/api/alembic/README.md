# Alembic Migrations

Phase 0 reserves this directory for future Alembic migrations.

During Phase 0 the API initializes the database directly from SQLModel metadata with
`SQLModel.metadata.create_all(...)` in the app factory. A later phase can replace
that metadata creation path with Alembic revision generation and upgrade commands.
