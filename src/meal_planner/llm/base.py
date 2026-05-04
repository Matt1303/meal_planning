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


class LLMClient(Protocol):
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse: ...


class NullLLM:
    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse:
        return LLMResponse(items=[], usage=LLMUsage(), raw_text="")
