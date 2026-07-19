from __future__ import annotations

import json
from typing import Any

import pytest

from meal_planner.llm.base import LLMResponse, NullLLM, ParsedLine


@pytest.mark.unit
def test_null_llm_returns_empty() -> None:
    llm = NullLLM()
    response = llm.parse_lines(["1 onion"], ["Other Vegetables"])
    assert isinstance(response, LLMResponse)
    assert response.items == []
    assert response.usage.input_tokens == 0


@pytest.mark.unit
def test_parsed_line_validation() -> None:
    raw = '[{"raw_text": "1 onion", "ingredient_name": "onion", "food_group": "Other Vegetables"}]'
    items = json.loads(raw)
    line = ParsedLine.model_validate(items[0])
    assert line.ingredient_name == "onion"
    assert line.food_group == "Other Vegetables"


@pytest.mark.unit
def test_anthropic_parse_array_extracts_known_lines() -> None:
    from meal_planner.llm.anthropic_client import _parse_array

    payload = json.dumps(
        [
            {
                "raw_text": "1 onion",
                "ingredient_name": "onion",
                "food_group": "Other Vegetables",
            },
            {
                "raw_text": "alien text",
                "ingredient_name": "alien",
                "food_group": "Beans",
            },
        ]
    )
    items = _parse_array(payload, ["1 onion"])
    assert len(items) == 1
    assert items[0].raw_text == "1 onion"


@pytest.mark.unit
def test_anthropic_parse_array_handles_fenced_json() -> None:
    from meal_planner.llm.anthropic_client import _parse_array

    payload = """```json
[{"raw_text": "1 carrot", "ingredient_name": "carrot", "food_group": "Other Vegetables"}]
```"""
    items = _parse_array(payload, ["1 carrot"])
    assert len(items) == 1
    assert items[0].ingredient_name == "carrot"


@pytest.mark.unit
def test_anthropic_parse_array_invalid_json() -> None:
    from meal_planner.llm.anthropic_client import _parse_array

    assert _parse_array("not json", ["1 carrot"]) == []


@pytest.mark.unit
def test_factory_returns_null_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty settings key is not on its own "no key" — the factory falls back
    # to the environment, so a real key in the developer's .env satisfied this
    # and the test only passed on machines that had none.
    for var in ("LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    from meal_planner.config import LLMSettings
    from meal_planner.llm.factory import get_llm_client

    settings = LLMSettings(api_key="")
    client = get_llm_client(settings)
    assert isinstance(client, NullLLM)


@pytest.mark.unit
def test_factory_falls_back_to_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fallback the test above has to defeat: this is how the pipeline picks
    # the key up from .env rather than the config file.
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    from meal_planner.config import LLMSettings
    from meal_planner.llm.factory import get_llm_client

    client = get_llm_client(LLMSettings(provider="anthropic", api_key=""))
    assert not isinstance(client, NullLLM)


@pytest.mark.unit
def test_factory_returns_null_for_unknown_provider() -> None:
    from meal_planner.config import LLMSettings
    from meal_planner.llm.factory import get_llm_client

    settings = LLMSettings(provider="bedrock", api_key="abc")
    client = get_llm_client(settings)
    assert isinstance(client, NullLLM)


@pytest.mark.unit
def test_anthropic_extract_usage_handles_missing() -> None:
    from meal_planner.llm.anthropic_client import _extract_usage

    class Stub:
        usage: Any = None

    assert _extract_usage(Stub()).input_tokens == 0
