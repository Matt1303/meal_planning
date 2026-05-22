from __future__ import annotations

import pytest
import responses

from meal_planner.nutrition import _lookup_open_food_facts


def _product(
    name: str, *, kcal: float | None = 200, protein: float | None = 10, fiber: float | None = 5
) -> dict[str, object]:
    nutriments: dict[str, object] = {}
    if kcal is not None:
        nutriments["energy-kcal_100g"] = kcal
    if protein is not None:
        nutriments["proteins_100g"] = protein
    if fiber is not None:
        nutriments["fiber_100g"] = fiber
    nutriments["carbohydrates_100g"] = 20
    nutriments["fat_100g"] = 1
    return {"product_name": name, "nutriments": nutriments}


def _mock_off(products: list[dict[str, object]]) -> None:
    responses.add(
        responses.GET,
        "https://world.openfoodfacts.org/cgi/search.pl",
        json={"products": products, "count": len(products)},
        status=200,
    )


@pytest.mark.unit
@responses.activate
def test_off_picks_clean_product() -> None:
    _mock_off([_product("Tofu", kcal=120, protein=13.6, fiber=2)])
    result = _lookup_open_food_facts(
        "tofu", user_agent="ua", timeout=5, enabled=True, countries="en:united-kingdom"
    )
    assert result is not None
    assert result.source == "open_food_facts"
    assert result.kcal_per_100g is not None
    assert float(result.kcal_per_100g) == pytest.approx(120, rel=0.01)


@pytest.mark.unit
@responses.activate
def test_off_rejects_composite_product_for_single_token_query() -> None:
    _mock_off([_product("Cocoa & Banana", kcal=200, protein=4)])
    result = _lookup_open_food_facts("banana", user_agent="ua", timeout=5, enabled=True)
    assert result is None


@pytest.mark.unit
@responses.activate
def test_off_rejects_chips_for_banana() -> None:
    _mock_off([_product("Banana chips", kcal=501, protein=1.9)])
    result = _lookup_open_food_facts("banana", user_agent="ua", timeout=5, enabled=True)
    assert result is None


@pytest.mark.unit
@responses.activate
def test_off_accepts_multi_token_plant_milk() -> None:
    _mock_off([_product("Organic Almond Milk", kcal=24, protein=0.9)])
    result = _lookup_open_food_facts("almond milk", user_agent="ua", timeout=5, enabled=True)
    assert result is not None
    assert "almond milk" in (result.match_source_name or "").lower()


@pytest.mark.unit
@responses.activate
def test_off_skips_products_with_missing_protein() -> None:
    _mock_off([_product("Tofu", kcal=120, protein=None)])
    result = _lookup_open_food_facts("tofu", user_agent="ua", timeout=5, enabled=True)
    assert result is None


@pytest.mark.unit
def test_off_disabled_returns_none() -> None:
    result = _lookup_open_food_facts("tofu", user_agent="ua", timeout=5, enabled=False)
    assert result is None
