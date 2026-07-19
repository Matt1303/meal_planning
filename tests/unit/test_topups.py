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


def _line(
    canonical: str, group: str | None, qualifies: bool, fraction: float | None = None
) -> IngredientLine:
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
        dozen_fraction=(1.0 if qualifies else 0.0) if fraction is None else fraction,
    )


@pytest.mark.unit
def test_meal_dozen_counts_each_food_capped_at_one_portion() -> None:
    lines = [
        _line("rice", "Whole Grains", True),
        _line("oats", "Whole Grains", True),
        # Same food twice: the two halves add up, but never past one portion.
        _line("rice", "Whole Grains", True),
        # Below a full portion, so it counts for its fraction rather than zero.
        _line("kale", "Greens", False, fraction=0.625),
        _line("salt", None, False),
    ]
    dz = _meal_dozen(lines, {"Whole Grains", "Greens", "Berries"})
    assert sorted(dz["Whole Grains"]) == ["oats", "rice"]
    assert dz["Whole Grains"]["rice"] == pytest.approx(1.0)
    assert dz["Greens"]["kale"] == pytest.approx(0.625)
    assert "Berries" not in dz


@pytest.mark.unit
def test_meal_dozen_caps_a_generous_single_food_at_one() -> None:
    # 400 g of rice is one whole grain, not five — the rule that made counting
    # distinct foods necessary in the first place.
    dz = _meal_dozen([_line("rice", "Whole Grains", True, fraction=1.0)], {"Whole Grains"})
    assert sum(dz["Whole Grains"].values()) == pytest.approx(1.0)


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
