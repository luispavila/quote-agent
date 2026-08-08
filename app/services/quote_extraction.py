"""Extrai preços estruturados de uma resposta de fornecedor em texto livre (WhatsApp).

Usa prompt JSON direto (não function-calling) para funcionar em qualquer provider
OpenAI-compatível, com heurística de fallback quando o LLM falha ou não há chave.
"""

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class QuoteLineExtraction(BaseModel):
    description: str = ""
    unit_price: float | None = None
    total_price: float | None = None


class QuoteExtraction(BaseModel):
    items: list[QuoteLineExtraction] = Field(default_factory=list)
    freight: float | None = None
    delivery_days: int | None = None
    payment_terms: str | None = None
    grand_total: float | None = None


PROMPT = (
    "Você extrai a cotação de um fornecedor a partir de uma mensagem de WhatsApp em português.\n"
    "Responda APENAS com um objeto JSON válido, sem texto antes ou depois, no formato:\n"
    '{"items":[{"description":str,"unit_price":number|null,"total_price":number|null}],'
    '"freight":number|null,"delivery_days":int|null,"payment_terms":str|null,"grand_total":number|null}\n'
    "Regras: valores em reais como número (1650.00, não \"R$ 1.650,00\"). "
    "Use null para o que não foi informado. delivery_days é o PRAZO DE ENTREGA em dias "
    "(não a condição de pagamento). Não invente valores.\n\n"
)


def _parse_money(raw: str) -> float | None:
    raw = raw.strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def heuristic_extract(text: str) -> QuoteExtraction:
    low = text.lower()
    # totais explícitos (após "=" ou "total")
    totals = [
        v for v in (_parse_money(m) for m in re.findall(r"(?:=|total[:\s]*)\s*r?\$?\s*([\d.,]+)", low))
        if v is not None
    ]
    freight = None
    fm = re.search(r"frete[:\s]*(gr[áa]tis|gratuito|r?\$?\s*[\d.,]+)", low)
    if fm:
        freight = 0.0 if fm.group(1).startswith("gr") else _parse_money(re.sub(r"[^\d.,]", "", fm.group(1)))
    # prazo de entrega: prioriza frases com "entreg"
    days = None
    dm = re.search(r"entreg\w*[^\d]{0,20}(\d+)\s*dias?", low) or re.search(r"(\d+)\s*dias?\s*(?:para|de)?\s*entreg", low)
    if dm:
        days = int(dm.group(1))
    grand = round(sum(totals) + (freight or 0), 2) if totals else None
    return QuoteExtraction(freight=freight, delivery_days=days, grand_total=grand)


def _extract_json(content: str) -> dict | None:
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def extract_quote(reply_text: str, item_descriptions: list[str]) -> QuoteExtraction:
    from app.settings import get_settings

    settings = get_settings()
    fallback = heuristic_extract(reply_text)
    if settings.featherless_api_key is None:
        return fallback
    try:
        from app.llm import build_llm
        from app.tracing import callbacks

        context = "Itens da cotação:\n" + "\n".join(f"- {d}" for d in item_descriptions)
        response = build_llm().invoke(
            PROMPT + f"{context}\n\nMensagem do fornecedor:\n{reply_text}",
            config={"callbacks": callbacks(), "run_name": "extrair-cotacao"},
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        if not data:
            return fallback
        result = QuoteExtraction.model_validate(data)
        if result.grand_total is None:
            item_totals = [i.total_price for i in result.items if i.total_price is not None]
            if item_totals:
                result.grand_total = round(sum(item_totals) + (result.freight or 0), 2)
        # se o LLM não achou nada útil, prefere a heurística
        if result.grand_total is None and fallback.grand_total is not None:
            return fallback
        return result
    except (ValidationError, Exception):
        logger.exception("falha ao extrair cotação — usando heurística")
        return fallback
