from __future__ import annotations

import pandas as pd
import pytest

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings
from meal_planner.optimize.data import ModelInputs, prepare
from meal_planner.optimize.model import ModelOptions, build_model

OPTIONS = ModelOptions(
    enforce_daily_kcal=False,
    enforce_daily_fiber=False,
    enforce_daily_protein=False,
    enforce_weekly_kcal=False,
    enforce_weekly_fiber=False,
    enforce_weekly_protein=False,
    enforce_group_targets=False,
    enforce_weekly_groups=False,
)


def _inputs() -> ModelInputs:
    recipes = pd.DataFrame(
        {
            "recipe_id": [1, 2, 3],
            "title": ["Slow Stew", "Quick Curry", "Bol Pot"],
            "rating": [5, 4, 4],
            "categories": ["Dinner", "Dinner", "Dinner, Ready Meal"],
            "prep_minutes": [20, 5, None],
            "cook_minutes": [60, 15, None],
        }
    )
    meal_types = pd.DataFrame({"recipe_id": [1, 2, 3], "meal_type": ["dinner"] * 3})
    ingredients = pd.DataFrame(
        columns=["recipe_id", "ingredient_canonical", "food_group", "portion_met", "portions"]
    )
    nutrition = pd.DataFrame(
        {
            "recipe_id": [1, 2, 3],
            "per_serving_kcal": [600, 550, 500],
            "per_serving_fiber_g": [10, 8, 9],
            "per_serving_protein_g": [25, 22, 20],
        }
    )
    history = pd.DataFrame(columns=["recipe_id", "last_planned"])
    return ModelInputs(recipes, meal_types, ingredients, nutrition, history)


def _settings(**time_budget: object) -> Settings:
    base = Settings.load("config/pipeline.yaml")
    opt = base.optimizer.model_copy(
        update={
            "planning_horizon_days": 2,
            "max_recipe_repeats": 8,
            "time_budget": base.optimizer.time_budget.model_copy(
                update={"enabled": True, **time_budget}
            ),
        }
    )
    household = HouseholdSettings(
        profiles=[ProfileTargets(name="a"), ProfileTargets(name="b")],
        shared_meal_types=["dinner"],
    )
    return base.model_copy(
        update={"optimizer": opt, "household": household, "meal_types": ["dinner"]}
    )


@pytest.mark.unit
def test_minutes_scaled_and_ready_meals_flat() -> None:
    prepared = prepare(_inputs(), _settings())
    # 80 raw minutes at the shipped 1.5x multiplier; the ready meal ignores its
    # (absent) recipe times and costs the flat microwave minutes.
    assert prepared.cook_minutes[1] == pytest.approx(120.0)
    assert prepared.cook_minutes[3] == pytest.approx(3.0)
    assert prepared.ready_meal_ids == {3}


@pytest.mark.unit
def test_ready_meal_is_exempt_from_leftover_pairing() -> None:
    prepared = prepare(_inputs(), _settings())
    model = build_model(prepared, _settings(), OPTIONS)
    # Paired dishes get an exactly-twice constraint; the Bol pot must not.
    constrained = {(r, meal) for (r, meal) in model.leftover_pairing}
    assert (1, "dinner") in constrained
    assert (3, "dinner") not in constrained


@pytest.mark.unit
def test_weekly_time_budget_prefers_the_quick_options() -> None:
    from pyomo.environ import SolverFactory, value

    settings = _settings(weekly_minutes=45, slack_weight=50.0)
    prepared = prepare(_inputs(), settings)
    model = build_model(prepared, settings, OPTIONS)
    solver = SolverFactory("appsi_highs")
    solver.config.load_solution = True
    solver.solve(model)
    # Two dinner slots under a 45-minute week: the 120-minute stew cannot fit
    # without slack, the 30-minute curry pair or 3-minute pots can.
    stew_uses = sum(value(model.x_shared[1, d, "dinner"]) for d in prepared.days)
    assert stew_uses == 0
    assert value(model.slack_weekly_time) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.unit
def test_time_budget_off_builds_no_time_constraints() -> None:
    settings = _settings(weekly_minutes=45)
    disabled = settings.model_copy(
        update={
            "optimizer": settings.optimizer.model_copy(
                update={
                    "time_budget": settings.optimizer.time_budget.model_copy(
                        update={"enabled": False}
                    )
                }
            )
        }
    )
    model = build_model(prepare(_inputs(), disabled), disabled, OPTIONS)
    assert not hasattr(model, "weekly_time")
    assert not hasattr(model, "session_time")


@pytest.mark.unit
def test_session_budget_charges_the_fresh_day_once() -> None:
    from pyomo.environ import SolverFactory, value

    settings = _settings(session_minutes=130, slack_weight=50.0)
    prepared = prepare(_inputs(), settings)
    model = build_model(prepared, settings, OPTIONS)
    solver = SolverFactory("appsi_highs")
    solver.config.load_solution = True
    solver.solve(model)
    # Whatever got cooked, a paired dish is charged on one day, not both.
    for r, meal in model.FRESH:
        fresh_total = sum(value(model.fresh_cook[r, meal, d]) for d in prepared.days)
        picked = sum(value(model.x_shared[r, d, meal]) for d in prepared.days)
        if picked >= 2:
            assert fresh_total == pytest.approx(1.0, abs=1e-6)
