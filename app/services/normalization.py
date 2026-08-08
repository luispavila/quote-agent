import re
from functools import lru_cache

from app.schemas import (
    AttributeExtraction,
    MissingInformation,
    NormalizationExtraction,
)
from app.settings import get_settings


SYSTEM_PROMPT = """Você normaliza itens de compras de construção civil.
Extraia somente fatos apoiados pelo texto. Não invente marca, dimensão, norma,
resistência, embalagem ou aplicação. Use categoria canônica em inglês,
UPPER_SNAKE_CASE. Campos críticos inferidos devem aparecer em missing_information.
Retorne uma descrição normalizada somente quando não houver informação crítica ausente.
"""


def _find(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1) if match else None


def heuristic_normalize(description: str) -> NormalizationExtraction:
    text = " ".join(description.strip().split())
    lowered = text.lower()
    attributes: list[AttributeExtraction] = []
    missing: list[MissingInformation] = []

    if "cimento" in lowered:
        category, label = "CEMENT", "Cimento"
        cement_type = _find(r"\b(cp[- ]?(?:i{1,3}|iv|v)(?:-[a-z])?)\b", text)
        weight = _find(r"(\d+(?:[.,]\d+)?)\s*kg\b", text)
        strength = _find(r"(?:classe|resist[eê]ncia)\s*(?:de\s*)?(25|32|40)\b", text)
        if cement_type:
            attributes.append(AttributeExtraction(key="cementType", label="Tipo", value=cement_type.upper().replace(" ", "-"), source="USER_EXPLICIT", evidence=cement_type, confidence=.98))
        else:
            missing.append(MissingInformation(key="cementType", label="Tipo de cimento", reason="Tipos diferentes não são diretamente comparáveis.", suggested_question="Qual tipo de cimento deve ser cotado?", suggested_options=["CP-II", "CP-III", "CP-IV", "CP-V"]))
        if weight:
            attributes.append(AttributeExtraction(key="packageWeight", label="Peso da embalagem", value=float(weight.replace(",", ".")), unit="KG", source="USER_EXPLICIT", evidence=f"{weight} kg", confidence=.99))
        else:
            missing.append(MissingInformation(key="packageWeight", label="Peso da embalagem", reason="O preço por saco depende do peso.", suggested_question="Qual o peso de cada saco?", suggested_options=["40 kg", "50 kg"]))
        if strength:
            attributes.append(AttributeExtraction(key="strengthClass", label="Classe", value=strength, source="USER_EXPLICIT", evidence=strength, confidence=.95))
        else:
            missing.append(MissingInformation(key="strengthClass", label="Classe de resistência", reason="Classes diferentes podem produzir ofertas tecnicamente não equivalentes.", suggested_question="Qual classe de resistência deve ser cotada?", suggested_options=["25", "32", "40", "Aceito qualquer classe"]))
    elif "argamassa" in lowered:
        category, label = "MORTAR", "Argamassa"
        mortar_type = _find(r"\b(ac[- ]?(?:i{1,3}))\b", text)
        weight = _find(r"(\d+(?:[.,]\d+)?)\s*kg\b", text)
        if mortar_type:
            attributes.append(AttributeExtraction(key="mortarType", label="Tipo", value=mortar_type.upper().replace(" ", "-"), source="USER_EXPLICIT", evidence=mortar_type, confidence=.98))
        else:
            missing.append(MissingInformation(key="mortarType", label="Tipo de argamassa", reason="A aplicação muda por classe.", suggested_question="Qual tipo de argamassa deve ser cotado?", suggested_options=["AC-I", "AC-II", "AC-III"]))
        if weight:
            attributes.append(AttributeExtraction(key="packageWeight", label="Peso da embalagem", value=float(weight.replace(",", ".")), unit="KG", source="USER_EXPLICIT", evidence=f"{weight} kg", confidence=.99))
    elif "bloco" in lowered:
        category, label = "MASONRY_BLOCK", "Bloco de alvenaria"
        material = "CONCRETE" if "concreto" in lowered else "CERAMIC" if "cerâm" in lowered or "ceram" in lowered else None
        dimensions = re.search(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
        if material:
            attributes.append(AttributeExtraction(key="material", label="Material", value=material, source="USER_EXPLICIT", evidence="concreto" if material == "CONCRETE" else "cerâmico", confidence=.98))
        else:
            missing.append(MissingInformation(key="material", label="Material", reason="Blocos de materiais diferentes não são equivalentes.", suggested_question="Qual é o material do bloco?", suggested_options=["Concreto", "Cerâmico"]))
        if dimensions:
            value = "x".join(dimensions.groups())
            attributes.append(AttributeExtraction(key="dimensions", label="Dimensões", value=value, unit="CM", source="USER_EXPLICIT", evidence=dimensions.group(0), confidence=.99))
        else:
            missing.append(MissingInformation(key="dimensions", label="Dimensões", reason="A dimensão define o produto.", suggested_question="Quais são as dimensões do bloco em centímetros?", suggested_options=[]))
    else:
        category = re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")[:80] or "OTHER"
        label = text[:100]
        missing.append(MissingInformation(key="technicalSpecification", label="Especificação técnica", reason="A descrição não contém detalhes suficientes para comparar propostas.", suggested_question="Quais características técnicas são obrigatórias para este item?", suggested_options=[]))

    normalized = None if missing else _normalized_text(label, attributes)
    return NormalizationExtraction(
        canonical_category=category,
        category_label=label,
        category_confidence=.96 if category != "OTHER" else .7,
        attributes=attributes,
        missing_information=missing,
        normalized_description=normalized,
    )


def _normalized_text(label: str, attributes: list[AttributeExtraction]) -> str:
    values = [str(attribute.value) + (f" {attribute.unit.lower()}" if attribute.unit else "") for attribute in attributes]
    return ", ".join([label, *values])


@lru_cache
def _structured_llm():
    from app.llm import build_llm

    return build_llm().with_structured_output(NormalizationExtraction)


def normalize_item(description: str) -> NormalizationExtraction:
    settings = get_settings()
    if settings.featherless_api_key is None:
        return heuristic_normalize(description)
    try:
        return _structured_llm().invoke([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": description},
        ])
    except Exception:
        return heuristic_normalize(description)
