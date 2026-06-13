from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meal_planner.config import HouseholdSettings, ProfileTargets, Settings


@pytest.mark.unit
def test_pipeline_config_household_loads() -> None:
    # The shipped config defines the Matt + Ellie household; verify it loads
    # with shared lunch/dinner and Matt's pinned breakfast.
    settings = Settings.load(Path("config/pipeline.yaml"))
    names = {p.name for p in settings.household.profiles}
    assert names == {"matt", "ellie"}
    assert settings.household.shared_meal_types == ["lunch", "dinner"]
    matt = next(p for p in settings.household.profiles if p.name == "matt")
    assert matt.fixed_meals == {"breakfast": "Matt Breakfast Smoothie"}


@pytest.mark.unit
def test_household_empty_when_unset() -> None:
    household = HouseholdSettings()
    assert household.profiles == []
    assert household.shared_meal_types == []


@pytest.mark.unit
def test_household_two_profiles_valid() -> None:
    household = HouseholdSettings(
        profiles=[
            ProfileTargets(name="a", calories_daily_min=1800, protein_daily_min=70),
            ProfileTargets(name="b", calories_daily_min=2200, protein_daily_min=100),
        ],
        shared_meal_types=["lunch", "dinner"],
    )
    assert {p.name for p in household.profiles} == {"a", "b"}


@pytest.mark.unit
def test_profile_targets_validates_calorie_range() -> None:
    with pytest.raises(ValueError, match="calories_daily_min"):
        ProfileTargets(name="x", calories_daily_min=3000, calories_daily_max=2000)


@pytest.mark.unit
def test_profile_targets_validates_protein_range() -> None:
    with pytest.raises(ValueError, match="protein_daily_min"):
        ProfileTargets(name="x", protein_daily_min=150, protein_daily_max=100)


@pytest.mark.unit
def test_household_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="must be unique"):
        HouseholdSettings(
            profiles=[
                ProfileTargets(name="dup"),
                ProfileTargets(name="dup"),
            ]
        )


@pytest.mark.unit
def test_settings_validator_rejects_unknown_shared_meal(tmp_path: Path) -> None:
    raw = yaml.safe_load(Path("config/pipeline.yaml").read_text())
    raw["household"] = {
        "profiles": [],
        "shared_meal_types": ["foodfight"],
    }
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="unknown meal types"):
        Settings.load(cfg)
