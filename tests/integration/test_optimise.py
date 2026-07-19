from __future__ import annotations

from datetime import UTC, date, datetime
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
from meal_planner.optimize.confirm import confirm_plan
from meal_planner.parse import parse_ingredients

# Four days: an even number, so leftover pairing tiles, and small enough for the
# fixture's two shared-capable recipes to fill every slot.
FIXTURE_DAYS = 4


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
                    # The fixture has a single breakfast recipe and the repeat
                    # cap counts appearances across every profile, so a two-person
                    # household eats it FIXTURE_DAYS x 2 times. The cap has to
                    # clear that or the model is infeasible before any assertion.
                    "max_recipe_repeats": FIXTURE_DAYS * 2 + 2,
                    "min_rating": 0,
                    # The fixture has two plant recipes that can take a lunch or
                    # dinner slot. Leftover pairing uses each dish exactly twice,
                    # so those two can fill four slots — inheriting the
                    # production 8-day horizon made the model infeasible before
                    # any assertion ran. Pin the horizon here rather than
                    # tracking whatever the shipped config happens to say.
                    "planning_horizon_days": FIXTURE_DAYS,
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
def test_meal_history_recorded_only_once_confirmed(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    plan_run_id = write_plan(settings_with_fixtures, result, engine=clean_db)

    # Generating a plan leaves a draft — running the optimiser a few times to
    # compare options must not pollute the history the recency term reads.
    with clean_db.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM meal_planning.meal_history")).scalar_one() == 0
        )

    confirm_plan(plan_run_id, date(2026, 1, 5), settings_with_fixtures, engine=clean_db)
    with clean_db.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM meal_planning.meal_history")).scalar_one()
    assert n > 0


def _flatten_picks(plan: dict[int, dict[str, dict[str, int | None]]]) -> dict[int, list[int]]:
    """Every recipe picked on a day, across all slots and eaters."""
    out: dict[int, list[int]] = {}
    for day, slot_to_cell in plan.items():
        for cell in slot_to_cell.values():
            for recipe_id in cell.values():
                if recipe_id is not None:
                    out.setdefault(day, []).append(recipe_id)
    return out


def _picks_per_eater(
    plan: dict[int, dict[str, dict[str, int | None]]],
) -> dict[tuple[int, str], list[int]]:
    """Recipes each person eats on each day.

    The no-repeat rule is per person: a household sharing one breakfast recipe
    means the same id legitimately appears once for each of them on a day.
    """
    out: dict[tuple[int, str], list[int]] = {}
    for day, slot_to_cell in plan.items():
        for cell in slot_to_cell.values():
            for owner, recipe_id in cell.items():
                if recipe_id is not None:
                    out.setdefault((day, owner), []).append(recipe_id)
    return out


@pytest.mark.integration
def test_no_recipe_appears_twice_on_same_day(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    _seed_recipe_nutrition(clean_db)
    result = optimize_plan(settings_with_fixtures, engine=clean_db)
    for (day, owner), picked in _picks_per_eater(result.plan).items():
        assert len(picked) == len(
            set(picked)
        ), f"day {day}, {owner}: recipe appears in more than one slot: {picked}"


def _count_consecutive_repeats(plan: dict[int, dict[str, dict[str, int | None]]]) -> int:
    appearances: dict[int, list[int]] = {}
    for day, picks in _flatten_picks(plan).items():
        for recipe_id in picks:
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


def _user_profile_ids(engine: Engine) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT profile_id FROM meal_planning.user_profile")).fetchall()
    return [int(r[0]) for r in rows]


@pytest.mark.integration
def test_household_two_users_share_lunch_and_dinner(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    from meal_planner.config import HouseholdSettings, ProfileTargets

    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    with clean_db.begin() as conn:
        rows = conn.execute(text("SELECT recipe_id FROM meal_planning.recipe")).fetchall()
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.recipe_nutrition
                        (recipe_id, calories_kcal, fiber_g, per_serving_kcal,
                         per_serving_fiber_g, per_serving_protein_g)
                    VALUES (:rid, 2000, 30, 500, 7.5, 25)
                    ON CONFLICT (recipe_id) DO NOTHING
                    """
                ),
                {"rid": int(row[0])},
            )
    household_settings = settings_with_fixtures.model_copy(
        update={
            "household": HouseholdSettings(
                profiles=[
                    ProfileTargets(name="user_a", calories_daily_min=1700, protein_daily_min=70),
                    ProfileTargets(name="user_b", calories_daily_min=2100, protein_daily_min=100),
                ],
                shared_meal_types=["lunch", "dinner"],
            ),
            "optimizer": settings_with_fixtures.optimizer.model_copy(
                update={"max_recipe_repeats": 30}
            ),
        }
    )
    result = optimize_plan(household_settings, engine=clean_db)
    plan_run_id = write_plan(household_settings, result, engine=clean_db)

    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT day, meal_type, profile_id, recipe_id
                FROM meal_planning.plan_meal
                WHERE plan_run_id = :pr
                ORDER BY day, meal_type, profile_id
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()

    shared_rows = [r for r in rows if r[1] in ("lunch", "dinner")]
    by_day_meal: dict[tuple[int, str], list[tuple[int, int | None]]] = {}
    for d, m, pid, rid in shared_rows:
        by_day_meal.setdefault((int(d), str(m)), []).append((int(pid), rid))
    for (day, meal), entries in by_day_meal.items():
        assert len(entries) == 1, f"day {day} {meal} has more than one row: {entries}"
        assert entries[0][0] == 0, f"day {day} {meal} should be shared (profile_id=0)"

    profile_ids = {int(r[2]) for r in rows if r[1] in ("breakfast", "snack")}
    real_profile_ids = {pid for pid in _user_profile_ids(clean_db) if pid != 0}
    assert profile_ids == real_profile_ids


@pytest.mark.integration
def test_household_per_profile_day_totals_recorded(
    clean_db: Engine, settings_with_fixtures: Settings
) -> None:
    from meal_planner.config import HouseholdSettings, ProfileTargets

    ingest_local_html(settings_with_fixtures, engine=clean_db)
    parse_ingredients(settings_with_fixtures, engine=clean_db)
    with clean_db.begin() as conn:
        rows = conn.execute(text("SELECT recipe_id FROM meal_planning.recipe")).fetchall()
        for row in rows:
            conn.execute(
                text(
                    """
                    INSERT INTO meal_planning.recipe_nutrition
                        (recipe_id, calories_kcal, fiber_g, per_serving_kcal,
                         per_serving_fiber_g, per_serving_protein_g)
                    VALUES (:rid, 2000, 30, 500, 7.5, 25)
                    ON CONFLICT (recipe_id) DO NOTHING
                    """
                ),
                {"rid": int(row[0])},
            )
    household_settings = settings_with_fixtures.model_copy(
        update={
            "household": HouseholdSettings(
                profiles=[
                    ProfileTargets(name="alpha", calories_daily_min=1600),
                    ProfileTargets(name="beta", calories_daily_min=2200),
                ],
                shared_meal_types=["lunch", "dinner"],
            ),
            "optimizer": settings_with_fixtures.optimizer.model_copy(
                update={"max_recipe_repeats": 30}
            ),
        }
    )
    result = optimize_plan(household_settings, engine=clean_db)
    plan_run_id = write_plan(household_settings, result, engine=clean_db)
    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT day, profile_id, kcal FROM meal_planning.plan_day_profile
                WHERE plan_run_id = :pr ORDER BY day, profile_id
                """
            ),
            {"pr": plan_run_id},
        ).fetchall()
    assert len(rows) == FIXTURE_DAYS * 2
    for _, _, kcal in rows:
        assert kcal is not None and float(kcal) > 0


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
