from __future__ import annotations

import os

from meal_planner.config import LLMSettings
from meal_planner.llm.base import LLMClient, NullLLM

_PROVIDER_ENV_FALLBACKS: dict[str, tuple[str, ...]] = {
    "anthropic": ("LLM_API_KEY", "ANTHROPIC_API_KEY"),
    "openai": ("LLM_API_KEY", "OPENAI_API_KEY"),
}


def get_llm_client(settings: LLMSettings) -> LLMClient:
    provider = (settings.provider or "").lower()
    api_key = settings.api_key
    if not api_key:
        for env_name in _PROVIDER_ENV_FALLBACKS.get(provider, ("LLM_API_KEY",)):
            value = os.getenv(env_name)
            if value:
                api_key = value
                break
    if not api_key or not provider:
        return NullLLM()
    if provider == "anthropic":
        from meal_planner.llm.anthropic_client import AnthropicLLM

        return AnthropicLLM(api_key=api_key, model=settings.model)
    if provider == "openai":
        from meal_planner.llm.openai_client import OpenAILLM

        return OpenAILLM(api_key=api_key, model=settings.model)
    return NullLLM()
