from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from meal_planner.config import Settings
from meal_planner.ingest import ingest_local_html
from meal_planner.parse import parse_ingredients


@pytest.fixture
def settings_with_fixture_recipes() -> Settings:
    base = Settings.load(Path("config/pipeline.yaml"))
    return base.model_copy(
        update={
            "sources": base.sources.model_copy(
                update={
                    "local_html": base.sources.local_html.model_copy(
                        update={"path": Path("tests/fixtures/recipes")}
                    )
                }
            )
        }
    )


@pytest.mark.integration
def test_parse_assigns_canonicals(
    clean_db: Engine, settings_with_fixture_recipes: Settings
) -> None:
    ingest_local_html(settings_with_fixture_recipes, engine=clean_db)
    parse_ingredients(settings_with_fixture_recipes, engine=clean_db)
    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT raw_text, ingredient_canonical, food_group
                FROM meal_planning.recipe_ingredient
                ORDER BY raw_text
                """
            )
        ).fetchall()
    by_raw = {r[0]: (r[1], r[2]) for r in rows}
    canonical_for_kale = next(
        (c for raw, (c, _) in by_raw.items() if "kale" in raw.lower()),
        None,
    )
    assert canonical_for_kale is not None
    canonical_for_chickpeas = next(
        (c for raw, (c, _) in by_raw.items() if "chickpeas" in raw.lower()),
        None,
    )
    assert canonical_for_chickpeas in {"chickpeas", "chick peas"}


@pytest.mark.integration
def test_parse_idempotent(clean_db: Engine, settings_with_fixture_recipes: Settings) -> None:
    ingest_local_html(settings_with_fixture_recipes, engine=clean_db)
    first = parse_ingredients(settings_with_fixture_recipes, engine=clean_db)
    second = parse_ingredients(settings_with_fixture_recipes, engine=clean_db)
    # second pass should have already-parsed rows resolved (cached)
    assert second <= first
