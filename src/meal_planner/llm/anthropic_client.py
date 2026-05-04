from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from meal_planner.llm.base import LLMResponse, LLMUsage, ParsedLine


class AnthropicLLM:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        from anthropic import Anthropic

        self._client = Anthropic(api_key=api_key)
        self._model = model

    def parse_lines(self, lines: Sequence[str], food_groups: Sequence[str]) -> LLMResponse:
        if not lines:
            return LLMResponse(items=[], usage=LLMUsage(), raw_text="")

        groups = ", ".join(food_groups)
        system_prompt = (
            "You are an ingredient parser for plant-based recipes. "
            "Given an ingredient line, return JSON with raw_text, ingredient_name, "
            "quantity_value, quantity_unit, and food_group. "
            f"food_group must be exactly one of: {groups}.\n"
            "Return only a JSON array of objects, no prose."
        )
        user_prompt = "\n".join(lines)

        system_blocks: list[Any] = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            temperature=0,
            system=system_blocks,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = _extract_text(resp.content)
        usage = _extract_usage(resp)
        items = _parse_array(raw, lines)
        if not items:
            retry_text = "Reply with ONLY a JSON array. " + user_prompt
            resp2 = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                temperature=0,
                system=system_prompt,
                messages=[{"role": "user", "content": retry_text}],
            )
            retry_raw = _extract_text(resp2.content)
            items = _parse_array(retry_raw, lines)
            if items:
                raw = retry_raw
        return LLMResponse(items=items, usage=usage, raw_text=raw)


def _extract_text(content: object) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        text_value = getattr(block, "text", None)
        if isinstance(text_value, str):
            parts.append(text_value)
    return "\n".join(parts)


def _extract_usage(resp: object) -> LLMUsage:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return LLMUsage()
    return LLMUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
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
