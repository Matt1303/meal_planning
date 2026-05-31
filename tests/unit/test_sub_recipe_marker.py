from __future__ import annotations

import pytest

from meal_planner.parse import detect_sub_recipe_name


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.5 litres Light Vegetable Broth (separate recipe)", "Light Vegetable Broth"),
        ("60 ml Biome Broth (separate recipe) or water", "Biome Broth"),
        ("Brazil Nut Parm (separate recipe)", "Brazil Nut Parm"),
        ("60 grams Pumpkin Hummus (separate recipe) or other hummus", "Pumpkin Hummus"),
        ("Creamy Coriander Sauce (separate recipe), to serve", "Creamy Coriander Sauce"),
        ("Jicama Fries (separate recipe), for serving", "Jicama Fries"),
        ("250 ml Almond Milk (separate recipe)", "Almond Milk"),
        ("120 grams reserved 4-Bean Chilli (separate recipe)", "4-Bean Chilli"),
        ("Pumpkin Seed Parmesan (separate recipe), to serve", "Pumpkin Seed Parmesan"),
        ("2 roasted red peppers (separate recipe,or shop-bought), chopped", "roasted red peppers"),
    ],
)
def test_detect_sub_recipe_name(raw: str, expected: str) -> None:
    name = detect_sub_recipe_name(raw)
    assert name is not None
    assert expected.lower() in name.lower()


@pytest.mark.unit
def test_detect_sub_recipe_name_returns_none_for_normal_ingredient() -> None:
    assert detect_sub_recipe_name("200 g black beans, drained") is None
    assert detect_sub_recipe_name("1 tbsp olive oil") is None
    assert detect_sub_recipe_name("1 onion, finely chopped") is None


@pytest.mark.unit
def test_detect_sub_recipe_name_handles_see_recipe_variant() -> None:
    assert detect_sub_recipe_name("60 grams Tahini Dressing (see recipe)") == "Tahini Dressing"


@pytest.mark.unit
def test_detect_sub_recipe_name_strips_wrapper_prefixes() -> None:
    assert detect_sub_recipe_name("Leftover Muhammara Dip (separate recipe)") == "Muhammara Dip"
    assert (
        detect_sub_recipe_name("1 recipe Crispy Baked Tofu (separate recipe)")
        == "Crispy Baked Tofu"
    )
    assert detect_sub_recipe_name("Reserved 4-Bean Chilli (separate recipe)") == "4-Bean Chilli"


@pytest.mark.unit
def test_detect_sub_recipe_name_handles_recipe_above_below() -> None:
    assert detect_sub_recipe_name("100 g Lentil Sauce (recipe above)") == "Lentil Sauce"
    assert detect_sub_recipe_name("100 g Lentil Sauce (recipe below)") == "Lentil Sauce"
