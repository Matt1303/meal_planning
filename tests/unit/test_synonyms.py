from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.config import Settings
from meal_planner.parse import ParseContext, _resolve_canonical, build_context


@pytest.fixture
def context() -> ParseContext:
    settings = Settings.load(Path("config/pipeline.yaml"))
    overrides: dict[str, tuple[str | None, str | None]] = {
        "1 secret onion": ("onions", "Other Vegetables"),
    }
    return build_context(settings, overrides)


@pytest.mark.unit
def test_override_takes_priority(context: ParseContext) -> None:
    canonical, group = _resolve_canonical("1 secret onion", "secret onion", context)
    assert canonical == "onions"
    assert group == "Other Vegetables"


@pytest.mark.unit
def test_synonym_used_when_no_override(context: ParseContext) -> None:
    canonical, group = _resolve_canonical("aubergine", "aubergine", context)
    assert canonical == "aubergine (eggplant)"
    assert group == "Other Vegetables"


@pytest.mark.unit
def test_fuzzy_below_threshold_returns_none(context: ParseContext) -> None:
    canonical, group = _resolve_canonical("xyzzy", "xyzzy", context)
    assert canonical is None
    assert group is None


@pytest.mark.unit
def test_resolve_known_canonical(context: ParseContext) -> None:
    canonical, group = _resolve_canonical("kale", "kale", context)
    assert canonical == "kale"
    assert group == "Cruciferous Vegetables"
