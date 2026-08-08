import hmac
import logging
import uuid
from collections import deque
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import create_schema, get_db
from app.graph import resume_procurement, start_procurement
from app.models import (
    AgentEvent,
    Clarification,
    Company,
    ConstructionSite,
    ItemStatus,
    PurchaseRequest,
    PurchaseRequestItem,
    RequestStatus,
    Supplier,
    SupplierSelection,
    User,
)
from app.schemas import ClarificationAnswers, PurchaseRequestCreate
from pydantic import BaseModel, Field
from app.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_schema()
    yield


settings = get_settings()
app = FastAPI(title="Nexo Compras API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _money(value):
    return float(value) if value is not None else None


def request_to_dict(request: PurchaseRequest, session: Session) -> dict:
    site = session.get(ConstructionSite, request.construction_site_id)
    user = session.get(User, request.requested_by)
    selections = session.execute(
        select(SupplierSelection, Supplier)
        .join(Supplier, Supplier.id == SupplierSelection.supplier_id)
        .where(SupplierSelection.purchase_request_id == request.id)
        .order_by(SupplierSelection.selected.desc(), SupplierSelection.rank)
    ).all()
    clarifications = session.scalars(
        select(Clarification)
        .where(Clarification.purchase_request_id == request.id)
        .order_by(Clarification.created_at)
    ).all()
    events = session.scalars(
        select(AgentEvent)
        .where(AgentEvent.purchase_request_id == request.id)
        .order_by(AgentEvent.created_at.desc())
    ).all()
    return {
        "id": request.id,
        "code": request.code,
        "title": request.title,
        "status": request.status.value,
        "priority": request.priority,
        "requiredAt": request.required_at.isoformat(),
        "maximumBudget": _money(request.maximum_budget),
        "site": {"id": site.id, "name": site.name, "city": site.delivery_address.get("city")} if site else None,
        "requestedBy": user.name if user else None,
        "items": [{
            "id": item.id,
            "clientItemId": item.client_item_id,
            "rawDescription": item.raw_description,
            "normalizedDescription": item.normalized_description,
            "category": item.category_label,
            "canonicalCategory": item.canonical_category,
            "quantity": float(item.quantity),
            "unit": item.unit,
            "status": item.status.value,
            "confidence": float(item.confidence) if item.confidence is not None else None,
            "specifications": item.specifications,
            "missingFields": item.missing_fields,
        } for item in request.items],
        "clarifications": [{
            "id": clarification.id,
            "itemId": clarification.item_id,
            "field": clarification.field_path,
            "question": clarification.question,
            "reason": clarification.reason,
            "options": clarification.options,
            "status": clarification.status,
            "answer": clarification.answer,
        } for clarification in clarifications],
        "supplierSelections": [{
            "supplierId": supplier.id,
            "supplierName": supplier.trade_name,
            "selected": selection.selected,
            "rank": selection.rank,
            "score": float(selection.score) if selection.score is not None else None,
            "riskLevel": selection.risk_level,
            "factors": selection.factors,
            "reasons": selection.reasons,
            "exclusionReason": selection.exclusion_reason,
        } for selection, supplier in selections],
        "events": [{
            "id": event.id,
            "type": event.event_type,
            "title": event.title,
            "detail": event.detail,
            "createdAt": event.created_at.isoformat(),
        } for event in events],
        "createdAt": request.created_at.isoformat(),
    }


@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "env": settings.app_env,
        "llm_provider": settings.llm_provider,
        "model": settings.active_model,
        "llm_configured": settings.featherless_api_key is not None,
        "llm_base_url": settings.featherless_base_url,
        "langfuse": settings.langfuse_enabled,
        "whatsapp": settings.wa_configured,
    }


class WaWebhook(BaseModel):
    event: str
    message_id: str | None = Field(default=None, alias="messageId")
    from_phone: str | None = Field(default=None, alias="from")
    is_group: bool = Field(default=False, alias="isGroup")
    from_me: bool = Field(default=False, alias="fromMe")
    text: str | None = None
    status: str | None = None

    model_config = {"populate_by_name": True}


_seen_message_ids: deque[str] = deque(maxlen=500)


@app.post("/webhooks/wa")
def wa_webhook(payload: WaWebhook, background: BackgroundTasks, x_wa_token: str = Header(default="")) -> dict:
    if not settings.wa_shared_token or not hmac.compare_digest(x_wa_token, settings.wa_shared_token.get_secret_value()):
        raise HTTPException(401, "token inválido")
    if payload.event == "connection.update":
        logger.info("wa-service: conexão %s", payload.status)
        return {"ok": True}
    if payload.event == "message.received" and payload.message_id:
        if payload.message_id in _seen_message_ids:
            return {"ok": True, "dedup": True}
        _seen_message_ids.append(payload.message_id)
    # O canal está preservado; o disparo de cotações será ligado no próximo marco.
    return {"ok": True}


@app.get("/api/bootstrap")
def bootstrap(session: Session = Depends(get_db)) -> dict:
    companies = session.scalars(select(Company)).all()
    sites = session.scalars(select(ConstructionSite)).all()
    users = session.scalars(select(User)).all()
    return {
        "companies": [{"id": company.id, "name": company.trade_name} for company in companies],
        "sites": [{"id": site.id, "companyId": site.company_id, "name": site.name, "city": site.delivery_address.get("city")} for site in sites],
        "users": [{"id": user.id, "companyId": user.company_id, "name": user.name} for user in users],
    }


@app.get("/api/suppliers")
def list_suppliers(session: Session = Depends(get_db)) -> list[dict]:
    suppliers = session.scalars(select(Supplier).order_by(Supplier.trade_name)).all()
    return [{
        "id": supplier.id,
        "name": supplier.trade_name,
        "phone": supplier.phone,
        "cities": supplier.service_cities,
        "categories": supplier.categories,
        "performance": supplier.performance,
        "status": supplier.status,
    } for supplier in suppliers]


@app.post("/api/demo/seed")
def seed_demo(session: Session = Depends(get_db)) -> dict:
    company = session.scalar(select(Company).where(Company.tax_id == "12345678000190"))
    if company:
        user = session.scalar(select(User).where(User.company_id == company.id))
        site = session.scalar(select(ConstructionSite).where(ConstructionSite.company_id == company.id))
        suppliers = session.scalar(select(func.count()).select_from(Supplier)) or 0
        return {"companyId": company.id, "userId": user.id, "constructionSiteId": site.id, "suppliersCreated": suppliers}
    company = Company(legal_name="Construtora Horizonte Ltda.", trade_name="Construtora Horizonte", tax_id="12345678000190", settings={"currency": "BRL", "allowSplitOrders": True})
    session.add(company); session.flush()
    user = User(company_id=company.id, name="Marina Costa", email="marina@horizonte.com.br", phone="+5519999999999", roles=["BUYER", "APPROVER"], approval_limit=Decimal("50000"))
    site = ConstructionSite(company_id=company.id, code="OBRA-ALAMEDA", name="Residencial Alameda", cost_center="CC-2026-014", delivery_address={"street": "Av. das Amoreiras", "number": "2450", "city": "Campinas", "state": "SP", "postalCode": "13031100"}, receiving_rules={"startTime": "08:00", "endTime": "16:00"})
    session.add_all([user, site])
    suppliers = [
        Supplier(trade_name="Depósito Campinas", tax_id="11111111000111", phone="+551932001001", categories=["CEMENT", "MORTAR", "MASONRY_BLOCK"], service_cities=["Campinas", "Valinhos"], performance={"completedOrders": 42, "onTimeDeliveryRate": .96, "quoteResponseRate": .92, "orderAccuracyRate": .98, "responseSpeedScore": .86, "priceCompetitiveness": .78, "logisticsScore": .95}),
        Supplier(trade_name="Construmax Materiais", tax_id="22222222000122", phone="+551932002002", categories=["CEMENT", "MORTAR"], service_cities=["Campinas"], performance={"completedOrders": 18, "onTimeDeliveryRate": .91, "quoteResponseRate": .88, "orderAccuracyRate": .95, "responseSpeedScore": .94, "priceCompetitiveness": .9, "logisticsScore": .88}),
        Supplier(trade_name="Nova Base Suprimentos", tax_id="33333333000133", phone="+551932003003", categories=["CEMENT", "MORTAR", "MASONRY_BLOCK"], service_cities=["Campinas"], performance={"completedOrders": 1, "onTimeDeliveryRate": .5, "quoteResponseRate": .5, "orderAccuracyRate": .5, "responseSpeedScore": .72, "priceCompetitiveness": .8, "logisticsScore": .82}),
        Supplier(trade_name="Interior Obras", tax_id="44444444000144", phone="+551934004004", categories=["CEMENT", "MASONRY_BLOCK"], service_cities=["Piracicaba"], performance={"completedOrders": 20}),
    ]
    session.add_all(suppliers); session.commit()
    return {"companyId": company.id, "userId": user.id, "constructionSiteId": site.id, "suppliersCreated": len(suppliers)}


@app.get("/api/dashboard")
def dashboard(session: Session = Depends(get_db)) -> dict:
    counts = {status.value: session.scalar(select(func.count()).select_from(PurchaseRequest).where(PurchaseRequest.status == status)) or 0 for status in RequestStatus}
    return {
        "openRequests": sum(value for key, value in counts.items() if key not in {"CANCELLED"}),
        "awaitingClarification": counts.get("CLARIFYING", 0),
        "readyForSourcing": counts.get("READY", 0),
        "suppliersSelected": counts.get("SUPPLIERS_SELECTED", 0),
        "activeSuppliers": session.scalar(select(func.count()).select_from(Supplier).where(Supplier.status == "ACTIVE")) or 0,
    }


@app.post("/api/purchase-requests", status_code=201)
def create_purchase_request(payload: PurchaseRequestCreate, session: Session = Depends(get_db)) -> dict:
    if session.get(Company, payload.company_id) is None or session.get(ConstructionSite, payload.construction_site_id) is None or session.get(User, payload.requested_by) is None:
        raise HTTPException(422, "Empresa, obra ou solicitante inválido")
    code = f"SC-{str(uuid.uuid4())[:6].upper()}"
    request = PurchaseRequest(code=code, company_id=payload.company_id, construction_site_id=payload.construction_site_id, requested_by=payload.requested_by, title=payload.request_title, priority=payload.priority, required_at=payload.required_at, maximum_budget=payload.maximum_budget, constraints=payload.commercial_constraints, notes=payload.notes)
    request.items = [PurchaseRequestItem(client_item_id=item.client_item_id, raw_description=item.raw_description, quantity=item.quantity, unit=item.unit) for item in payload.items]
    session.add(request); session.flush()
    session.add(AgentEvent(purchase_request_id=request.id, event_type="REQUEST_CREATED", title="Solicitação criada", detail=f"{len(request.items)} itens recebidos para análise.", payload={"code": code}))
    session.commit(); session.refresh(request)
    return request_to_dict(request, session)


@app.get("/api/purchase-requests")
def list_purchase_requests(session: Session = Depends(get_db)) -> list[dict]:
    requests = session.scalars(select(PurchaseRequest).order_by(PurchaseRequest.created_at.desc())).all()
    return [request_to_dict(request, session) for request in requests]


@app.get("/api/purchase-requests/{request_id}")
def get_purchase_request(request_id: str, session: Session = Depends(get_db)) -> dict:
    request = session.get(PurchaseRequest, request_id)
    if request is None:
        raise HTTPException(404, "Solicitação não encontrada")
    return request_to_dict(request, session)


@app.post("/api/purchase-requests/{request_id}/process")
def process_purchase_request(request_id: str, session: Session = Depends(get_db)) -> dict:
    if session.get(PurchaseRequest, request_id) is None:
        raise HTTPException(404, "Solicitação não encontrada")
    try:
        result = start_procurement(request_id)
    except Exception as exc:
        logger.exception("Falha no grafo de procurement")
        raise HTTPException(422, str(exc)) from exc
    request = session.get(PurchaseRequest, request_id); session.refresh(request)
    response = request_to_dict(request, session)
    if "__interrupt__" in result:
        response["interrupt"] = result["__interrupt__"][0].value
    return response


@app.post("/api/purchase-requests/{request_id}/clarifications")
def answer_clarifications(request_id: str, payload: ClarificationAnswers, session: Session = Depends(get_db)) -> dict:
    answers = {answer.clarification_id: answer.value for answer in payload.answers}
    try:
        resume_procurement(request_id, answers)
    except Exception as exc:
        logger.exception("Falha ao retomar grafo")
        raise HTTPException(422, str(exc)) from exc
    session.expire_all()
    request = session.get(PurchaseRequest, request_id)
    if request is None:
        raise HTTPException(404, "Solicitação não encontrada")
    return request_to_dict(request, session)


@app.get("/")
def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
