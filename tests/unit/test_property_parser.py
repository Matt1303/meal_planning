from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meal_planner.optimize.data import recency_score
from meal_planner.parse import regex_parse_quantity


@pytest.mark.unit
@given(grams=st.integers(min_value=1, max_value=10_000))
@settings(max_examples=50)
def test_grams_with_unit_parses_to_positive(grams: int) -> None:
    qty, unit = regex_parse_quantity(f"{grams} g lentils")
    assert qty == Decimal(grams)
    assert unit == "g"


@pytest.mark.unit
@given(days=st.integers(min_value=0, max_value=365))
@settings(max_examples=30)
def test_recency_in_unit_interval(days: int) -> None:
    today = date.today()
    score = recency_score(today - timedelta(days=days), 30)
    assert 0 <= score <= 1


@pytest.mark.unit
@given(
    days_a=st.integers(min_value=0, max_value=30), days_b=st.integers(min_value=31, max_value=120)
)
@settings(max_examples=30)
def test_recency_monotone(days_a: int, days_b: int) -> None:
    today = date.today()
    a = recency_score(today - timedelta(days=days_a), 30)
    b = recency_score(today - timedelta(days=days_b), 30)
    assert a >= b
