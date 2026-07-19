from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings
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
def test_shipped_config_redistributes_a_whole_batch() -> None:
    # Matt (3300 kcal) and Ellie (1500 kcal) eat the same lunch and dinner, so
    # the servings are split unevenly — but they still add to two per sitting,
    # which finishes a 4-serving batch over the fresh and leftover sittings.
    household = Settings.load(Path("config/pipeline.yaml")).household
    matt = next(p for p in household.profiles if p.name == "matt")
    ellie = next(p for p in household.profiles if p.name == "ellie")
    assert household.shared_servings_per_sitting == 2
    assert matt.shared_portion_max > 1.0 > ellie.shared_portion_min
    assert matt.shared_portion_min + ellie.shared_portion_max == 2
    assert matt.shared_portion_max + ellie.shared_portion_min == 2


@pytest.mark.unit
def test_rejects_total_the_profiles_cannot_reach() -> None:
    with pytest.raises(ValueError, match="outside the reachable range"):
        HouseholdSettings(
            profiles=[
                ProfileTargets(name="a", shared_portion_min=0.5, shared_portion_max=0.8),
                ProfileTargets(name="b", shared_portion_min=0.5, shared_portion_max=0.8),
            ],
            shared_meal_types=["dinner"],
            shared_servings_per_sitting=2,
        )


@pytest.mark.unit
def test_group_and_nutrition_slack_weights_are_independent() -> None:
    # They were one weight, so pushing harder on the Daily Dozen also tightened
    # the calorie and protein bands by the same factor. Defaulting them equal
    # keeps the previous behaviour until one is deliberately changed.
    optimizer = Settings.load(Path("config/pipeline.yaml")).optimizer
    assert optimizer.slack_weight == optimizer.group_slack_weight

    tuned = optimizer.model_copy(update={"group_slack_weight": 12.0})
    assert tuned.slack_weight == optimizer.slack_weight
    assert tuned.group_slack_weight == 12.0
