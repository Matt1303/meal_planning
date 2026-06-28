from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from meal_planner import nutrition


@pytest.mark.unit
def test_off_circuit_breaker_trips_and_stops_calling() -> None:
    nutrition._reset_off_breaker()
    calls = [0]

    def boom(*_args: object, **_kwargs: object) -> object:
        calls[0] += 1
        raise requests.exceptions.ReadTimeout("timeout")

    with patch("meal_planner.nutrition.requests.get", boom):
        for i in range(8):
            nutrition._lookup_open_food_facts(f"thing{i}", user_agent="x", timeout=10, enabled=True)

    # 3 failed items × 3 retries each = 9 attempts, then OFF is disabled.
    assert nutrition._OFF_STATE["disabled"] is True
    assert calls[0] == 9


@pytest.mark.unit
def test_off_disabled_short_circuits_immediately() -> None:
    nutrition._reset_off_breaker()
    nutrition._OFF_STATE["disabled"] = True
    with patch("meal_planner.nutrition.requests.get", side_effect=AssertionError("called")):
        assert (
            nutrition._lookup_open_food_facts("x", user_agent="x", timeout=10, enabled=True) is None
        )
    nutrition._reset_off_breaker()
