"""Fábrica do modelo: Featherless (perk do evento) primário, Claude como fallback.

Basta UMA das chaves para a app funcionar; com as duas, o fallback segura falhas
do primário (saldo, rate limit, indisponibilidade) sem derrubar a demo.
"""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from app.settings import get_settings


def _featherless(settings) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.featherless_model,
        base_url=settings.featherless_base_url,
        api_key=settings.featherless_api_key,
        max_tokens=settings.max_tokens,
        timeout=60.0,
        max_retries=1,
    )


def _anthropic(settings) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.anthropic_model,
        max_tokens=settings.max_tokens,
        api_key=settings.anthropic_api_key,
        timeout=60.0,
        max_retries=1,
    )


@lru_cache
def build_llm() -> BaseChatModel:
    settings = get_settings()
    models: list[BaseChatModel] = []
    if settings.featherless_api_key is not None:
        models.append(_featherless(settings))
    if settings.anthropic_api_key is not None:
        models.append(_anthropic(settings))
    if not models:
        raise RuntimeError("Configure FEATHERLESS_API_KEY e/ou ANTHROPIC_API_KEY")
    primary, *fallbacks = models
    return primary.with_fallbacks(fallbacks) if fallbacks else primary
