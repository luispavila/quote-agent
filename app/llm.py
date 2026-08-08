"""Fábrica do modelo Featherless usando a API compatível com OpenAI."""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from app.settings import get_settings


def _featherless_model() -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if settings.featherless_api_key is None:
        raise RuntimeError("FEATHERLESS_API_KEY não configurada")
    return ChatOpenAI(
        model=settings.featherless_model,
        base_url=settings.featherless_base_url.rstrip("/"),
        api_key=settings.featherless_api_key,
        temperature=settings.featherless_temperature,
        seed=42,
        max_tokens=settings.max_tokens,
        timeout=90.0,
        max_retries=2,
        default_headers={
            "HTTP-Referer": "https://github.com/luispavila/quote-agent",
            "X-Title": "Nexo Compras",
        },
    )


@lru_cache
def build_llm() -> BaseChatModel:
    settings = get_settings()
    if settings.llm_provider != "featherless":
        raise RuntimeError("LLM_PROVIDER deve ser 'featherless'")
    return _featherless_model()
