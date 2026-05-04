from __future__ import annotations

import re

import pytest

from meal_planner.correlation import (
    current_correlation_id,
    new_correlation_id,
    reset,
    set_correlation_id,
)


@pytest.mark.unit
def test_new_id_is_uuid_like() -> None:
    cid = new_correlation_id()
    assert re.match(r"^[0-9a-f-]{36}$", cid)


@pytest.mark.unit
def test_current_id_creates_when_unset() -> None:
    reset()
    cid = current_correlation_id()
    assert isinstance(cid, str)
    assert cid


@pytest.mark.unit
def test_set_then_current() -> None:
    reset()
    set_correlation_id("abc-123")
    assert current_correlation_id() == "abc-123"
    reset()
