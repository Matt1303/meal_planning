from __future__ import annotations

import os
import time
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError


def build_url(
    user: str | None = None,
    password: str | None = None,
    host: str | None = None,
    port: str | None = None,
    db: str | None = None,
) -> str:
    user = user or os.getenv("DB_USER", "postgres")
    password = password or os.getenv("DB_PASSWORD", "postgres")
    host = host or os.getenv("DB_HOST", "postgres")
    port = port or os.getenv("DB_PORT", "5432")
    db = db or os.getenv("DB_NAME", "meal_planning")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


def build_engine(url: str | None = None) -> Engine:
    target = url or build_url()
    return create_engine(
        target,
        pool_pre_ping=True,
        pool_size=5,
        pool_recycle=3600,
        future=True,
    )


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine()


def wait_for_db(engine: Engine, retries: int = 10, delay: float = 3.0) -> bool:
    for _ in range(retries):
        try:
            with engine.connect():
                return True
        except OperationalError:
            time.sleep(delay)
    return False
