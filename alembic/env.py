from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from meal_planner.db.engine import build_url
from meal_planner.db.models import SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

DB_URL = os.getenv("DATABASE_URL") or build_url()
config.set_main_option("sqlalchemy.url", DB_URL)


def include_object(obj: object, name: str | None, type_: str, *_: object) -> bool:
    if type_ == "table" and name in {"recipes", "processed_recipes", "weekly_meal_plan"}:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        connection.execute_options(isolation_level="AUTOCOMMIT")
        connection.execute(_create_schema_sql())
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


def _create_schema_sql() -> object:
    from sqlalchemy import text

    return text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
