from __future__ import annotations

import pandas as pd
import pytest

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings
from meal_planner.optimize.data import ModelInputs, prepare, snack_slot_names


@pytest.mark.unit
def test_snack_slot_names() -> None:
    assert snack_slot_names(1) == ["snack"]
    assert snack_slot_names(3) == ["snack_1", "snack_2", "snack_3"]


def _inputs() -> ModelInputs:
    recipes = pd.DataFrame(
        {
            "recipe_id": [1, 2, 3, 4],
            "title": ["Smoothie A", "Energy Balls", "Oat Bar", "Smoothie B"],
            "rating": [5, 4, 4, 5],
            "categories": [
                "Smoothies, Snacks",
                "Snacks",
                "Snacks",
                "Breakfasts, Smoothies, Snacks",
            ],
        }
    )
    meal_types = pd.DataFrame(
        {"recipe_id": [1, 2, 3, 4], "meal_type": ["snack", "snack", "snack", "snack"]}
    )
    ingredients = pd.DataFrame(
        columns=["recipe_id", "ingredient_canonical", "food_group", "portion_met", "portions"]
    )
    nutrition = pd.DataFrame(
        {
            "recipe_id": [1, 2, 3, 4],
            "per_serving_kcal": [200, 150, 180, 210],
            "per_serving_fiber_g": [3, 2, 4, 3],
            "per_serving_protein_g": [8, 5, 6, 9],
        }
    )
    history = pd.DataFrame(columns=["recipe_id", "last_planned"])
    return ModelInputs(
        recipes=recipes,
        meal_types=meal_types,
        ingredients=ingredients,
        nutrition=nutrition,
        history=history,
    )


def _settings(max_snacks: int, limits: dict[str, int]) -> Settings:
    base = Settings.load("config/pipeline.yaml")
    opt = base.optimizer.model_copy(
        update={"max_snacks_per_day": max_snacks, "snack_category_limits": limits}
    )
    return base.model_copy(
        update={
            "optimizer": opt,
            "household": HouseholdSettings(
                profiles=[ProfileTargets(name="x")], shared_meal_types=[]
            ),
        }
    )


@pytest.mark.unit
def test_prepare_expands_snack_slots() -> None:
    prepared = prepare(_inputs(), _settings(3, {"smoothie": 1}))
    assert prepared.snack_meal_types == ["snack_1", "snack_2", "snack_3"]
    assert "snack_1" in prepared.per_user_meal_types
    assert "snack" not in prepared.per_user_meal_types
    # snack allow-list propagates to each slot
    assert prepared.allowed_meal[(1, "snack_1")] == 1


@pytest.mark.unit
def test_prepare_maps_smoothie_category() -> None:
    prepared = prepare(_inputs(), _settings(3, {"smoothie": 1}))
    assert prepared.category_recipe_ids["smoothie"] == {1, 4}
    assert prepared.snack_category_limits == {"smoothie": 1}


@pytest.mark.unit
def test_single_snack_keeps_plain_name() -> None:
    prepared = prepare(_inputs(), _settings(1, {}))
    assert prepared.snack_meal_types == []
    assert "snack" in prepared.per_user_meal_types
