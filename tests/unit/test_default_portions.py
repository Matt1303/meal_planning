from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from meal_planner.parse import _load_default_portions


@pytest.mark.unit
def test_load_default_portions_reads_csv() -> None:
    portions = _load_default_portions(Path("config/default_portion_grams.csv"))
    # the user's explicit case
    assert portions["rice"] == Decimal(250)
    assert portions["brown rice"] == Decimal(250)
    # a curated estimate
    assert portions["cheddar cheese"] == Decimal(30)
    assert portions["tortilla crisps"] == Decimal(30)


@pytest.mark.unit
def test_load_default_portions_missing_file(tmp_path: Path) -> None:
    assert _load_default_portions(tmp_path / "nope.csv") == {}


@pytest.mark.unit
def test_load_default_portions_skips_bad_rows(tmp_path: Path) -> None:
    csv = tmp_path / "p.csv"
    csv.write_text(
        "ingredient_canonical,grams_per_portion,note\n"
        "rice,250,cooked\n"
        "bad,notanumber,\n"
        ",100,blank canonical\n"
    )
    portions = _load_default_portions(csv)
    assert portions == {"rice": Decimal(250)}
