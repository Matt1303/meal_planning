from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from meal_planner.llm.base import (
    LLMResponse,
    LLMUsage,
    NutritionMacros,
    NutritionMatchCandidate,
    NutritionMatchVerdict,
    NutritionQuery,
    ParsedLine,
)


class OpenAILLM:
    def __init__(self, api_key: str, model: str = "gpt-4o") -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse:
        if not lines:
            return LLMResponse(items=[], usage=LLMUsage(), raw_text="")

        groups = ", ".join(food_groups)
        system_prompt = (
            "You are an ingredient parser. Reply with a JSON array of "
            "{raw_text, ingredient_name, quantity_value, quantity_unit, food_group}. "
            f"food_group must be one of: {groups}."
        )
        user_prompt = "\n".join(lines)

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        raw = _extract_text(resp)
        usage = _extract_usage(resp)
        items = _parse_array(raw, lines)
        if not items:
            retry = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt + " Reply with ONLY JSON."},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0,
            )
            retry_raw = _extract_text(retry)
            retry_items = _parse_array(retry_raw, lines)
            if retry_items:
                items = retry_items
                raw = retry_raw
        return LLMResponse(items=items, usage=usage, raw_text=raw)

    def verify_nutrition_matches(
        self, candidates: Sequence[NutritionMatchCandidate]
    ) -> list[NutritionMatchVerdict]:
        # Verification is implemented in the Anthropic client; OpenAI falls
        # back to no-op so existing config without OpenAI verify is unaffected.
        return []

    def fetch_nutrition_macros(self, queries: Sequence[NutritionQuery]) -> list[NutritionMacros]:
        # Direct-macros is implemented in the Anthropic client only for now.
        return []


def _extract_text(resp: object) -> str:
    choices = getattr(resp, "choices", None)
    if not choices:
        return ""
    first = cast(list[Any], choices)[0]
    message = getattr(first, "message", None)
    if message is None:
        return ""
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def _extract_usage(resp: object) -> LLMUsage:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return LLMUsage()
    return LLMUsage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


_ADAPTER = TypeAdapter(list[ParsedLine])


def _parse_array(text: str, lines: Sequence[str]) -> list[ParsedLine]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
    try:
        loaded = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []
    try:
        items = _ADAPTER.validate_python(loaded)
    except ValidationError:
        return []
    known = set(lines)
    return [item for item in items if item.raw_text in known]
