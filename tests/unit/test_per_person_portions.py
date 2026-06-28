from __future__ import annotations

import pytest

from meal_planner.config import PerPersonPortion, Settings
from meal_planner.optimize.data import _portion_delta

COOKED_RICE = [130.0, 0.4, 2.7, 0.3, 28.0]
DRY_RICE = [362.0, 3.6, 7.5, 2.9, 76.0]


@pytest.mark.unit
def test_computed_recipe_full_cooked_replacement() -> None:
    # Computed recipe: replace 250 g dry-valued rice with 400 g cooked for Matt.
    kcal = _portion_delta(250.0, 400.0, COOKED_RICE, DRY_RICE)[0]
    # 400 g cooked = 520 kcal; old 250 g * 362/100 = 905 kcal -> delta -385.
    assert kcal == pytest.approx(520.0 - 905.0)


@pytest.mark.unit
def test_declared_recipe_marginal_only() -> None:
    # Declared recipe: base valued at cooked too, so only the marginal applies.
    matt = _portion_delta(250.0, 400.0, COOKED_RICE, COOKED_RICE)[0]
    ellie = _portion_delta(250.0, 200.0, COOKED_RICE, COOKED_RICE)[0]
    assert matt == pytest.approx((400 - 250) * 130 / 100)  # +195
    assert ellie == pytest.approx((200 - 250) * 130 / 100)  # -65


@pytest.mark.unit
def test_settings_loads_per_person_portions() -> None:
    settings = Settings.load("config/pipeline.yaml")
    specs = settings.optimizer.per_person_portions
    assert specs, "expected rice per-person portions configured"
    rice = next(s for s in specs if "rice" in s.canonicals)
    assert isinstance(rice, PerPersonPortion)
    assert rice.value_as == "cooked rice"
    assert rice.estimated_only is True
    assert rice.grams == {"matt": 400, "ellie": 200}
    # cooked-weight defaults convert to dry weight on the shopping list
    assert rice.cooked_to_raw_ratio == 3
