from __future__ import annotations

from decimal import Decimal

import pytest

from meal_planner.parse import (
    normalize_text,
    regex_parse_quantity,
    strip_quantity,
)


@pytest.mark.unit
def test_strip_quantity_basic() -> None:
    assert strip_quantity("2 cups chopped kale") == "chopped kale"


@pytest.mark.unit
def test_strip_quantity_with_grams() -> None:
    assert strip_quantity("400g black beans") == "black beans"


@pytest.mark.unit
def test_normalize_text() -> None:
    assert normalize_text("  RED  Onion  ") == "red onion"


@pytest.mark.unit
def test_regex_parse_range() -> None:
    qty, unit = regex_parse_quantity("200-300g lentils")
    assert qty == Decimal(250)
    assert unit == "g"


@pytest.mark.unit
def test_regex_parse_multiplier() -> None:
    qty, unit = regex_parse_quantity("2 x 400g tomatoes")
    assert qty == Decimal(800)
    assert unit == "g"


@pytest.mark.unit
def test_regex_parse_fraction() -> None:
    qty, unit = regex_parse_quantity("1 1/2 cups oats")
    assert qty == Decimal("1.5")
    assert unit in {"cup", "cups"}


@pytest.mark.unit
def test_regex_parse_simple_fraction() -> None:
    qty, unit = regex_parse_quantity("1/2 tsp salt")
    assert qty == Decimal("0.5")
    assert unit == "tsp"


@pytest.mark.unit
def test_regex_parse_none() -> None:
    qty, unit = regex_parse_quantity("a pinch of salt")
    assert qty is None or unit is not None
