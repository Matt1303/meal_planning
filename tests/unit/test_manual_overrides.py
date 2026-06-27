from __future__ import annotations

import csv
from decimal import Decimal

import pytest

from meal_planner.config import Settings
from meal_planner.nutrition import _decimal_or_none_str


@pytest.mark.unit
def test_decimal_or_none_str() -> None:
    assert _decimal_or_none_str("388") == Decimal("388")
    assert _decimal_or_none_str(" 4.0 ") == Decimal("4.0")
    assert _decimal_or_none_str("") is None
    assert _decimal_or_none_str("n/a") is None


@pytest.mark.unit
def test_settings_has_override_paths() -> None:
    s = Settings.load("config/pipeline.yaml")
    assert s.nutrition.overrides_path.name == "nutrition_overrides.csv"
    assert s.parse.overrides_path.name == "ingredient_overrides.csv"


@pytest.mark.unit
def test_override_csvs_are_well_formed() -> None:
    s = Settings.load("config/pipeline.yaml")
    nut = list(csv.DictReader(s.nutrition.overrides_path.open(newline="")))
    canon = {r["ingredient_canonical"] for r in nut}
    assert {"instant oats", "vegan protein supplement"} <= canon
    for r in nut:
        Decimal(r["kcal_per_100g"])  # parseable
        Decimal(r["protein_g_per_100g"])

    ing = list(csv.DictReader(s.parse.overrides_path.open(newline="")))
    raws = {r["raw_text"] for r in ing}
    assert "Instant Oats 80g" in raws
    assert all(r["ingredient_canonical"] for r in ing)
