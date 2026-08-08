import hmac
import logging
import uuid
from collections import deque
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
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
    SupplierDiscovery,
    SupplierSelection,
    User,
    utcnow,
)
from app.schemas import ClarificationAnswers, ConstructionSiteCreate, PurchaseRequestCreate, SupplierCreate
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


def _dedupe_quotes(events) -> list[dict]:
    """Uma linha por fornecedor: a cotação mais recente (events já vêm desc por data)."""
    seen: dict[str, dict] = {}
    for event in events:
        if event.event_type != "SUPPLIER_QUOTE":
            continue
        name = event.payload.get("supplierName") or "—"
        if name in seen:
            continue  # mantém a mais recente
        seen[name] = {
            "supplierName": name,
            "grandTotal": event.payload.get("grandTotal"),
            "freight": event.payload.get("freight"),
            "deliveryDays": event.payload.get("deliveryDays"),
            "paymentTerms": event.payload.get("paymentTerms"),
            "items": event.payload.get("items", []),
            "receivedAt": event.created_at.isoformat(),
        }
    return list(seen.values())


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
    discoveries = session.scalars(
        select(SupplierDiscovery)
        .where(SupplierDiscovery.purchase_request_id == request.id)
        .order_by(SupplierDiscovery.confidence.desc(), SupplierDiscovery.created_at.desc())
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
        "quotes": _dedupe_quotes(events),
        "discoveredSuppliers": [{
            "id": discovery.id,
            "name": discovery.supplier_name,
            "website": discovery.website,
            "category": discovery.category,
            "city": discovery.city,
            "confidence": float(discovery.confidence) if discovery.confidence is not None else None,
            "rationale": discovery.rationale,
            "status": discovery.status,
        } for discovery in discoveries],
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
        # DM de texto → tenta casar com fornecedor e registrar na cotação (em background)
        if payload.text and payload.from_phone and not payload.from_me and not payload.is_group:
            background.add_task(_register_supplier_reply, payload.from_phone, payload.text, None)
    return {"ok": True}


# ---------- Operação do WhatsApp (proxy autenticado para o wa-service) ----------

def _require_wa(token: str) -> None:
    if not settings.wa_configured:
        raise HTTPException(503, "wa-service não configurado (WA_SERVICE_URL/WA_SHARED_TOKEN)")
    if not hmac.compare_digest(token, settings.wa_shared_token.get_secret_value()):
        raise HTTPException(401, "token inválido")


def _wa_request(method: str, path: str, json_body: dict | None = None) -> httpx.Response:
    try:
        return httpx.request(
            method,
            f"{settings.wa_service_url.rstrip('/')}{path}",
            json=json_body,
            headers={"x-wa-token": settings.wa_shared_token.get_secret_value()},
            timeout=60.0,  # qr pode esperar a conexão do Baileys (+ cold start no free)
        )
    except httpx.HTTPError as err:
        raise HTTPException(502, f"wa-service inacessível: {err}") from err


@app.get("/api/wa/status")
def wa_status(token: str = "", x_wa_token: str = Header(default="")) -> dict:
    _require_wa(token or x_wa_token)
    return _wa_request("GET", "/status").json()


@app.get("/api/wa/qr.png")
def wa_qr_png(token: str = "", x_wa_token: str = Header(default="")) -> Response:
    """Abra no browser: /api/wa/qr.png?token=<WA_SHARED_TOKEN> e escaneie no WhatsApp."""
    _require_wa(token or x_wa_token)
    upstream = _wa_request("GET", "/pairing/qr.png")
    if upstream.status_code != 200:
        raise HTTPException(upstream.status_code, upstream.text[:200])
    return Response(content=upstream.content, media_type="image/png", headers={"cache-control": "no-store"})


class WaPairingCode(BaseModel):
    phone: str = Field(min_length=10)


@app.post("/api/wa/pairing/code")
def wa_pairing_code(body: WaPairingCode, token: str = "", x_wa_token: str = Header(default="")) -> dict:
    _require_wa(token or x_wa_token)
    upstream = _wa_request("POST", "/pairing/code", {"phone": body.phone})
    if upstream.status_code != 200:
        raise HTTPException(upstream.status_code, upstream.text[:200])
    return upstream.json()


@app.post("/api/wa/logout")
def wa_logout(token: str = "", x_wa_token: str = Header(default="")) -> dict:
    _require_wa(token or x_wa_token)
    upstream = _wa_request("POST", "/session/logout")
    if upstream.status_code != 200:
        raise HTTPException(upstream.status_code, upstream.text[:200])
    return upstream.json()


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


@app.post("/api/suppliers", status_code=201)
def create_supplier(payload: SupplierCreate, session: Session = Depends(get_db)) -> dict:
    supplier = Supplier(
        trade_name=payload.name,
        tax_id=payload.tax_id or None,
        phone=payload.phone,
        service_cities=[payload.city],
        categories=payload.categories,
        performance={"completedOrders": 0, "quoteResponseRate": 0, "onTimeDeliveryRate": 0},
    )
    session.add(supplier)
    session.commit()
    session.refresh(supplier)
    return {"id": supplier.id, "name": supplier.trade_name, "phone": supplier.phone, "cities": supplier.service_cities, "categories": supplier.categories, "performance": supplier.performance, "status": supplier.status}


@app.post("/api/construction-sites", status_code=201)
def create_construction_site(payload: ConstructionSiteCreate, session: Session = Depends(get_db)) -> dict:
    if session.get(Company, payload.company_id) is None:
        raise HTTPException(422, "Empresa inválida")
    site = ConstructionSite(
        company_id=payload.company_id,
        code=payload.code,
        name=payload.name,
        cost_center=payload.cost_center,
        delivery_address={"street": payload.street, "number": payload.number, "city": payload.city, "state": payload.state.upper(), "postalCode": payload.postal_code},
        receiving_rules={},
    )
    session.add(site)
    session.commit()
    session.refresh(site)
    return {"id": site.id, "companyId": site.company_id, "name": site.name, "city": payload.city}


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


@app.post("/api/demo/seed-whatsapp")
def seed_whatsapp_suppliers(session: Session = Depends(get_db)) -> dict:
    """Cria/atualiza 2 fornecedores com números reais de teste para a demo por WhatsApp."""
    seed_demo(session)  # garante empresa/obra/usuário
    # Números reais que atuarão como empresas fornecedoras na demo
    test_suppliers = [
        ("Triângulo Materiais de Construção", "55555501000155", "+5534998418420", ["Uberlândia", "Campinas", "São Paulo"]),
        ("Paulista Suprimentos para Obra", "55555502000155", "+5511998567712", ["São Paulo", "Campinas"]),
    ]
    created, updated = [], []
    for name, tax_id, phone, cities in test_suppliers:
        supplier = session.scalar(select(Supplier).where(Supplier.tax_id == tax_id))
        if supplier:
            supplier.phone = phone
            updated.append(name)
        else:
            session.add(Supplier(
                trade_name=name, tax_id=tax_id, phone=phone,
                categories=["CEMENT", "MORTAR", "MASONRY_BLOCK"], service_cities=cities,
                performance={"completedOrders": 10, "onTimeDeliveryRate": .9, "quoteResponseRate": .9,
                             "orderAccuracyRate": .95, "responseSpeedScore": .9, "priceCompetitiveness": .85,
                             "logisticsScore": .9},
            ))
            created.append(name)
    session.commit()
    return {"ok": True, "created": created, "updated": updated}


@app.post("/api/demo/seed-quote", status_code=201)
def seed_test_quote(session: Session = Depends(get_db)) -> dict:
    """Cria uma cotação de teste já com fornecedores de WhatsApp preferidos."""
    seed_whatsapp_suppliers(session)
    company = session.scalar(select(Company).where(Company.tax_id == "12345678000190"))
    site = session.scalar(select(ConstructionSite).where(ConstructionSite.company_id == company.id))
    user = session.scalar(select(User).where(User.company_id == company.id))
    wa_suppliers = session.scalars(
        select(Supplier).where(Supplier.tax_id.in_(["55555501000155", "55555502000155"]))
    ).all()

    from datetime import timedelta

    request = PurchaseRequest(
        code=f"SC-{str(uuid.uuid4())[:6].upper()}",
        company_id=company.id, construction_site_id=site.id, requested_by=user.id,
        title="Cotação teste — concretagem Bloco B", priority="NORMAL",
        required_at=utcnow() + timedelta(days=7),
        constraints={"responseWindow": "24 horas", "freight": "CIF — entregue na obra"},
    )
    request.items = [
        PurchaseRequestItem(client_item_id="item_1", raw_description="Cimento CP-II F 32, saco 50 kg",
                            canonical_category="CEMENT", category_label="CEMENT", quantity=Decimal("50"), unit="BAG"),
        PurchaseRequestItem(client_item_id="item_2", raw_description="Bloco cerâmico 14x19x39 cm",
                            canonical_category="MASONRY_BLOCK", category_label="MASONRY_BLOCK", quantity=Decimal("800"), unit="UNIT"),
    ]
    session.add(request); session.flush()
    for rank, supplier in enumerate(wa_suppliers, start=1):
        session.add(SupplierSelection(purchase_request_id=request.id, supplier_id=supplier.id, selected=True, rank=rank))
    session.commit()
    return {"ok": True, "requestId": request.id, "code": request.code, "suppliers": [s.trade_name for s in wa_suppliers]}


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
def create_purchase_request(payload: PurchaseRequestCreate, background: BackgroundTasks, session: Session = Depends(get_db)) -> dict:
    if session.get(Company, payload.company_id) is None or session.get(ConstructionSite, payload.construction_site_id) is None or session.get(User, payload.requested_by) is None:
        raise HTTPException(422, "Empresa, obra ou solicitante inválido")
    code = f"SC-{str(uuid.uuid4())[:6].upper()}"
    request = PurchaseRequest(code=code, company_id=payload.company_id, construction_site_id=payload.construction_site_id, requested_by=payload.requested_by, title=payload.request_title, priority=payload.priority, required_at=payload.required_at, maximum_budget=payload.maximum_budget, constraints=payload.commercial_constraints, notes=payload.notes)
    request.items = [PurchaseRequestItem(client_item_id=item.client_item_id, raw_description=item.raw_description, canonical_category=item.category, category_label=item.category, quantity=item.quantity, unit=item.unit) for item in payload.items]
    session.add(request); session.flush()
    for rank, supplier_id in enumerate(payload.preferred_supplier_ids, start=1):
        if session.get(Supplier, supplier_id):
            session.add(SupplierSelection(purchase_request_id=request.id, supplier_id=supplier_id, selected=True, rank=rank, score=1, risk_level="BUYER_SELECTED", factors={"source": "BUYER_PREFERENCE"}, reasons=["Selecionado diretamente pelo comprador."]))
    session.add(AgentEvent(purchase_request_id=request.id, event_type="REQUEST_CREATED", title="Solicitação criada", detail=f"{len(request.items)} itens recebidos para análise.", payload={"code": code}))
    session.commit(); session.refresh(request)
    response = request_to_dict(request, session)
    from app.services.supplier_discovery import run_supplier_discovery
    background.add_task(run_supplier_discovery, request.id)
    return response


@app.post("/api/purchase-requests/{request_id}/discover-suppliers", status_code=202)
def rediscover_suppliers(request_id: str, background: BackgroundTasks, session: Session = Depends(get_db)) -> dict:
    if session.get(PurchaseRequest, request_id) is None:
        raise HTTPException(404, "Cotação não encontrada")
    from app.services.supplier_discovery import run_supplier_discovery
    background.add_task(run_supplier_discovery, request_id)
    return {"ok": True, "status": "DISCOVERY_QUEUED"}


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


@app.post("/api/purchase-requests/{request_id}/send-whatsapp")
def send_request_whatsapp(
    request_id: str,
    session: Session = Depends(get_db),
    token: str = "",
    x_wa_token: str = Header(default=""),
) -> dict:
    """Envia a cotação por WhatsApp para os fornecedores selecionados (ação do operador)."""
    _require_wa(token or x_wa_token)
    request = session.get(PurchaseRequest, request_id)
    if request is None:
        raise HTTPException(404, "Cotação não encontrada")
    rows = session.execute(
        select(SupplierSelection, Supplier)
        .join(Supplier, Supplier.id == SupplierSelection.supplier_id)
        .where(SupplierSelection.purchase_request_id == request_id, SupplierSelection.selected.is_(True))
    ).all()
    if not rows:
        raise HTTPException(422, "Nenhum fornecedor selecionado — processe a cotação antes")

    from app.wa import send_text

    site = session.get(ConstructionSite, request.construction_site_id)
    lines = [f"🏗️ *Cotação {request.code}* — {request.title}"]
    if site:
        lines.append(f"Obra: {site.name} ({site.delivery_address.get('city', '')})")
    lines.append("")
    for i, item in enumerate(request.items, 1):
        desc = item.normalized_description or item.raw_description
        lines.append(f"{i}. {item.quantity:g} {item.unit} — {desc}")
    lines.append("")
    lines.append(f"Entrega até: {request.required_at.strftime('%d/%m/%Y')}")
    lines.append("Responda esta mensagem com preço por item, frete e prazo de entrega.")
    message = "\n".join(lines)

    sent, failed = [], []
    for _, supplier in rows:
        if not supplier.phone:
            failed.append(supplier.trade_name)
            continue
        (sent if send_text(supplier.phone, message) else failed).append(supplier.trade_name)
    session.add(AgentEvent(
        purchase_request_id=request_id,
        event_type="WHATSAPP_SENT" if sent else "WHATSAPP_FAILED",
        title="Cotação enviada por WhatsApp" if sent else "Falha no envio por WhatsApp",
        detail=f"Enviada para: {', '.join(sent) or 'ninguém'}" + (f" · Falhou: {', '.join(failed)}" if failed else ""),
        payload={"sent": sent, "failed": failed},
    ))
    session.commit()
    return {"ok": bool(sent), "sent": sent, "failed": failed}


def _register_supplier_reply(phone: str, text: str, push_name: str | None) -> None:
    from app.database import SessionLocal
    from app.wa import phone_variants

    variants = phone_variants(phone)
    if not variants:
        return
    with SessionLocal.begin() as session:
        supplier = next(
            (s for s in session.scalars(select(Supplier)).all() if phone_variants(s.phone or "") & variants),
            None,
        )
        if supplier is None:
            logger.info("mensagem de número não cadastrado como fornecedor — ignorada")
            return
        request_id = session.execute(
            select(SupplierSelection.purchase_request_id)
            .join(PurchaseRequest, PurchaseRequest.id == SupplierSelection.purchase_request_id)
            .where(SupplierSelection.supplier_id == supplier.id)
            .order_by(PurchaseRequest.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        session.add(AgentEvent(
            purchase_request_id=request_id,
            event_type="SUPPLIER_REPLY",
            title=f"Resposta de {supplier.trade_name}" + (f" ({push_name})" if push_name else ""),
            detail=text[:800],
            payload={"supplierId": supplier.id, "phone": phone},
        ))
        supplier_name, item_descs = supplier.trade_name, []
        if request_id:
            req = session.get(PurchaseRequest, request_id)
            item_descs = [i.normalized_description or i.raw_description for i in req.items] if req else []
    logger.info("resposta de fornecedor registrada (%s)", supplier.trade_name)

    # Extrai preços estruturados da resposta (a "mágica" da demo) — fora da transação anterior
    if request_id:
        from app.services.quote_extraction import extract_quote

        quote = extract_quote(text, item_descs)
        # só vira cotação estruturada se tiver preço/itens — conversa solta fica só como SUPPLIER_REPLY
        has_price = quote.grand_total is not None or any(i.total_price or i.unit_price for i in quote.items)
        if has_price:
            with SessionLocal.begin() as session:
                session.add(AgentEvent(
                    purchase_request_id=request_id,
                    event_type="SUPPLIER_QUOTE",
                    title=f"Cotação de {supplier_name}",
                    detail=(f"Total R$ {quote.grand_total:.2f}" if quote.grand_total else "Preço por item recebido")
                    + (f" · entrega em {quote.delivery_days} dias" if quote.delivery_days else ""),
                    payload={
                        "supplierName": supplier_name,
                        "grandTotal": quote.grand_total,
                        "freight": quote.freight,
                        "deliveryDays": quote.delivery_days,
                        "paymentTerms": quote.payment_terms,
                        "items": [i.model_dump() for i in quote.items],
                    },
                ))


class CloseQuote(BaseModel):
    supplier_name: str = Field(alias="supplierName")
    markup_percent: float = Field(default=0, ge=0, le=100, alias="markupPercent")

    model_config = {"populate_by_name": True}


@app.post("/api/purchase-requests/{request_id}/close")
def close_quote(
    request_id: str,
    body: CloseQuote,
    session: Session = Depends(get_db),
    token: str = "",
    x_wa_token: str = Header(default=""),
) -> dict:
    """Fecha o pedido no fornecedor escolhido (markup aplicado) e avisa todos por WhatsApp."""
    request = session.get(PurchaseRequest, request_id)
    if request is None:
        raise HTTPException(404, "Cotação não encontrada")

    existing_events = session.scalars(
        select(AgentEvent).where(AgentEvent.purchase_request_id == request_id)
    ).all()
    # idempotência: já fechado → não reenvia nada (evita spam em retry/duplo-clique)
    closed = next((e for e in existing_events if e.event_type == "ORDER_CLOSED"), None)
    if closed:
        p = closed.payload
        return {"ok": True, "alreadyClosed": True, "supplierName": p.get("supplierName"),
                "cost": p.get("cost"), "finalPrice": p.get("finalPrice"), "notified": []}

    quote_event = next(
        (e for e in existing_events
         if e.event_type == "SUPPLIER_QUOTE" and e.payload.get("supplierName") == body.supplier_name),
        None,
    )
    base = (quote_event.payload.get("grandTotal") if quote_event else None) or 0
    final_price = round(base * (1 + body.markup_percent / 100), 2)

    # Avisa os fornecedores por WhatsApp ANTES de gravar o fechamento — vencedor confirma, demais recebem retorno.
    notified = []
    wa_ok = settings.wa_configured and bool(token or x_wa_token) and hmac.compare_digest(
        token or x_wa_token, settings.wa_shared_token.get_secret_value()
    )
    if wa_ok:
        from app.wa import send_text

        suppliers = session.execute(
            select(Supplier)
            .join(SupplierSelection, SupplierSelection.supplier_id == Supplier.id)
            .where(SupplierSelection.purchase_request_id == request_id, SupplierSelection.selected.is_(True))
        ).scalars().all()
        for supplier in suppliers:
            if not supplier.phone:
                continue
            if supplier.trade_name == body.supplier_name:
                msg = (f"✅ Fechamos o pedido da cotação {request.code} com vocês! "
                       f"Nossa equipe entra em contato para confirmar entrega e pagamento. Obrigado!")
            else:
                msg = (f"Olá! Sobre a cotação {request.code}: desta vez seguimos com outro fornecedor. "
                       f"Agradecemos a proposta e contamos com vocês nas próximas. 🙏")
            try:
                if send_text(supplier.phone, msg):
                    notified.append(supplier.trade_name)
            except Exception:
                logger.exception("falha ao avisar fornecedor %s", supplier.trade_name)

    request.status = RequestStatus.SUPPLIERS_SELECTED
    session.add(AgentEvent(
        purchase_request_id=request_id, event_type="ORDER_CLOSED",
        title=f"Pedido fechado com {body.supplier_name}",
        detail=f"Custo R$ {base:.2f} + markup {body.markup_percent:g}% = R$ {final_price:.2f} para o cliente.",
        payload={"supplierName": body.supplier_name, "cost": base, "markupPercent": body.markup_percent, "finalPrice": final_price},
    ))
    if notified:
        session.add(AgentEvent(
            purchase_request_id=request_id, event_type="SUPPLIERS_NOTIFIED",
            title="Fornecedores avisados por WhatsApp",
            detail=f"Vencedor: {body.supplier_name} · Avisados: {', '.join(notified)}",
            payload={"winner": body.supplier_name, "notified": notified},
        ))
    session.commit()
    return {"ok": True, "supplierName": body.supplier_name, "cost": base, "finalPrice": final_price, "notified": notified}


@app.post("/api/demo/reset")
def reset_demo(session: Session = Depends(get_db)) -> dict:
    """Limpa cotações, respostas e descobertas — mantém empresa, obra e fornecedores."""
    session.query(AgentEvent).delete()
    session.query(SupplierDiscovery).delete()
    session.query(SupplierSelection).delete()
    session.query(Clarification).delete()
    session.query(PurchaseRequestItem).delete()
    session.query(PurchaseRequest).delete()
    session.commit()
    return {"ok": True, "message": "Cotações e dados operacionais limpos."}


@app.post("/api/demo/reset-all")
def reset_all(session: Session = Depends(get_db)) -> dict:
    """Limpa TUDO, inclusive fornecedores/empresas/obras. Recomeça do zero."""
    for model in (AgentEvent, SupplierDiscovery, SupplierSelection, Clarification,
                  PurchaseRequestItem, PurchaseRequest, Supplier, ConstructionSite, User, Company):
        session.query(model).delete()
    session.commit()
    return {"ok": True, "message": "Todos os dados do sistema foram apagados."}


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
