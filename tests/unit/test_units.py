from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from meal_planner.units import UnitTable


@pytest.fixture
def units() -> UnitTable:
    return UnitTable.from_paths(
        Path("config/unit_grams.csv"),
        Path("config/density_g_per_ml.csv"),
        piece_path=Path("config/piece_grams.csv"),
    )


@pytest.mark.unit
def test_kg(units: UnitTable) -> None:
    assert units.to_grams(Decimal(1), "kg") == Decimal(1000)


@pytest.mark.unit
def test_tbsp(units: UnitTable) -> None:
    assert units.to_grams(Decimal(2), "tbsp") == Decimal(30)


@pytest.mark.unit
def test_cup_with_density_for_rice(units: UnitTable) -> None:
    grams = units.to_grams(Decimal(1), "cup", "brown rice")
    assert grams is not None
    assert Decimal(195) <= grams <= Decimal(220)


@pytest.mark.unit
def test_cup_for_oats_lighter_than_water(units: UnitTable) -> None:
    grams = units.to_grams(Decimal(1), "cup", "oats")
    assert grams is not None
    assert grams < Decimal(110)


@pytest.mark.unit
def test_unknown_unit_returns_none(units: UnitTable) -> None:
    assert units.to_grams(Decimal(1), "barrel") is None


@pytest.mark.unit
def test_pinch(units: UnitTable) -> None:
    grams = units.to_grams(Decimal(2), "pinch")
    assert grams == Decimal("0.6")


@pytest.mark.unit
def test_known_unit(units: UnitTable) -> None:
    assert units.known_unit("tsp")
    assert not units.known_unit("squiggle")
    assert not units.known_unit(None)


@pytest.mark.unit
def test_piece_lookup_for_banana_without_unit(units: UnitTable) -> None:
    assert units.to_grams(Decimal(1), None, "banana") == Decimal(120)


@pytest.mark.unit
def test_piece_lookup_half_broccoli(units: UnitTable) -> None:
    grams = units.to_grams(Decimal("0.5"), None, "broccoli")
    assert grams is not None
    assert Decimal(200) <= grams <= Decimal(500)


@pytest.mark.unit
def test_dimensionless_unit_falls_back_to_piece(units: UnitTable) -> None:
    assert units.to_grams(Decimal(1), "each", "avocado") == Decimal(200)


@pytest.mark.unit
def test_cubic_centimetre_alias_resolves(units: UnitTable) -> None:
    assert units.to_grams(Decimal(120), "cubic centimetre", "almond milk") == Decimal(120)


@pytest.mark.unit
def test_known_unit_recognises_alias(units: UnitTable) -> None:
    assert units.known_unit("cubic centimetre")


@pytest.mark.unit
def test_size_adjective_treated_as_piece(units: UnitTable) -> None:
    # quantulum reads "1 large onion" with unit "large"
    assert units.to_grams(Decimal(1), "large", "onion") == Decimal(150)
    assert units.to_grams(Decimal(1), "medium", "carrot") == Decimal(75)


@pytest.mark.unit
def test_parenthetical_canonical_resolves_piece(units: UnitTable) -> None:
    # canonical "courgettes (zucchini)" should hit the "courgettes" piece weight
    grams = units.to_grams(Decimal(1), None, "courgettes (zucchini)")
    assert grams is not None and grams > 0
    grams2 = units.to_grams(Decimal(1), "large", "aubergine (eggplant)")
    assert grams2 is not None and grams2 > 0
