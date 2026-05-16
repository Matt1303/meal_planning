from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from meal_planner.config import Settings
from meal_planner.db.recipes_repo import (
    insert_raw_ingredient_lines,
    replace_meal_types,
    upsert_recipe,
    upsert_recipe_source,
)
from meal_planner.ingest import ingest_local_html
from meal_planner.optimize import optimize_plan, write_plan
from meal_planner.parse import parse_ingredients


@pytest.fixture
def settings_with_fixtures() -> Settings:
    base = Settings.load(Path("config/pipeline.yaml"))
    return base.model_copy(
        update={
            "sources": base.sources.model_copy(
                update={
                    "local_html": base.sources.local_html.model_copy(
                        update={"path": Path("tests/fixtures/recipes")}
                    )
                }
            ),
            "optimizer": base.optimizer.model_copy(
                update={
                    "calories_daily_min": None,
                    "calories_daily_max": None,
                    "fiber_daily_min": None,
                    "calories_weekly_min": None,
                    "calories_weekly_max": None,
                    "fiber_weekly_min": None,
                    "max_recipe_repeats": 7,
                    "min_rating": 0,
                }
            ),
        }
    )


def _seed_recipe_nutrition(engine: Engine) -> None:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT recipe_id FROM meal_planning.recipe")).fetchall()
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.recipe_nutrition
                        (recipe_id, calories_kcal, fiber_g, per_serving_kcal, per_serving_fiber_g)
                    VALUES (:rid, 2000, 30, 500, 7.5)
                    ON CONFLICT (recipe_id) DO NOTHING
                    """
                ),
                {"rid": int(row[0])},
            )


@pytest.mark.integration
def test_optimise_produces_plan(clean_db: Engine, settings_with_fixtures: Settings) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    assert result.solver_status
    assert len(result.plan) == settings_with_fixtures.optimizer.planning_horizon_days


@pytest.mark.integration
def test_plant_only_excludes_chicken(clean_db: Engine, settings_with_fixtures: Settings) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    plan_run_id = write_plan(settings_with_fixtures, result, engine=clean_db)
    with clean_db.connect() as conn:
        chicken_count = conn.execute(
            text(
                """
                SELECT count(*)
                FROM meal_planning.plan_meal pm
                JOIN meal_planning.recipe r ON r.recipe_id = pm.recipe_id
                WHERE pm.plan_run_id = :pr AND lower(r.title) LIKE '%chicken%'
                """
            ),
            {"pr": plan_run_id},
        ).scalar_one()
    assert chicken_count == 0


@pytest.mark.integration
def test_plan_config_snapshot(clean_db: Engine, settings_with_fixtures: Settings) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    plan_run_id = write_plan(settings_with_fixtures, result, engine=clean_db)
    with clean_db.connect() as conn:
        cfg_id = conn.execute(
            text("SELECT config_id FROM meal_planning.plan_run WHERE plan_run_id = :pr"),
            {"pr": plan_run_id},
        ).scalar_one()
    assert cfg_id is not None


@pytest.mark.integration
def test_no_recipes_raises(clean_db: Engine, settings_with_fixtures: Settings) -> None:
    with pytest.raises(RuntimeError):
        optimize_plan(settings_with_fixtures, engine=clean_db)


@pytest.mark.integration
def test_meal_history_recorded(clean_db: Engine, settings_with_fixtures: Settings) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    write_plan(settings_with_fixtures, result, engine=clean_db)
    with clean_db.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM meal_planning.meal_history")).scalar_one()
    assert n > 0


@pytest.mark.integration
def test_no_recipe_appears_twice_on_same_day(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    for day, meals in result.plan.items():
        picked = [r for r in meals.values() if r is not None]
        assert len(picked) == len(
            set(picked)
        ), f"day {day}: recipe appears in more than one slot: {meals}"


def _count_consecutive_repeats(result_plan: dict[int, dict[str, int | None]]) -> int:
    appearances: dict[int, list[int]] = {}
    for day, meals in result_plan.items():
        for recipe_id in meals.values():
            if recipe_id is None:
                continue
            appearances.setdefault(recipe_id, []).append(day)
    count = 0
    for days in appearances.values():
        sorted_days = sorted(days)
        for d1, d2 in pairwise(sorted_days):
            if d2 - d1 == 1:
                count += 1
    return count


@pytest.mark.integration
def test_spacing_penalty_reduces_consecutive_repeats(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)

    no_spacing = settings_with_fixtures.model_copy(
        update={
            "optimizer": settings_with_fixtures.optimizer.model_copy(update={"spacing_weight": 0.0})
        }
    )
    with_spacing = settings_with_fixtures.model_copy(
        update={
            "optimizer": settings_with_fixtures.optimizer.model_copy(
                update={
                    "spacing_weight": 20.0,
                    "spacing_penalty_by_gap": {1: 10.0, 2: 1.0, 3: 0.1},
                }
            )
        }
    )

    base = optimize_plan(no_spacing, engine=clean_db)
    nudged = optimize_plan(with_spacing, engine=clean_db)
    base_consecutive = _count_consecutive_repeats(base.plan)
    nudged_consecutive = _count_consecutive_repeats(nudged.plan)
    assert nudged_consecutive <= base_consecutive, (
        f"spacing penalty did not reduce consecutive repeats: "
        f"base={base_consecutive} nudged={nudged_consecutive}"
    )


@pytest.mark.integration
def test_protein_daily_min_constraint_used(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    with clean_db.begin() as conn:
        rows = conn.execute(text("SELECT recipe_id FROM meal_planning.recipe")).fetchall()
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.recipe_nutrition
                        (recipe_id, calories_kcal, fiber_g,
                         per_serving_kcal, per_serving_fiber_g, per_serving_protein_g)
                    VALUES (:rid, 2000, 30, 500, 7.5, 25)
                    ON CONFLICT (recipe_id) DO NOTHING
                    """
                ),
                {"rid": int(row[0])},
            )
    with_protein = settings_with_fixtures.model_copy(
        update={
            "optimizer": settings_with_fixtures.optimizer.model_copy(
                update={"protein_daily_min": 60}
            )
        }
    )
    result = optimize_plan(with_protein, engine=clean_db)
    assert result.solver_status
    assert result.relaxation_level == 0
