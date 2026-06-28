from __future__ import annotations

import pytest

from meal_planner.config import TopUpFruit, TopUpSettings
from meal_planner.ui.data import IngredientLine, _fruit_topups, _meal_dozen, _whey_meal


def _topup() -> TopUpSettings:
    return TopUpSettings(
        whey_protein_g=18,
        whey_kcal=95,
        max_whey_scoops=6,
        fruits=[
            TopUpFruit(name="Mixed berries", grams=80, food_group="Berries", kcal=40),
            TopUpFruit(name="Banana", grams=120, food_group="Other Fruits", kcal=107),
            TopUpFruit(name="Apple", grams=150, food_group="Other Fruits", kcal=78),
            TopUpFruit(name="Orange", grams=130, food_group="Other Fruits", kcal=62),
        ],
    )


def _line(canonical: str, group: str | None, qualifies: bool) -> IngredientLine:
    return IngredientLine(
        raw_text=canonical,
        ingredient_canonical=canonical,
        per_serving_grams=100.0,
        kcal=0.0,
        protein_g=0.0,
        fiber_g=0.0,
        fat_g=0.0,
        carbs_g=0.0,
        match_source_name=None,
        match_score=None,
        source=None,
        food_group=group,
        dozen_qualifies=qualifies,
    )


@pytest.mark.unit
def test_meal_dozen_counts_distinct_qualifying_foods() -> None:
    lines = [
        _line("rice", "Whole Grains", True),
        _line("oats", "Whole Grains", True),
        _line("rice", "Whole Grains", True),  # duplicate food — counted once
        _line("kale", "Greens", False),  # below min portion — does not count
        _line("salt", None, False),
    ]
    dz = _meal_dozen(lines, {"Whole Grains", "Greens", "Berries"})
    assert sorted(dz["Whole Grains"]) == ["oats", "rice"]
    assert "Greens" not in dz  # kale did not meet its min portion


@pytest.mark.unit
def test_whey_meal_macros_and_label() -> None:
    meal = _whey_meal(_topup(), 3)
    assert "3 scoops" in (meal.title or "")
    assert meal.protein_g == pytest.approx(54.0)  # 3 × 18
    assert meal.kcal == pytest.approx(285.0)  # 3 × 95
    assert meal.is_topup


@pytest.mark.unit
def test_fruit_adds_distinct_foods_within_calorie_ceiling() -> None:
    # Other Fruits target 3, banana already present -> add 2 *new* distinct fruits.
    entries = _fruit_topups(
        _topup(),
        {"Other Fruits": {"banana"}},
        {"Other Fruits": 3},
        day_kcal=1000.0,
        calorie_max=2000.0,
    )
    titles = [e.title or "" for e in entries]
    assert len(entries) == 2
    assert any("Apple" in t for t in titles)
    assert any("Orange" in t for t in titles)
    assert not any("Banana" in t for t in titles)  # already present, skipped


@pytest.mark.unit
def test_fruit_respects_calorie_ceiling() -> None:
    # Already at the ceiling -> no fruit added even though the category is short.
    entries = _fruit_topups(
        _topup(),
        {"Other Fruits": set()},
        {"Other Fruits": 3},
        day_kcal=1990.0,
        calorie_max=2000.0,
    )
    assert entries == []
