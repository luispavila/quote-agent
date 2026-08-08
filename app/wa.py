"""Cliente do wa-service (Baileys) — envio de texto com o token compartilhado."""

import logging
import re

import httpx

from app.settings import get_settings

logger = logging.getLogger(__name__)


def digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def phone_variants(phone: str) -> set[str]:
    """Variações BR do mesmo número (com/sem 55 e com/sem o 9º dígito)."""
    d = digits(phone)
    if not d:
        return set()
    base = d if d.startswith("55") else f"55{d}"
    variants = {base}
    local = base[2:]
    if len(local) == 10 and local[2] in "6789":
        variants.add(f"55{local[:2]}9{local[2:]}")
    if len(local) == 11 and local[2] == "9":
        variants.add(f"55{local[:2]}{local[3:]}")
    return variants


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
