from __future__ import annotations

from decimal import Decimal

import pytest

from meal_planner.servings import parse_servings_count


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("4", Decimal(4)),
        ("4.5", Decimal("4.5")),
        ("4-6", Decimal(5)),
        ("4 to 6", Decimal(5)),
        ("makes 12", Decimal(12)),
        ("serves 6", Decimal(6)),
        ("1 dozen", Decimal(12)),
        ("2 dozen", Decimal(24)),
        ("Makes about 8 cookies", Decimal(8)),
        ("8 (4 per portion)", Decimal(8)),
        (None, None),
        ("", None),
        ("zero servings", None),
        ("0", None),
    ],
)
def test_parse_servings_count(raw: str | None, expected: Decimal | None) -> None:
    assert parse_servings_count(raw) == expected


@pytest.mark.unit
def test_unicode_dash_treated_as_range() -> None:
    assert parse_servings_count("4–6") == Decimal(5)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "Makes about 500ml",
        "Makes about 480 grams",
        "Makes about 300g dip",
        "Makes about 1kg",
        "1 litre",
        "250g loaf",
    ],
)
def test_yield_not_treated_as_servings(raw: str) -> None:
    # weight/volume yields are not a serving count
    assert parse_servings_count(raw) is None


@pytest.mark.unit
def test_serving_count_still_parsed_alongside_yield() -> None:
    assert parse_servings_count("Serves 4") == Decimal(4)
    assert parse_servings_count("Makes 12 muffins") == Decimal(12)
