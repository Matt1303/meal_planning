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


class NutritionQuery(BaseModel):
    ingredient_canonical: str
    sample_raw_text: str


class PortionEstimate(BaseModel):
    ingredient_canonical: str
    grams_per_portion: float | None = None
    note: str | None = None


class NutritionMacros(BaseModel):
    ingredient_canonical: str
    kcal_per_100g: float | None = None
    protein_g_per_100g: float | None = None
    fiber_g_per_100g: float | None = None
    fat_g_per_100g: float | None = None
    carbs_g_per_100g: float | None = None
    confidence: str = Field(default="medium", pattern="^(high|medium|low|unknown)$")
    notes: str | None = None


class KetoSwapSuggestion(BaseModel):
    """A low-carb stand-in for a high-carb ingredient, with its own macros.

    The macros ride along rather than pointing at a canonical, because most
    substitutes (cauliflower rice, courgette spirals, konjac noodles) are not
    in the catalogue and would otherwise need the whole enrichment pipeline
    run against them first.
    """

    from_canonical: str
    to_name: str
    kcal_per_100g: float = Field(ge=0)
    protein_g_per_100g: float = Field(ge=0)
    fat_g_per_100g: float = Field(ge=0)
    carbs_g_per_100g: float = Field(ge=0)
    fiber_g_per_100g: float = Field(ge=0)
    # Grams of substitute per gram replaced — cauliflower rice is used about
    # volume-for-volume, so usually 1.
    gram_ratio: float = Field(default=1.0, gt=0)
    note: str = ""


class LLMClient(Protocol):
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse: ...

    def verify_nutrition_matches(
        self, candidates: Sequence[NutritionMatchCandidate]
    ) -> list[NutritionMatchVerdict]: ...

    def fetch_nutrition_macros(
        self, queries: Sequence[NutritionQuery]
    ) -> list[NutritionMacros]: ...

    def estimate_portions(self, queries: Sequence[NutritionQuery]) -> list[PortionEstimate]: ...

    def suggest_keto_swaps(self, canonicals: Sequence[str]) -> list[KetoSwapSuggestion]: ...


class NullLLM:
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse:
        return LLMResponse(items=[], usage=LLMUsage(), raw_text="")

    def verify_nutrition_matches(
        self, candidates: Sequence[NutritionMatchCandidate]
    ) -> list[NutritionMatchVerdict]:
        return []

    def fetch_nutrition_macros(self, queries: Sequence[NutritionQuery]) -> list[NutritionMacros]:
        return []

    def estimate_portions(self, queries: Sequence[NutritionQuery]) -> list[PortionEstimate]:
        return []

    def suggest_keto_swaps(self, canonicals: Sequence[str]) -> list[KetoSwapSuggestion]:
        return []
