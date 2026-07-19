from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.config import Settings


@pytest.mark.unit
def test_cooked_canonicals_have_a_raw_conversion() -> None:
    # Lines whose grams are a cooked weight resolve to a "cooked X" canonical so
    # they are valued at cooked calories. The shopping list has to undo that to
    # buy the right amount of the dry ingredient.
    ratios = Settings.load(Path("config/pipeline.yaml")).optimizer.cooked_to_raw_ratios
    assert ratios["cooked rice"] == 3
    assert ratios["cooked pasta"] > 1


@pytest.mark.unit
def test_per_person_portions_is_gone() -> None:
    # Each person's share of a shared dish comes from the household's
    # shared_portion_min/max now. The old per-person grams never reached the
    # solver (its delta function was defined but never called), so keeping the
    # field would invite someone to set it and see nothing happen.
    optimizer = Settings.load(Path("config/pipeline.yaml")).optimizer
    assert not hasattr(optimizer, "per_person_portions")
