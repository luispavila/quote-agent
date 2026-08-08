"""Fábrica do modelo: Claude primário, Featherless (OpenAI-compatível) como fallback opcional."""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from app.settings import get_settings


@lru_cache
def build_llm() -> BaseChatModel:
    settings = get_settings()
    if settings.anthropic_api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY não configurada")

    from langchain_anthropic import ChatAnthropic

    primary = ChatAnthropic(
        model=settings.anthropic_model,
        max_tokens=settings.max_tokens,
        api_key=settings.anthropic_api_key,
        timeout=60.0,
        max_retries=2,
    )

    if settings.featherless_api_key is None:
        return primary

    from langchain_openai import ChatOpenAI

    fallback = ChatOpenAI(
        model=settings.featherless_model,
        base_url=settings.featherless_base_url,
        api_key=settings.featherless_api_key,
        max_tokens=settings.max_tokens,
        timeout=60.0,
        max_retries=1,
    )
    return primary.with_fallbacks([fallback])
