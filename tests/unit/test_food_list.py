from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.food_list import load_food_groups, load_synonyms, parse_food_list


@pytest.mark.unit
def test_parse_canonical_food_list() -> None:
    path = Path("config/food_list_canonical.txt")
    parsed = parse_food_list(path)
    items = {item.lower(): group for item, group in parsed}
    assert items.get("chickpeas") == "Beans"
    assert items.get("kale") == "Cruciferous Vegetables"
    assert items.get("oats") == "Whole Grains"


@pytest.mark.unit
def test_load_food_groups_paths() -> None:
    groups = load_food_groups([Path("config/food_list_canonical.txt")])
    assert "kale" in groups
    assert groups["kale"] == "Cruciferous Vegetables"


@pytest.mark.unit
def test_load_synonyms() -> None:
    syn = load_synonyms(Path("config/ingredient_synonyms.csv"))
    assert syn.get("aubergine") == "aubergine (eggplant)"
