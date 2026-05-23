from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, Field


class ParsedLine(BaseModel):
    raw_text: str
    ingredient_name: str | None = None
    quantity_value: Decimal | None = None
    quantity_unit: str | None = None
    food_group: str | None = None


class LLMUsage(BaseModel):
    cache_read_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    items: list[ParsedLine] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    raw_text: str = ""


class NutritionMatchCandidate(BaseModel):
    ingredient_canonical: str
    ingredient_raw_text: str
    matched_food_name: str | None
    match_source: str
    match_score: float | None
    kcal_per_100g: float | None
    protein_per_100g: float | None
    fiber_per_100g: float | None


class NutritionMatchVerdict(BaseModel):
    ingredient_canonical: str
    decision: str = Field(pattern="^(approve|reject|alternative)$")
    alternative_query: str | None = None
    reason: str | None = None


class LLMClient(Protocol):
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse: ...

    def verify_nutrition_matches(
        self, candidates: Sequence[NutritionMatchCandidate]
    ) -> list[NutritionMatchVerdict]: ...


class NullLLM:
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse:
        return LLMResponse(items=[], usage=LLMUsage(), raw_text="")

    def verify_nutrition_matches(
        self, candidates: Sequence[NutritionMatchCandidate]
    ) -> list[NutritionMatchVerdict]:
        return []
