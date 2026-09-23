from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from meal_planner.config import DietRules, HouseholdSettings, ProfileTargets, Settings
from meal_planner.diet import IngredientSwap, compute_diet_adjustments, load_swaps, save_swaps


def _swaps() -> dict[str, IngredientSwap]:
    return {
        "cooked rice": IngredientSwap(
            from_canonical="cooked rice",
            to_name="Cauliflower rice",
            kcal_per_100g=25.0,
            protein_g_per_100g=1.9,
            fat_g_per_100g=0.3,
            carbs_g_per_100g=3.0,
            fiber_g_per_100g=1.8,
        )
    }


def _settings(max_carbs: float = 20.0) -> Settings:
    base = Settings.load(Path("config/pipeline.yaml"))
    household = HouseholdSettings(
        profiles=[
            ProfileTargets(name="matt"),
            ProfileTargets(
                name="ellie",
                carbs_daily_max=50,
                diet=DietRules(max_carbs_per_serving=max_carbs),
            ),
        ],
        shared_meal_types=["lunch", "dinner"],
    )
    return base.model_copy(update={"household": household})


def _frames(fiber_is_null: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    lines = pd.DataFrame(
        {
            "recipe_id": [1],
            "ingredient_canonical": ["cooked rice"],
            "per_serving_grams": [250.0],
            "kcal_per_100g": [130.0],
            # The cache leaves plenty of these NULL, which arrives as NaN.
            "fiber_g_per_100g": [float("nan") if fiber_is_null else 0.4],
            "protein_g_per_100g": [2.7],
            "fat_g_per_100g": [0.3],
            "carbs_g_per_100g": [28.0],
        }
    )
    nutrition = pd.DataFrame(
        {
            "recipe_id": [1],
            "per_serving_kcal": [600.0],
            "per_serving_fiber_g": [9.0],
            "per_serving_protein_g": [20.0],
            "per_serving_fat_g": [18.0],
            "per_serving_carbs_g": [80.0],
        }
    )
    return lines, nutrition


@pytest.mark.unit
def test_no_dieters_means_no_adjustments() -> None:
    base = Settings.load(Path("config/pipeline.yaml"))
    plain = base.model_copy(
        update={
            "household": HouseholdSettings(
                profiles=[ProfileTargets(name="matt")], shared_meal_types=["dinner"]
            )
        }
    )
    lines, nutrition = _frames()
    adj = compute_diet_adjustments(lines, nutrition, plain, _swaps())
    assert not adj.active
    assert adj.deltas == {}


@pytest.mark.unit
def test_swapping_rice_drops_the_carbs() -> None:
    lines, nutrition = _frames()
    adj = compute_diet_adjustments(lines, nutrition, _settings(), _swaps())
    carbs_delta = adj.deltas[(1, "ellie")][4]
    # 250 g of rice at 28 g/100 g becomes 250 g of cauliflower at 3 g/100 g.
    assert carbs_delta == pytest.approx((3.0 - 28.0) * 250 / 100)
    assert 1 in adj.eligible["ellie"]  # 80 - 62.5 = 17.5 g, under the 20 g cap


@pytest.mark.unit
def test_a_dish_still_too_carby_after_swapping_is_excluded() -> None:
    lines, nutrition = _frames()
    adj = compute_diet_adjustments(lines, nutrition, _settings(max_carbs=10.0), _swaps())
    assert 1 not in adj.eligible["ellie"]


@pytest.mark.unit
def test_a_null_cached_macro_never_becomes_nan() -> None:
    # A NaN coefficient makes HiGHS drop every row and "solve" an empty model,
    # which looked like a plan meeting every target on 4,685 kcal a day.
    lines, nutrition = _frames(fiber_is_null=True)
    adj = compute_diet_adjustments(lines, nutrition, _settings(), _swaps())
    for value in adj.deltas[(1, "ellie")]:
        assert not math.isnan(value)


@pytest.mark.unit
def test_a_swap_can_never_take_a_macro_below_zero() -> None:
    # Declared-nutrition recipes carry an external total that is not the sum of
    # their lines, so removing an ingredient in full can overshoot it.
    lines, nutrition = _frames()
    nutrition.loc[0, "per_serving_carbs_g"] = 10.0  # less than the rice alone
    adj = compute_diet_adjustments(lines, nutrition, _settings(), _swaps())
    assert 10.0 + adj.deltas[(1, "ellie")][4] >= 0.0


@pytest.mark.unit
def test_swaps_round_trip_through_the_csv(tmp_path: Path) -> None:
    path = tmp_path / "swaps.csv"
    save_swaps(path, _swaps())
    assert load_swaps(path)["cooked rice"].to_name == "Cauliflower rice"


@pytest.mark.unit
def test_a_missing_swaps_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_swaps(tmp_path / "nope.csv") == {}


@pytest.mark.unit
def test_oil_top_up_is_only_built_for_a_profile_with_a_fat_floor() -> None:
    # Matt sets no fat floor, so he must gain no variables from this at all.
    from meal_planner.config import Settings as S

    base = S.load(Path("config/pipeline.yaml"))
    fat_floors = {p.name: p.fat_daily_min for p in base.household.profiles}
    assert fat_floors["matt"] is None
    assert fat_floors["ellie"] is not None


@pytest.mark.unit
def test_oil_meal_reports_what_it_adds() -> None:
    from meal_planner.config import TopUpSettings
    from meal_planner.ui.data import _oil_meal

    meal = _oil_meal(TopUpSettings(), 27.0)
    # Pure fat: no carbs, no protein, and the calories follow the grams.
    assert meal.fat_g == pytest.approx(27.0)
    assert meal.carbs_g == 0.0
    assert meal.protein_g == 0.0
    assert meal.kcal == pytest.approx(27.0 * TopUpSettings().oil_kcal_per_g)
    assert meal.is_topup
