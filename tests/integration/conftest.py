from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        yield explicit
        return
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not available")

    container = PostgresContainer("postgres:16-alpine")
    try:
        container.start()
    except Exception as exc:
        pytest.skip(f"docker not available: {exc}")
    try:
        yield container.get_connection_url(driver="psycopg2")
    finally:
        container.stop()


@pytest.fixture(scope="session")
def pg_engine(pg_url: str) -> Iterator[Engine]:
    from sqlalchemy import create_engine, text

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS meal_planning"))

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", pg_url)
    os.environ["DATABASE_URL"] = pg_url
    command.upgrade(cfg, "head")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def clean_db(pg_engine: Engine) -> Iterator[Engine]:
    from sqlalchemy import text

    tables = [
        "pipeline_metric",
        "plan_day_group",
        "plan_day_profile",
        "plan_day",
        "plan_meal",
        "plan_run",
        "plan_config",
        "meal_history",
        "recipe_nutrition",
        "ingredient_override",
        "ingredient_parse_cache",
        "ingredient_nutrition_cache",
        "recipe_ingredient",
        "recipe_meal_type",
        "recipe_source",
        "recipe",
    ]
    with pg_engine.begin() as conn:
        for table in tables:
            conn.execute(text(f"TRUNCATE meal_planning.{table} CASCADE"))
        # Keep the shared-meal sentinel (profile_id=0) but wipe other profiles.
        conn.execute(text("DELETE FROM meal_planning.user_profile WHERE profile_id <> 0"))
    yield pg_engine
