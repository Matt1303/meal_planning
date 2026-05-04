from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from meal_planner.config import Settings
from meal_planner.ingest import ingest_local_html


@pytest.fixture
def settings_with_fixture_recipes(tmp_path: Path) -> Settings:
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
def test_ingest_loads_fixtures(clean_db: Engine, settings_with_fixture_recipes: Settings) -> None:
    result = ingest_local_html(settings_with_fixture_recipes, engine=clean_db)
    assert result.files_seen == 5
    assert result.recipes_upserted == 5
    with clean_db.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM meal_planning.recipe")).scalar_one()
        plant_count = conn.execute(
            text("SELECT count(*) FROM meal_planning.recipe WHERE is_plant_based = TRUE")
        ).scalar_one()
        non_plant_count = conn.execute(
            text("SELECT count(*) FROM meal_planning.recipe WHERE is_plant_based = FALSE")
        ).scalar_one()
    assert count == 5
    assert plant_count == 4
    assert non_plant_count == 1


@pytest.mark.integration
def test_lunches_normalised_to_lunch(
    clean_db: Engine, settings_with_fixture_recipes: Settings
) -> None:
    ingest_local_html(settings_with_fixture_recipes, engine=clean_db)
    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT meal_type FROM meal_planning.recipe_meal_type
                WHERE recipe_id IN (
                    SELECT recipe_id FROM meal_planning.recipe WHERE title = 'Simple Lentil Stew'
                )
                ORDER BY meal_type
                """
            )
        ).fetchall()
    types = sorted(r[0] for r in rows)
    assert "lunch" in types
    assert "lunche" not in types


@pytest.mark.integration
def test_recipe_source_idempotent(
    clean_db: Engine, settings_with_fixture_recipes: Settings
) -> None:
    ingest_local_html(settings_with_fixture_recipes, engine=clean_db)
    ingest_local_html(settings_with_fixture_recipes, engine=clean_db)
    with clean_db.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM meal_planning.recipe_source")).scalar_one()
    assert count == 5
