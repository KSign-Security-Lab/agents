"""Alembic environment.

The DSN comes from application settings rather than alembic.ini so there is one
source of truth, and pgvector's types are imported so autogenerate emits
``vector``/``sparsevec`` columns instead of falling back to generic types.
"""
from __future__ import annotations

from logging.config import fileConfig

import pgvector.sqlalchemy  # noqa: F401 - registers the vector/sparsevec types
from alembic import context
from sqlalchemy import engine_from_config, pool

from api.app.config import settings
from api.app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.sync_dsn)
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    # LangGraph's checkpointer manages its own tables; never let autogenerate
    # try to drop them.
    if type_ == "table" and name.startswith(("checkpoint", "checkpoint_blobs",
                                             "checkpoint_writes", "checkpoint_migrations")):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
