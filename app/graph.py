"""LangGraph do fluxo de sourcing: normalização, esclarecimento e seleção."""

from functools import lru_cache
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    AgentEvent,
    Clarification,
    ConstructionSite,
    ItemStatus,
    PurchaseRequest,
    RequestStatus,
    Supplier,
    SupplierSelection,
    utcnow,
)
from app.services.normalization import normalize_item
from app.services.supplier_selection import select_suppliers


class ProcurementState(TypedDict, total=False):
    request_id: str
    pending_clarifications: list[dict[str, Any]]
    clarification_answers: dict[str, str]
    selected_supplier_ids: list[str]
    status: str


def _event(session, request_id: str, event_type: str, title: str, detail: str, payload: dict | None = None) -> None:
    session.add(AgentEvent(
        purchase_request_id=request_id,
        event_type=event_type,
        title=title,
        detail=detail,
        payload=payload or {},
    ))


def normalize_node(state: ProcurementState) -> dict:
    with SessionLocal.begin() as session:
        request = session.get(PurchaseRequest, state["request_id"])
        if request is None:
            raise ValueError("Requisição não encontrada")
        request.status = RequestStatus.NORMALIZING
        pending = []
        for item in request.items:
            if item.status == ItemStatus.READY:
                continue
            item.status = ItemStatus.PROCESSING
            extraction = normalize_item(item.raw_description)
            item.canonical_category = extraction.canonical_category
            item.category_label = extraction.category_label
            item.confidence = extraction.category_confidence
            item.specifications = {
                attribute.key: {
                    "value": attribute.value,
                    "unit": attribute.unit,
                    "source": attribute.source,
                    "evidence": attribute.evidence,
                    "confidence": attribute.confidence,
                }
                for attribute in extraction.attributes
            }
            item.normalized_description = extraction.normalized_description
            item.missing_fields = [missing.key for missing in extraction.missing_information]
            if extraction.missing_information:
                item.status = ItemStatus.NEEDS_CLARIFICATION
                for missing in extraction.missing_information:
                    clarification = Clarification(
                        purchase_request_id=request.id,
                        item_id=item.id,
                        field_path=f"specifications.{missing.key}",
                        question=missing.suggested_question,
                        reason=missing.reason,
                        options=missing.suggested_options,
                    )
                    session.add(clarification)
                    session.flush()
                    pending.append({
                        "clarificationId": clarification.id,
                        "itemId": item.id,
                        "itemDescription": item.raw_description,
                        "field": clarification.field_path,
                        "question": clarification.question,
                        "reason": clarification.reason,
                        "options": clarification.options,
                    })
            else:
                item.status = ItemStatus.READY
        request.status = RequestStatus.CLARIFYING if pending else RequestStatus.READY
        _event(session, request.id, "NORMALIZATION_COMPLETED", "Itens analisados", f"{len(request.items)} itens normalizados; {len(pending)} esclarecimentos pendentes.", {"pending": len(pending)})
        return {"pending_clarifications": pending, "status": request.status.value}


def route_after_normalize(state: ProcurementState) -> Literal["await_clarification", "select_suppliers"]:
    return "await_clarification" if state.get("pending_clarifications") else "select_suppliers"


def clarification_node(state: ProcurementState) -> dict:
    answers = interrupt({
        "type": "CLARIFICATION_REQUIRED",
        "requestId": state["request_id"],
        "questions": state.get("pending_clarifications", []),
    })
    return {"clarification_answers": answers}


def apply_clarifications_node(state: ProcurementState) -> dict:
    answers = state.get("clarification_answers", {})
    with SessionLocal.begin() as session:
        request = session.get(PurchaseRequest, state["request_id"])
        if request is None:
            raise ValueError("Requisição não encontrada")
        clarifications = session.scalars(
            select(Clarification).where(
                Clarification.purchase_request_id == request.id,
                Clarification.status == "WAITING_USER",
            )
        ).all()
        for clarification in clarifications:
            if clarification.id not in answers:
                continue
            answer = str(answers[clarification.id]).strip()
            clarification.answer = answer
            clarification.status = "ANSWERED"
            clarification.answered_at = utcnow()
            item = next(item for item in request.items if item.id == clarification.item_id)
            key = clarification.field_path.split(".")[-1]
            specs = dict(item.specifications or {})
            specs[key] = {"value": answer, "source": "USER_CONFIRMED", "confidence": 1}
            item.specifications = specs
            item.missing_fields = [field for field in item.missing_fields if field != key]
            if not item.missing_fields:
                item.status = ItemStatus.READY
                values = [str(value.get("value")) for value in specs.values() if value.get("value") is not None]
                item.normalized_description = ", ".join([item.category_label or item.raw_description, *values])
        still_pending = any(item.status != ItemStatus.READY for item in request.items)
        request.status = RequestStatus.CLARIFYING if still_pending else RequestStatus.READY
        _event(session, request.id, "CLARIFICATIONS_APPLIED", "Respostas incorporadas", f"{len(answers)} respostas validadas e incorporadas aos itens.")
        if still_pending:
            raise ValueError("Ainda existem esclarecimentos sem resposta")
    return {"pending_clarifications": [], "status": "READY"}


def select_suppliers_node(state: ProcurementState) -> dict:
    with SessionLocal.begin() as session:
        request = session.get(PurchaseRequest, state["request_id"])
        if request is None:
            raise ValueError("Requisição não encontrada")
        request.status = RequestStatus.SELECTING_SUPPLIERS
        site = session.get(ConstructionSite, request.construction_site_id)
        city = str((site.delivery_address if site else {}).get("city", ""))
        categories = {item.canonical_category for item in request.items if item.canonical_category}
        suppliers = list(session.scalars(select(Supplier)).all())
        results = select_suppliers(suppliers, categories, city)
        session.query(SupplierSelection).filter(SupplierSelection.purchase_request_id == request.id).delete()
        selected_ids = []
        rank = 0
        for result in sorted(results, key=lambda value: value.score or 0, reverse=True):
            if result.selected:
                rank += 1
                selected_ids.append(result.supplier.id)
            session.add(SupplierSelection(
                purchase_request_id=request.id,
                supplier_id=result.supplier.id,
                selected=result.selected,
                rank=rank if result.selected else None,
                score=result.score,
                risk_level=result.risk_level,
                factors=result.factors,
                reasons=result.reasons,
                exclusion_reason=result.exclusion_reason,
            ))
        request.status = RequestStatus.SUPPLIERS_SELECTED
        _event(session, request.id, "SUPPLIERS_SELECTED", "Shortlist concluída", f"{len(selected_ids)} fornecedores selecionados por categoria, região, risco e histórico.", {"supplierIds": selected_ids})
        return {"selected_supplier_ids": selected_ids, "status": request.status.value}


@lru_cache
def build_procurement_graph():
    builder = StateGraph(ProcurementState)
    builder.add_node("normalize", normalize_node)
    builder.add_node("await_clarification", clarification_node)
    builder.add_node("apply_clarifications", apply_clarifications_node)
    builder.add_node("select_suppliers", select_suppliers_node)
    builder.add_edge(START, "normalize")
    builder.add_conditional_edges("normalize", route_after_normalize)
    builder.add_edge("await_clarification", "apply_clarifications")
    builder.add_edge("apply_clarifications", "select_suppliers")
    builder.add_edge("select_suppliers", END)
    return builder.compile(checkpointer=InMemorySaver())


def start_procurement(request_id: str) -> dict:
    config = {"configurable": {"thread_id": request_id}}
    return build_procurement_graph().invoke({"request_id": request_id}, config=config)


def resume_procurement(request_id: str, answers: dict[str, str]) -> dict:
    config = {"configurable": {"thread_id": request_id}}
    return build_procurement_graph().invoke(Command(resume=answers), config=config)
