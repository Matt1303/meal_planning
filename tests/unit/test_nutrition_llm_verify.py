from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal

import pytest

from meal_planner.llm.base import (
    LLMResponse,
    LLMUsage,
    NutritionMatchCandidate,
    NutritionMatchVerdict,
)


class FakeLLM:
    def __init__(self, verdicts: list[NutritionMatchVerdict]) -> None:
        self.verdicts = verdicts
        self.calls: list[list[NutritionMatchCandidate]] = []

    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse:
        return LLMResponse(items=[], usage=LLMUsage(), raw_text="")

    def verify_nutrition_matches(
        self, candidates: Sequence[NutritionMatchCandidate]
    ) -> list[NutritionMatchVerdict]:
        self.calls.append(list(candidates))
        return list(self.verdicts)


@pytest.mark.unit
def test_anthropic_verdict_json_parse_round_trip() -> None:
    from meal_planner.llm.anthropic_client import _parse_verdicts

    candidates = [
        NutritionMatchCandidate(
            ingredient_canonical="lemon",
            ingredient_raw_text="1/2 lemon, juice only",
            matched_food_name="Lemon Curd",
            match_source="open_food_facts",
            match_score=85.0,
            kcal_per_100g=320.0,
            protein_per_100g=2.0,
            fiber_per_100g=0.0,
        ),
        NutritionMatchCandidate(
            ingredient_canonical="vegetable stock",
            ingredient_raw_text="700 ml vegetable stock",
            matched_food_name="stock cubes, vegetable",
            match_source="cofid",
            match_score=88.0,
            kcal_per_100g=300.0,
            protein_per_100g=13.0,
            fiber_per_100g=0.0,
        ),
    ]
    llm_reply = json.dumps(
        [
            {
                "ingredient_canonical": "lemon",
                "decision": "alternative",
                "alternative_query": "raw lemon juice",
            },
            {
                "ingredient_canonical": "vegetable stock",
                "decision": "alternative",
                "alternative_query": "prepared vegetable stock",
            },
        ]
    )
    verdicts = _parse_verdicts(llm_reply, candidates)
    assert {v.ingredient_canonical for v in verdicts} == {"lemon", "vegetable stock"}
    assert all(v.decision == "alternative" for v in verdicts)


@pytest.mark.unit
def test_anthropic_verdict_filters_unknown_canonical() -> None:
    from meal_planner.llm.anthropic_client import _parse_verdicts

    candidates = [
        NutritionMatchCandidate(
            ingredient_canonical="lemon",
            ingredient_raw_text="1/2 lemon",
            matched_food_name="Lemon Curd",
            match_source="open_food_facts",
            match_score=85.0,
            kcal_per_100g=320.0,
            protein_per_100g=2.0,
            fiber_per_100g=0.0,
        )
    ]
    reply = json.dumps(
        [
            {"ingredient_canonical": "lemon", "decision": "reject"},
            {"ingredient_canonical": "phantom", "decision": "approve"},
        ]
    )
    verdicts = _parse_verdicts(reply, candidates)
    assert len(verdicts) == 1
    assert verdicts[0].ingredient_canonical == "lemon"


@pytest.mark.unit
def test_anthropic_verdict_handles_invalid_json() -> None:
    from meal_planner.llm.anthropic_client import _parse_verdicts

    assert _parse_verdicts("not json", []) == []


@pytest.mark.unit
def test_anthropic_verdict_strips_code_fences() -> None:
    from meal_planner.llm.anthropic_client import _parse_verdicts

    candidates = [
        NutritionMatchCandidate(
            ingredient_canonical="onion",
            ingredient_raw_text="1 onion",
            matched_food_name="Onion granules",
            match_source="open_food_facts",
            match_score=86.0,
            kcal_per_100g=271.0,
            protein_per_100g=7.6,
            fiber_per_100g=0.0,
        )
    ]
    fenced = (
        "```json\n"
        + json.dumps([{"ingredient_canonical": "onion", "decision": "reject"}])
        + "\n```"
    )
    verdicts = _parse_verdicts(fenced, candidates)
    assert verdicts[0].decision == "reject"


@pytest.mark.unit
def test_null_llm_verify_returns_empty() -> None:
    from meal_planner.llm.base import NullLLM

    llm = NullLLM()
    result = llm.verify_nutrition_matches([])
    assert result == []


@pytest.mark.unit
def test_cooking_oil_absorption_scales_grams() -> None:
    absorption = Decimal("0.5")
    per_serving = Decimal("22")
    effective = per_serving * absorption
    assert effective == Decimal("11.0")


@pytest.mark.unit
def test_parse_macros_round_trip() -> None:
    from meal_planner.llm.anthropic_client import _parse_macros
    from meal_planner.llm.base import NutritionQuery

    queries = [
        NutritionQuery(
            ingredient_canonical="vegetable stock", sample_raw_text="700 ml vegetable stock"
        ),
        NutritionQuery(ingredient_canonical="onion", sample_raw_text="1 onion"),
    ]
    reply = json.dumps(
        [
            {
                "ingredient_canonical": "vegetable stock",
                "kcal_per_100g": 5,
                "protein_g_per_100g": 0.3,
                "fiber_g_per_100g": 0,
                "fat_g_per_100g": 0.1,
                "carbs_g_per_100g": 0.7,
                "confidence": "high",
                "notes": "prepared liquid stock",
            },
            {
                "ingredient_canonical": "onion",
                "kcal_per_100g": 40,
                "protein_g_per_100g": 1.1,
                "fiber_g_per_100g": 1.7,
                "fat_g_per_100g": 0.1,
                "carbs_g_per_100g": 9.3,
                "confidence": "high",
            },
        ]
    )
    macros = _parse_macros(reply, queries)
    assert {m.ingredient_canonical for m in macros} == {"vegetable stock", "onion"}
    stock = next(m for m in macros if m.ingredient_canonical == "vegetable stock")
    assert stock.kcal_per_100g == 5
    assert stock.confidence == "high"


@pytest.mark.unit
def test_parse_macros_filters_unknown_canonical() -> None:
    from meal_planner.llm.anthropic_client import _parse_macros
    from meal_planner.llm.base import NutritionQuery

    queries = [NutritionQuery(ingredient_canonical="lemon", sample_raw_text="1/2 lemon")]
    reply = json.dumps(
        [
            {
                "ingredient_canonical": "lemon",
                "kcal_per_100g": 29,
                "protein_g_per_100g": 1.1,
                "fiber_g_per_100g": 2.8,
                "fat_g_per_100g": 0.3,
                "carbs_g_per_100g": 9.3,
                "confidence": "high",
            },
            {
                "ingredient_canonical": "phantom",
                "kcal_per_100g": 100,
                "protein_g_per_100g": 5,
                "fiber_g_per_100g": 1,
                "fat_g_per_100g": 0.5,
                "carbs_g_per_100g": 20,
                "confidence": "low",
            },
        ]
    )
    macros = _parse_macros(reply, queries)
    assert len(macros) == 1
    assert macros[0].ingredient_canonical == "lemon"


@pytest.mark.unit
def test_parse_macros_handles_invalid_json() -> None:
    from meal_planner.llm.anthropic_client import _parse_macros

    assert _parse_macros("not json at all", []) == []


@pytest.mark.unit
def test_confidence_threshold_gating() -> None:
    from meal_planner.nutrition import _confidence_meets

    assert _confidence_meets("high", "high")
    assert _confidence_meets("high", "medium")
    assert _confidence_meets("medium", "medium")
    assert not _confidence_meets("low", "medium")
    assert not _confidence_meets("unknown", "low")
