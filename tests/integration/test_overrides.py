from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from meal_planner.db.parse_repo import delete_override, list_overrides, upsert_override


@pytest.mark.integration
def test_override_round_trip(clean_db: Engine) -> None:
    with clean_db.begin() as conn:
        upsert_override(
            conn, raw_text="Magic onion", canonical="onions", food_group="Other Vegetables"
        )
    with clean_db.connect() as conn:
        rows = list_overrides(conn)
    assert ("magic onion", "onions", "Other Vegetables") in rows


@pytest.mark.integration
def test_override_delete(clean_db: Engine) -> None:
    with clean_db.begin() as conn:
        upsert_override(conn, raw_text="Magic", canonical="magic", food_group="Beans")
        deleted = delete_override(conn, "magic")
    assert deleted == 1


@pytest.mark.integration
def test_override_upsert_idempotent(clean_db: Engine) -> None:
    with clean_db.begin() as conn:
        upsert_override(conn, raw_text="X", canonical="onions", food_group="Other Vegetables")
        upsert_override(conn, raw_text="X", canonical="onions", food_group="Other Vegetables")
    with clean_db.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM meal_planning.ingredient_override")
        ).scalar_one()
    assert n == 1
