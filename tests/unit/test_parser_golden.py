from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from meal_planner.config import Settings
from meal_planner.parse import (
    _quantulum_then_regex,
    _resolve_canonical,
    build_context,
    strip_quantity,
)


@pytest.fixture
def context():  # type: ignore[no-untyped-def]
    return build_context(Settings.load(Path("config/pipeline.yaml")), overrides={})


def _load_cases() -> list[dict[str, Any]]:
    path = Path("tests/fixtures/parser_golden.yaml")
    raw = yaml.safe_load(path.read_text())
    return cast(list[dict[str, Any]], raw["cases"])


@pytest.mark.unit
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: cast(str, c["raw"]))
def test_parser_golden(case: dict[str, Any], context) -> None:  # type: ignore[no-untyped-def]
    raw = case["raw"]
    name = strip_quantity(raw) or raw
    canonical, group = _resolve_canonical(raw, name, context)
    expected_canonical = case.get("expected_canonical")
    expected_group = case.get("expected_food_group")
    assert canonical == expected_canonical, f"canonical mismatch for {raw}: got {canonical}"
    assert group == expected_group, f"group mismatch for {raw}: got {group}"

    if "min_grams" in case or "max_grams" in case:
        qty_value, qty_unit = _quantulum_then_regex(raw, [0])
        grams = context.units.to_grams(qty_value, qty_unit, canonical)
        assert grams is not None, f"grams could not be computed for {raw}"
        if "min_grams" in case:
            assert grams >= case["min_grams"], f"{raw}: grams={grams}"
        if "max_grams" in case:
            assert grams <= case["max_grams"], f"{raw}: grams={grams}"
