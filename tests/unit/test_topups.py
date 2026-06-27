from __future__ import annotations

import pytest

from meal_planner.config import PerPersonPortion, TopUpFruit, TopUpSettings
from meal_planner.ui.data import _build_topups, _meal_dozen

PORTION_SIZES = {"Other Fruits": 80.0, "Berries": 80.0, "Whole Grains": 80.0}


def _topup() -> TopUpSettings:
    return TopUpSettings(
        whey_protein_g=22,
        whey_kcal=114,
        max_whey_scoops=6,
        fruits=[
            TopUpFruit(name="Mixed berries", grams=80, food_group="Berries", kcal=40, fiber_g=2.0),
            TopUpFruit(name="Banana", grams=120, food_group="Other Fruits", kcal=107, fiber_g=3.1),
        ],
    )


@pytest.mark.unit
def test_whey_bridges_protein_gap() -> None:
    entries = _build_topups(
        _topup(),
        day_protein=150.0,
        protein_min=190.0,
        day_dozen={},
        dozen_targets={},
        portion_sizes=PORTION_SIZES,
    )
    whey = [e for e in entries if "Whey" in (e.title or "")]
    assert len(whey) == 1
    # gap 40 / 22 per scoop -> ceil = 2 scoops
    assert "×2" in (whey[0].title or "")
    assert whey[0].protein_g == pytest.approx(44.0)
    assert whey[0].kcal == pytest.approx(228.0)


@pytest.mark.unit
def test_no_whey_when_protein_met() -> None:
    entries = _build_topups(
        _topup(),
        day_protein=200.0,
        protein_min=190.0,
        day_dozen={},
        dozen_targets={},
        portion_sizes=PORTION_SIZES,
    )
    assert not [e for e in entries if "Whey" in (e.title or "")]


@pytest.mark.unit
def test_fruit_bridges_fruit_gap() -> None:
    # Other Fruits target 3, have 1.0; banana adds 120/80 = 1.5 servings each.
    entries = _build_topups(
        _topup(),
        day_protein=999.0,
        protein_min=None,
        day_dozen={"Other Fruits": 1.0},
        dozen_targets={"Other Fruits": 3},
        portion_sizes=PORTION_SIZES,
    )
    fruit = [e for e in entries if "Banana" in (e.title or "")]
    assert len(fruit) == 1
    added = fruit[0].dozen["Other Fruits"]
    assert 1.0 + added >= 3.0  # gap closed
    assert "×2" in (fruit[0].title or "")  # two bananas (1.5 + 1.5)


@pytest.mark.unit
def test_meal_dozen_applies_per_person_rice_override() -> None:
    rows = [("Whole Grains", "brown rice", 3.125, 250.0, True)]
    specs = [
        PerPersonPortion(canonicals=["brown rice"], grams={"matt": 400}, value_as="cooked rice")
    ]
    matt = _meal_dozen(rows, "matt", specs, PORTION_SIZES)
    ellie = _meal_dozen(rows, "ellie", specs, PORTION_SIZES)
    assert matt["Whole Grains"] == pytest.approx(400 / 80)  # 5.0 servings
    assert ellie["Whole Grains"] == pytest.approx(3.125)  # unchanged (no override)
