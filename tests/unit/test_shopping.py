from __future__ import annotations

import pytest

from meal_planner.shopping import (
    ShoppingItem,
    _format_qty,
    _section_for,
    shopping_list_markdown,
)

KEYWORDS = [
    ("coconut milk", "Tins & Pulses"),
    ("almond milk", "Chilled"),
    ("milk", "Chilled"),
    ("rice", "Grains & Pasta"),
]
# longest-first, as the loader sorts them
KEYWORDS.sort(key=lambda kv: len(kv[0]), reverse=True)


@pytest.mark.unit
def test_section_keyword_longest_match_wins() -> None:
    # "coconut milk" must beat the shorter "milk"
    assert _section_for("coconut milk", None, KEYWORDS) == "Tins & Pulses"
    assert _section_for("almond milk", None, KEYWORDS) == "Chilled"


@pytest.mark.unit
def test_section_falls_back_to_food_group_then_other() -> None:
    assert _section_for("kale", "Greens", KEYWORDS) == "Fruit & Veg"
    assert _section_for("chickpeas", "Beans", KEYWORDS) == "Tins & Pulses"
    assert _section_for("mystery", None, KEYWORDS) == "Other"


@pytest.mark.unit
def test_format_qty() -> None:
    assert _format_qty(3000) == "3.0 kg"
    assert _format_qty(740) == "740 g"
    assert _format_qty(12) == "10 g"
    assert _format_qty(2) == "5 g"  # floor at 5 g


@pytest.mark.unit
def test_markdown_groups_by_section_with_checkboxes() -> None:
    items = [
        ShoppingItem("Fruit & Veg", "banana", 1100.0, "banana — 1.1 kg", 0, checked=True),
        ShoppingItem("Grains & Pasta", "brown rice", 3000.0, "brown rice — 3.0 kg", 4),
    ]
    md = shopping_list_markdown(items, "Shopping list")
    assert "# Shopping list" in md
    assert "## Fruit & Veg" in md
    assert "- [x] banana — 1.1 kg" in md
    assert "- [ ] brown rice — 3.0 kg" in md
