import os
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError


def get_engine():
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "postgres")
    port = os.getenv("DB_PORT", "5432")
    db = os.getenv("DB_NAME", "meal_planning")
    return create_engine(f"postgresql://{user}:{password}@{host}:{port}/{db}")


def wait_for_db(engine, retries=10, delay=3):
    for _ in range(retries):
        try:
            with engine.connect():
                return True
        except OperationalError:
            time.sleep(delay)
    return False


def fetch_all(engine, query, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        return result.fetchall()


def execute(engine, query, params=None):
    with engine.begin() as conn:
        conn.execute(text(query), params or {})


def execute_many(engine, query, rows):
    with engine.begin() as conn:
        conn.execute(text(query), rows)
