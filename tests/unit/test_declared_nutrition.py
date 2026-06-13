from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from meal_planner.ingest import _parse_declared_nutrition


def _soup(body: str) -> BeautifulSoup:
    return BeautifulSoup(body, "html.parser")


@pytest.mark.unit
def test_parse_paprika_nutrition_block() -> None:
    html = (
        '<div itemprop="nutrition" class="nutrition text">'
        "<p>592 calories<br/>67g carbohydrate<br/>12g fat<br/>"
        "48g protein<br/>13g fibre</p></div>"
    )
    d = _parse_declared_nutrition(_soup(html), "div.nutrition.text")
    assert d.has_any
    assert d.kcal == 592
    assert d.carbs_g == 67
    assert d.fat_g == 12
    assert d.protein_g == 48
    assert d.fiber_g == 13


@pytest.mark.unit
def test_parse_handles_kcal_and_us_spelling() -> None:
    html = (
        '<div class="nutrition text">'
        "<p>320 kcal, 22 g protein, 8 g fiber, 14 g total fat, 30 g carbs</p></div>"
    )
    d = _parse_declared_nutrition(_soup(html), "div.nutrition.text")
    assert d.kcal == 320
    assert d.protein_g == 22
    assert d.fiber_g == 8
    assert d.fat_g == 14
    assert d.carbs_g == 30


@pytest.mark.unit
def test_parse_partial_block_leaves_missing_none() -> None:
    html = '<div class="nutrition text"><p>450 calories<br/>20g protein</p></div>'
    d = _parse_declared_nutrition(_soup(html), "div.nutrition.text")
    assert d.kcal == 450
    assert d.protein_g == 20
    assert d.fiber_g is None
    assert d.fat_g is None
    assert d.carbs_g is None


@pytest.mark.unit
def test_parse_no_nutrition_section() -> None:
    d = _parse_declared_nutrition(_soup("<div class='x'>nope</div>"), "div.nutrition.text")
    assert not d.has_any


@pytest.mark.unit
def test_parse_empty_selector() -> None:
    d = _parse_declared_nutrition(_soup("<div class='nutrition text'>x</div>"), "")
    assert not d.has_any
