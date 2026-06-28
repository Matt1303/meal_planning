from __future__ import annotations

import pandas as pd
import pytest

from meal_planner.config import Settings
from meal_planner.optimize.data import ModelInputs, filter_recipes


def _settings(must: list[int]) -> Settings:
    s = Settings.load("config/pipeline.yaml")
    return s.model_copy(
        update={"optimizer": s.optimizer.model_copy(update={"must_include_recipe_ids": must})}
    )


def _inputs() -> ModelInputs:
    recipes = pd.DataFrame(
        {"recipe_id": [1, 2, 3], "title": ["a", "b", "c"], "rating": [5.0, 1.0, 1.0]}
    )
    meal_types = pd.DataFrame({"recipe_id": [1, 2, 3], "meal_type": ["dinner", "dinner", "dinner"]})
    empty = pd.DataFrame()
    return ModelInputs(recipes, meal_types, empty, empty, empty)


@pytest.mark.unit
def test_low_rated_recipe_kept_when_must_include() -> None:
    out = filter_recipes(_inputs(), min_rating=3.0, settings=_settings([3]))
    ids = set(out.recipes["recipe_id"])
    assert 1 in ids  # high rating kept normally
    assert 3 in ids  # low rating kept because must-include
    assert 2 not in ids  # low rating, not pinned -> filtered


@pytest.mark.unit
def test_no_must_include_filters_low_rated() -> None:
    out = filter_recipes(_inputs(), min_rating=3.0, settings=_settings([]))
    ids = set(out.recipes["recipe_id"])
    assert ids == {1}
