from __future__ import annotations

import pytest

from meal_planner.config import TopUpFruit, TopUpSettings
from meal_planner.ui.data import IngredientLine, _build_topups, _meal_dozen


def _topup() -> TopUpSettings:
    return TopUpSettings(
        whey_protein_g=22,
        whey_kcal=114,
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
def test_whey_bridges_protein_gap() -> None:
    entries = _build_topups(_topup(), 150.0, 190.0, {}, {})
    whey = [e for e in entries if "Whey" in (e.title or "")]
    assert len(whey) == 1
    assert "×2" in (whey[0].title or "")  # gap 40 / 22 -> 2 scoops
    assert whey[0].protein_g == pytest.approx(44.0)


@pytest.mark.unit
def test_no_whey_when_protein_met() -> None:
    entries = _build_topups(_topup(), 200.0, 190.0, {}, {})
    assert not [e for e in entries if "Whey" in (e.title or "")]


@pytest.mark.unit
def test_fruit_adds_distinct_foods_only() -> None:
    # Other Fruits target 3, banana already present -> add 2 *new* distinct fruits.
    entries = _build_topups(
        _topup(), 999.0, None, {"Other Fruits": {"banana"}}, {"Other Fruits": 3}
    )
    titles = [e.title or "" for e in entries]
    assert len(entries) == 2
    assert any("Apple" in t for t in titles)
    assert any("Orange" in t for t in titles)
    assert not any("Banana" in t for t in titles)  # already present, skipped
    for e in entries:
        assert len(e.dozen["Other Fruits"]) == 1  # each adds one distinct food
