"""Langfuse com no-op silencioso: sem LANGFUSE_*, a app roda normalmente sem tracing.

Tracing nunca pode derrubar a app — toda a inicialização é defensiva.
"""

import logging
from functools import lru_cache

from app.settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_langfuse_handler():
    settings = get_settings()
    if not settings.langfuse_enabled:
        logger.info("Langfuse desabilitado (LANGFUSE_PUBLIC_KEY/SECRET_KEY ausentes)")
        return None
    try:
        import os

        # o SDK v3 lê as credenciais do ambiente
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.langfuse_public_key or "")
        os.environ.setdefault(
            "LANGFUSE_SECRET_KEY",
            settings.langfuse_secret_key.get_secret_value() if settings.langfuse_secret_key else "",
        )
        os.environ.setdefault("LANGFUSE_HOST", settings.langfuse_host)

        from langfuse.langchain import CallbackHandler

        handler = CallbackHandler()
        logger.info("Langfuse habilitado (host=%s)", settings.langfuse_host)
        return handler
    except Exception:
        logger.exception("Falha ao inicializar Langfuse — seguindo sem tracing")
        return None


def callbacks() -> list:
    handler = get_langfuse_handler()
    return [handler] if handler else []
