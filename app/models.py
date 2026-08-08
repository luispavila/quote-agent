import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    NORMALIZING = "NORMALIZING"
    CLARIFYING = "CLARIFYING"
    READY = "READY"
    SELECTING_SUPPLIERS = "SELECTING_SUPPLIERS"
    SUPPLIERS_SELECTED = "SUPPLIERS_SELECTED"
    CANCELLED = "CANCELLED"


class ItemStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    REJECTED = "REJECTED"


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    legal_name: Mapped[str] = mapped_column(String(200))
    trade_name: Mapped[str] = mapped_column(String(160))
    tax_id: Mapped[str] = mapped_column(String(20), unique=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(30))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    approval_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class ConstructionSite(Base):
    __tablename__ = "construction_sites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(180))
    cost_center: Mapped[str | None] = mapped_column(String(80))
    delivery_address: Mapped[dict] = mapped_column(JSON)
    receiving_rules: Mapped[dict] = mapped_column(JSON, default=dict)


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trade_name: Mapped[str] = mapped_column(String(180))
    tax_id: Mapped[str | None] = mapped_column(String(20), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="ACTIVE")
    verification_level: Mapped[str] = mapped_column(String(30), default="VERIFIED")
    phone: Mapped[str | None] = mapped_column(String(30))
    categories: Mapped[list] = mapped_column(JSON, default=list)
    service_cities: Mapped[list] = mapped_column(JSON, default=list)
    performance: Mapped[dict] = mapped_column(JSON, default=dict)
    commercial_terms: Mapped[dict] = mapped_column(JSON, default=dict)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code: Mapped[str] = mapped_column(String(30), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    construction_site_id: Mapped[str] = mapped_column(ForeignKey("construction_sites.id"))
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(220))
    status: Mapped[RequestStatus] = mapped_column(Enum(RequestStatus), default=RequestStatus.DRAFT)
    priority: Mapped[str] = mapped_column(String(20), default="NORMAL")
    required_at: Mapped[date] = mapped_column(Date)
    maximum_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    items: Mapped[list["PurchaseRequestItem"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", lazy="selectin"
    )


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"))
    client_item_id: Mapped[str] = mapped_column(String(80))
    raw_description: Mapped[str] = mapped_column(Text)
    normalized_description: Mapped[str | None] = mapped_column(Text)
    canonical_category: Mapped[str | None] = mapped_column(String(100))
    category_label: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3))
    unit: Mapped[str] = mapped_column(String(30))
    specifications: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    status: Mapped[ItemStatus] = mapped_column(Enum(ItemStatus), default=ItemStatus.PENDING)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    request: Mapped[PurchaseRequest] = relationship(back_populates="items")


class Clarification(Base):
    __tablename__ = "clarifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("purchase_request_items.id"))
    field_path: Mapped[str] = mapped_column(String(180))
    question: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="WAITING_USER")
    answer: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupplierSelection(Base):
    __tablename__ = "supplier_selections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_request_id: Mapped[str] = mapped_column(ForeignKey("purchase_requests.id"))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    rank: Mapped[int | None]
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    risk_level: Mapped[str | None] = mapped_column(String(20))
    factors: Mapped[dict] = mapped_column(JSON, default=dict)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    exclusion_reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    purchase_request_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_requests.id"))
    event_type: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
