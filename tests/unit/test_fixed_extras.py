from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.config import FixedExtra, Settings


@pytest.mark.unit
def test_day_one_is_a_monday() -> None:
    # confirm_plan schedules day d as week_start + (d - 1) from a Monday, so the
    # weekday mapping has to start there or a "Monday" extra lands on Tuesday.
    monday_only = FixedExtra(name="x", weekdays=["mon"])
    assert sorted(monday_only.days_within(7)) == [1]


@pytest.mark.unit
def test_weekday_names_are_forgiving() -> None:
    assert FixedExtra(name="x", weekdays=["Monday"]).days_within(7) == (
        FixedExtra(name="x", weekdays=["MON"]).days_within(7)
    )


@pytest.mark.unit
def test_a_longer_horizon_repeats_the_weekday() -> None:
    # An 8-day plan spans two Mondays, so a Monday habit happens twice.
    assert sorted(FixedExtra(name="x", weekdays=["mon"]).days_within(8)) == [1, 8]


@pytest.mark.unit
def test_unknown_weekday_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown weekday"):
        FixedExtra(name="x", weekdays=["someday"])


@pytest.mark.unit
def test_a_four_day_habit_lands_on_the_right_days() -> None:
    # Asserts the mechanism, not whoever currently has a habit configured: the
    # shipped config's coffee comes and goes with Ellie's keto block, and a
    # test that tracked it failed the moment the coffee was suspended.
    coffee = FixedExtra(name="Iced coffee", weekdays=["mon", "wed", "thu", "sat"], kcal=247.0)
    assert sorted(coffee.days_within(8)) == [1, 3, 4, 6, 8]  # Mon, Wed, Thu, Sat, Mon
    assert sorted(coffee.days_within(7)) == [1, 3, 4, 6]


@pytest.mark.unit
def test_profiles_without_extras_are_unaffected() -> None:
    settings = Settings.load(Path("config/pipeline.yaml"))
    matt = next(p for p in settings.household.profiles if p.name == "matt")
    assert matt.fixed_extras == []
