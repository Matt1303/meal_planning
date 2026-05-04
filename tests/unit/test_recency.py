from __future__ import annotations

from datetime import date, timedelta

import pytest

from meal_planner.optimize.data import recency_score


@pytest.mark.unit
def test_recency_recent_high_penalty() -> None:
    today = date.today()
    score = recency_score(today - timedelta(days=1), 30)
    assert 0 < score <= 1


@pytest.mark.unit
def test_recency_old_low_penalty() -> None:
    today = date.today()
    score = recency_score(today - timedelta(days=120), 30)
    assert 0 <= score < 0.05


@pytest.mark.unit
def test_recency_none() -> None:
    assert recency_score(None, 30) == 0.0


@pytest.mark.unit
def test_recency_monotone_decreasing() -> None:
    today = date.today()
    a = recency_score(today - timedelta(days=1), 30)
    b = recency_score(today - timedelta(days=10), 30)
    c = recency_score(today - timedelta(days=30), 30)
    assert a > b > c
