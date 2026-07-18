from __future__ import annotations

from decimal import Decimal

import pytest

from meal_planner.parse import (
    _is_section_header,
    _preprocess_fraction_words,
    _primary_clause,
    _quantulum_then_regex,
    _strip_containers,
    normalize_text,
    regex_parse_quantity,
    strip_quantity,
)


@pytest.mark.unit
def test_strip_containers() -> None:
    assert _strip_containers("400 gram can black beans").split() == [
        "400",
        "gram",
        "black",
        "beans",
    ]
    assert "tin" not in _strip_containers("1 tin chopped tomatoes")


@pytest.mark.unit
def test_canned_quantity_parses_after_container_strip() -> None:
    # "One 400 gram can black beans": leading "One" + "can" both broke quantulum
    qty, unit = _quantulum_then_regex(_strip_containers("One 400 gram can black beans"), [0])
    assert qty == Decimal("400")
    assert unit in {"gram", "g", "grams"}


@pytest.mark.unit
def test_primary_clause_splits_spaced_slash() -> None:
    assert (
        _primary_clause("400 ml can full-fat coconut milk / 160ml coconut cream")
        == "400 ml can full-fat coconut milk"
    )


@pytest.mark.unit
def test_primary_clause_keeps_dual_unit_and_alias() -> None:
    # unspaced slash is a dual-unit quantity or an alias — leave intact
    assert _primary_clause("1 cup/250ml vegetable broth") == "1 cup/250ml vegetable broth"
    assert _primary_clause("½ tsp chilli/hot pepper flakes") == "½ tsp chilli/hot pepper flakes"
    assert _primary_clause("300 g/10 oz brussels sprouts") == "300 g/10 oz brussels sprouts"


@pytest.mark.unit
def test_preprocess_strips_size_adjective() -> None:
    assert _preprocess_fraction_words("1 large avocado") == "1 avocado"
    assert _preprocess_fraction_words("1 medium onion, chopped") == "1 onion, chopped"
    assert _preprocess_fraction_words("2 large oranges") == "2 oranges"


@pytest.mark.unit
def test_preprocess_keeps_real_units() -> None:
    # a size word not between a count and food is left alone
    assert _preprocess_fraction_words("200 g flour") == "200 g flour"


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


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "LINGUINE",
        "ASPARAGUS",
        "TO SERVE",
        "FOR THE BASIL SALSA VERDE",
        "For The Tofu Feta:",
        "Toppings:",
        "To Serve:",
        "Optional",
    ],
)
def test_section_headers_detected(raw: str) -> None:
    assert _is_section_header(raw, recipe_has_mixed_case=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "To serve Cooked Rice",
        "To serve Toasted Sourdough",
        "400 g cooked brown rice, for serving",
        "Olive Oil",
        "Salt and freshly ground black pepper",
        "Chopped parsley, to serve",
    ],
)
def test_real_ingredients_are_not_headers(raw: str) -> None:
    assert not _is_section_header(raw, recipe_has_mixed_case=True)


@pytest.mark.unit
def test_all_caps_recipe_keeps_its_ingredients() -> None:
    # A recipe typed entirely in capitals must not lose every line.
    assert not _is_section_header("LINGUINE", recipe_has_mixed_case=False)
