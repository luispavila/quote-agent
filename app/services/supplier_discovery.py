"""Agente LangGraph para descoberta auditável de novos fornecedores na web."""

import html
import re
from functools import lru_cache
from typing import TypedDict
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.database import SessionLocal
from app.models import AgentEvent, ConstructionSite, PurchaseRequest, SupplierDiscovery
from app.settings import get_settings


class SearchPlan(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)


class Candidate(BaseModel):
    name: str
    website: str
    category: str
    city: str | None = None
    rationale: str
    confidence: float = Field(ge=0, le=1)


class CandidateBatch(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)


class DiscoveryState(TypedDict, total=False):
    request_id: str
    city: str
    categories: list[str]
    queries: list[str]
    results: list[dict]
    candidates: list[dict]


def _event(request_id: str, event_type: str, title: str, detail: str, payload: dict | None = None) -> None:
    with SessionLocal.begin() as session:
        session.add(AgentEvent(purchase_request_id=request_id, event_type=event_type, title=title, detail=detail, payload=payload or {}))


def load_context(state: DiscoveryState) -> dict:
    with SessionLocal() as session:
        request = session.get(PurchaseRequest, state["request_id"])
        if request is None:
            raise ValueError("Cotação não encontrada")
        site = session.get(ConstructionSite, request.construction_site_id)
        categories = sorted({item.canonical_category or item.category_label for item in request.items if item.canonical_category or item.category_label})
        city = str((site.delivery_address if site else {}).get("city", ""))
    _event(state["request_id"], "DISCOVERY_STARTED", "Busca de fornecedores iniciada", f"O agente iniciou a busca para {len(categories)} categorias na região de {city}.")
    return {"categories": categories, "city": city}


def plan_searches(state: DiscoveryState) -> dict:
    settings = get_settings()
    fallback = [f'fornecedor {category} construção civil {state["city"]} SP' for category in state["categories"]][:6]
    if settings.featherless_api_key is None:
        return {"queries": fallback}
    from app.llm import build_llm
    prompt = f"""Crie consultas curtas para encontrar distribuidores e fabricantes reais de materiais de construção.
Categorias: {state['categories']}. Região da obra: {state['city']}.
Priorize páginas empresariais, distribuidores regionais e fabricantes com atendimento comercial. Não busque marketplaces."""
    try:
        plan = build_llm().with_structured_output(SearchPlan).invoke(prompt)
        return {"queries": plan.queries}
    except Exception:
        return {"queries": fallback}


def _clean_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        target = parse_qs(parsed.query).get("uddg", [url])[0]
        return unquote(target)
    return url


def search_web(state: DiscoveryState) -> dict:
    settings = get_settings()
    found: list[dict] = []
    pattern = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
    with httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 CotaAI Supplier Discovery"}) as client:
        for query in state["queries"]:
            try:
                response = client.get(settings.web_search_url, params={"q": query})
                response.raise_for_status()
                for url, title in pattern.findall(response.text)[:8]:
                    clean_title = re.sub(r"<[^>]+>", "", html.unescape(title)).strip()
                    clean_url = _clean_url(html.unescape(url))
                    if clean_url.startswith("http"):
                        found.append({"title": clean_title, "url": clean_url, "query": query})
            except Exception:
                continue
    unique = {item["url"]: item for item in found}
    return {"results": list(unique.values())[:30]}


def qualify_results(state: DiscoveryState) -> dict:
    settings = get_settings()
    results = state.get("results", [])
    if not results:
        return {"candidates": []}
    def fallback_candidates() -> list[dict]:
        blocked = ("lista", "melhores", "guia", "notícia", "marketplace", "instagram", "facebook", "linkedin")
        usable = [row for row in results if not any(term in row["title"].lower() for term in blocked)] or results
        return [{"name": row["title"][:200], "website": row["url"], "category": state["categories"][0], "city": state["city"], "rationale": "Empresa encontrada na busca por categoria e região; dados comerciais e capacidade ainda requerem homologação humana.", "confidence": .45} for row in usable[:settings.supplier_discovery_limit]]
    if settings.featherless_api_key is None:
        return {"candidates": fallback_candidates()}
    from app.llm import build_llm
    prompt = f"""Você qualifica potenciais fornecedores para construção civil.
Selecione no máximo {settings.supplier_discovery_limit} empresas dos resultados abaixo.
Não invente empresa, URL, cidade ou capacidade. Use somente URLs fornecidas. Exclua marketplaces, diretórios, notícias e redes sociais.
Categorias: {state['categories']}. Cidade da obra: {state['city']}.
Resultados: {results}
Cada justificativa deve explicar a evidência e declarar que ainda requer homologação."""
    try:
        batch = build_llm().with_structured_output(CandidateBatch).invoke(prompt)
        allowed_hosts = {urlparse(row["url"]).netloc.removeprefix("www.") for row in results}
        qualified = [candidate.model_dump() for candidate in batch.candidates if urlparse(candidate.website).netloc.removeprefix("www.") in allowed_hosts][:settings.supplier_discovery_limit]
        return {"candidates": qualified or fallback_candidates()}
    except Exception:
        return {"candidates": fallback_candidates()}


def persist_candidates(state: DiscoveryState) -> dict:
    candidates = state.get("candidates", [])
    with SessionLocal.begin() as session:
        session.query(SupplierDiscovery).filter(SupplierDiscovery.purchase_request_id == state["request_id"]).delete()
        for candidate in candidates:
            session.add(SupplierDiscovery(purchase_request_id=state["request_id"], supplier_name=candidate["name"], website=candidate["website"], source_url=candidate["website"], category=candidate.get("category"), city=candidate.get("city"), confidence=candidate.get("confidence"), rationale=candidate.get("rationale")))
        session.add(AgentEvent(purchase_request_id=state["request_id"], event_type="DISCOVERY_COMPLETED", title="Busca de fornecedores concluída", detail=f"{len(candidates)} novos fornecedores foram sugeridos para homologação.", payload={"count": len(candidates)}))
    return {"candidates": candidates}


@lru_cache
def build_supplier_discovery_graph():
    graph = StateGraph(DiscoveryState)
    graph.add_node("load_context", load_context)
    graph.add_node("plan_searches", plan_searches)
    graph.add_node("search_web", search_web)
    graph.add_node("qualify_results", qualify_results)
    graph.add_node("persist_candidates", persist_candidates)
    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "plan_searches")
    graph.add_edge("plan_searches", "search_web")
    graph.add_edge("search_web", "qualify_results")
    graph.add_edge("qualify_results", "persist_candidates")
    graph.add_edge("persist_candidates", END)
    return graph.compile()


def run_supplier_discovery(request_id: str) -> None:
    try:
        build_supplier_discovery_graph().invoke({"request_id": request_id})
    except Exception as exc:
        _event(request_id, "DISCOVERY_FAILED", "Busca de fornecedores não concluída", str(exc)[:500])
