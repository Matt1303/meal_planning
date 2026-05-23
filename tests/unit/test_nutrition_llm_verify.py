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
