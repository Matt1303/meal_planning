from __future__ import annotations

import pytest

from meal_planner.logging import configure, get_logger


@pytest.mark.unit
def test_configure_console() -> None:
    configure("DEBUG", "console")
    log = get_logger("test")
    log.info("ping", value=1)


@pytest.mark.unit
def test_configure_json() -> None:
    configure("INFO", "json")
    log = get_logger()
    log.warning("event", x=2)


@pytest.mark.unit
def test_configure_int_level() -> None:
    configure(20, "console")
