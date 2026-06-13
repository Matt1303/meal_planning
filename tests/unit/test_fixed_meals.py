from __future__ import annotations

import pandas as pd
import pytest

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings
from meal_planner.optimize.data import ModelInputs, prepare


def _settings_with(profiles: list[ProfileTargets], shared: list[str]) -> Settings:
    base = Settings.load("config/pipeline.yaml")
    return base.model_copy(
        update={"household": HouseholdSettings(profiles=profiles, shared_meal_types=shared)}
    )


def _inputs() -> ModelInputs:
    recipes = pd.DataFrame(
        {"recipe_id": [1, 2, 3], "title": ["Smoothie", "Oats", "Soup"], "rating": [5, 4, 4]}
    )
    meal_types = pd.DataFrame(
        {
            "recipe_id": [1, 2, 3, 3],
            "meal_type": ["breakfast", "breakfast", "lunch", "dinner"],
        }
    )
    ingredients = pd.DataFrame(
        columns=["recipe_id", "ingredient_canonical", "food_group", "portion_met", "portions"]
    )
    nutrition = pd.DataFrame(
        {
            "recipe_id": [1, 2, 3],
            "per_serving_kcal": [592, 400, 300],
            "per_serving_fiber_g": [13, 8, 6],
            "per_serving_protein_g": [48, 20, 15],
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


@pytest.mark.unit
def test_fixed_meal_resolves_title_to_id() -> None:
    settings = _settings_with(
        [
            ProfileTargets(name="matt", fixed_meals={"breakfast": "Smoothie"}),
            ProfileTargets(name="sam"),
        ],
        shared=["lunch", "dinner"],
    )
    prepared = prepare(_inputs(), settings)
    assert prepared.fixed_assignments[("matt", "breakfast")] == 1
    assert 1 in prepared.fixed_recipe_ids
    # the pin is forced allowed for that meal slot
    assert prepared.allowed_meal[(1, "breakfast")] == 1


@pytest.mark.unit
def test_fixed_meal_case_insensitive() -> None:
    settings = _settings_with(
        [ProfileTargets(name="matt", fixed_meals={"breakfast": "  smOOthie "})],
        shared=[],
    )
    prepared = prepare(_inputs(), settings)
    assert prepared.fixed_assignments[("matt", "breakfast")] == 1


@pytest.mark.unit
def test_fixed_meal_missing_recipe_is_skipped() -> None:
    settings = _settings_with(
        [ProfileTargets(name="matt", fixed_meals={"breakfast": "Does Not Exist"})],
        shared=[],
    )
    prepared = prepare(_inputs(), settings)
    assert ("matt", "breakfast") not in prepared.fixed_assignments
    assert not prepared.fixed_recipe_ids


@pytest.mark.unit
def test_fixed_meal_on_shared_meal_type_is_skipped() -> None:
    # lunch is shared, so a per-profile fixed lunch makes no sense and is dropped.
    settings = _settings_with(
        [ProfileTargets(name="matt", fixed_meals={"lunch": "Soup"})],
        shared=["lunch", "dinner"],
    )
    prepared = prepare(_inputs(), settings)
    assert ("matt", "lunch") not in prepared.fixed_assignments
