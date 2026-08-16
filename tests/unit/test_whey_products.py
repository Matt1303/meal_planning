from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.config import ProfileTargets, Settings, TopUpSettings, WheyProduct
from meal_planner.ui.data import _whey_meal


@pytest.mark.unit
def test_profile_without_an_override_uses_the_household_default() -> None:
    settings = Settings.load(Path("config/pipeline.yaml"))
    assert settings.whey_for("matt").kcal == settings.topup.default_whey.kcal


@pytest.mark.unit
def test_ellie_uses_her_own_leaner_product() -> None:
    settings = Settings.load(Path("config/pipeline.yaml"))
    ellie = settings.whey_for("ellie")
    assert "clear whey" in ellie.label.lower()
    assert ellie.kcal < settings.whey_for("matt").kcal


@pytest.mark.unit
def test_unknown_profile_falls_back_rather_than_raising() -> None:
    settings = Settings.load(Path("config/pipeline.yaml"))
    assert settings.whey_for("nobody").kcal == settings.topup.default_whey.kcal


@pytest.mark.unit
def test_displayed_topup_uses_that_profile_s_macros() -> None:
    # The scoop count comes from the solver; the macros must be the ones the
    # solver costed it at, or the day's totals won't reconcile with the meals.
    clear = WheyProduct(
        label="Clear whey", scoop_grams=25, kcal=85, protein_g=20, fat_g=0.9, carbs_g=0.9
    )
    meal = _whey_meal(clear, 2)
    assert meal.kcal == pytest.approx(170.0)
    assert meal.protein_g == pytest.approx(40.0)
    assert "Clear whey" in (meal.title or "")


@pytest.mark.unit
def test_default_whey_round_trips_the_flat_topup_fields() -> None:
    topup = TopUpSettings(whey_kcal=95.3, whey_protein_g=20.2, whey_scoop_grams=25)
    product = topup.default_whey
    assert (product.kcal, product.protein_g, product.scoop_grams) == (95.3, 20.2, 25)


@pytest.mark.unit
def test_a_profile_override_is_optional() -> None:
    assert ProfileTargets(name="x").whey is None


@pytest.mark.unit
def test_whey_has_a_one_scoop_floor() -> None:
    # Either no shake, or a real one. A tenth of a scoop is 2.5 g of powder and
    # not something anyone measures out; the protein floor is soft, so a day
    # that can't justify a whole scoop goes without rather than shaving one.
    assert Settings.load(Path("config/pipeline.yaml")).topup.min_whey_scoops == 1.0
