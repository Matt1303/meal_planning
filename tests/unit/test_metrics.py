from __future__ import annotations

import pytest

from meal_planner.metrics import METRIC_DESCRIPTIONS, MetricName


@pytest.mark.unit
def test_every_metric_has_description() -> None:
    for member in MetricName:
        assert member in METRIC_DESCRIPTIONS, f"missing description for {member.name}"
        assert METRIC_DESCRIPTIONS[member]


@pytest.mark.unit
def test_metric_values_unique() -> None:
    values = [m.value for m in MetricName]
    assert len(values) == len(set(values))
