"""Cliente do wa-service (Baileys) — envio de texto com o token compartilhado."""

import logging

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)


def send_text(phone: str, text: str) -> bool:
    settings = get_settings()
    if not settings.wa_configured:
        logger.warning("wa-service não configurado — resposta descartada")
        return False
    try:
        resp = httpx.post(
            f"{settings.wa_service_url.rstrip('/')}/messages/text",
            json={"phone": phone, "text": text},
            headers={"x-wa-token": settings.wa_shared_token.get_secret_value()},
            timeout=30.0,
        )
        resp.raise_for_status()
        return True
    except httpx.HTTPError:
        logger.exception("falha ao enviar mensagem via wa-service")
        return False
