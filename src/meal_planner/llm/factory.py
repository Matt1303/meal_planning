from __future__ import annotations

import os

from meal_planner.config import LLMSettings
from meal_planner.llm.base import LLMClient, NullLLM


def get_llm_client(settings: LLMSettings) -> LLMClient:
    api_key = settings.api_key or os.getenv("LLM_API_KEY", "")
    if not api_key or not settings.provider:
        return NullLLM()
    provider = settings.provider.lower()
    if provider == "anthropic":
        from meal_planner.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(api_key=api_key, model=settings.model)
    if provider == "openai":
        from meal_planner.llm.openai_client import OpenAILLM

        return OpenAILLM(api_key=api_key, model=settings.model)
    return NullLLM()
