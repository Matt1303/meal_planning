from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.config import ProfileTargets, Settings
from meal_planner.optimize.data import ProfileSpec


@pytest.mark.unit
def test_shared_portion_defaults_to_full_serving() -> None:
    spec = ProfileSpec.from_targets(ProfileTargets(name="x"))
    assert spec.shared_portion_min == 1.0
    assert spec.shared_portion_max == 1.0
    assert not spec.portion_is_flexible


@pytest.mark.unit
def test_shared_portion_range_is_flexible() -> None:
    spec = ProfileSpec.from_targets(
        ProfileTargets(name="x", shared_portion_min=0.5, shared_portion_max=1.0)
    )
    assert spec.portion_is_flexible


@pytest.mark.unit
def test_shared_portion_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="shared_portion_min"):
        ProfileTargets(name="x", shared_portion_min=1.0, shared_portion_max=0.5)


@pytest.mark.unit
def test_ellie_shares_a_part_portion_in_shipped_config() -> None:
    # Matt (3300 kcal) and Ellie (1500 kcal) eat the same lunch and dinner, so
    # Ellie needs a smaller plate for both calorie bands to be satisfiable.
    settings = Settings.load(Path("config/pipeline.yaml"))
    ellie = next(p for p in settings.household.profiles if p.name == "ellie")
    matt = next(p for p in settings.household.profiles if p.name == "matt")
    assert ellie.shared_portion_min < ellie.shared_portion_max
    assert matt.shared_portion_min == matt.shared_portion_max == 1.0
