from __future__ import annotations

import pytest

from meal_planner.meal_types import MEAL_TYPE_NORMALIZE, normalize_meal_types


@pytest.mark.unit
def test_lunches_maps_to_lunch() -> None:
    assert MEAL_TYPE_NORMALIZE["lunches"] == "lunch"


@pytest.mark.unit
def test_lunches_in_categories_normalised() -> None:
    assert "lunch" in normalize_meal_types("Lunches, Dinner, How Not To Diet")


@pytest.mark.unit
def test_unknown_dropped() -> None:
    result = normalize_meal_types("Lunches, How Not To Diet, Random Tag")
    assert result == ["lunch"]


@pytest.mark.unit
def test_dedup() -> None:
    result = normalize_meal_types("Breakfast, Breakfasts, Brunch")
    assert result == ["breakfast"]


@pytest.mark.unit
def test_empty() -> None:
    assert normalize_meal_types(None) == []
    assert normalize_meal_types("") == []
    assert normalize_meal_types(",,") == []


@pytest.mark.unit
def test_canonical_meal_types_present() -> None:
    for token in ["breakfast", "lunch", "dinner", "snack"]:
        assert MEAL_TYPE_NORMALIZE[token] == token
