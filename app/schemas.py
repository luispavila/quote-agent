from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ItemCreate(BaseModel):
    client_item_id: str = Field(alias="clientItemId", min_length=1)
    raw_description: str = Field(alias="rawDescription", min_length=3, max_length=1000)
    quantity: Decimal = Field(gt=0)
    unit: str = Field(min_length=1, max_length=30)

    model_config = {"populate_by_name": True}


class PurchaseRequestCreate(BaseModel):
    company_id: str = Field(alias="companyId")
    construction_site_id: str = Field(alias="constructionSiteId")
    requested_by: str = Field(alias="requestedBy")
    request_title: str = Field(alias="requestTitle", min_length=3, max_length=220)
    required_at: date = Field(alias="requiredAt")
    priority: Literal["LOW", "NORMAL", "HIGH", "URGENT"] = "NORMAL"
    maximum_budget: Decimal | None = Field(default=None, alias="maximumBudget", ge=0)
    commercial_constraints: dict = Field(default_factory=dict, alias="commercialConstraints")
    notes: str | None = None
    items: list[ItemCreate] = Field(min_length=1, max_length=100)

    model_config = {"populate_by_name": True}


class ClarificationAnswer(BaseModel):
    clarification_id: str = Field(alias="clarificationId")
    value: str = Field(min_length=1, max_length=500)

    model_config = {"populate_by_name": True}


class ClarificationAnswers(BaseModel):
    answers: list[ClarificationAnswer] = Field(min_length=1)


class AttributeExtraction(BaseModel):
    key: str
    label: str
    value: str | int | float | bool | None = None
    unit: str | None = None
    source: Literal["USER_EXPLICIT", "INFERRED", "COMPANY_DEFAULT"]
    evidence: str | None = None
    confidence: float = Field(ge=0, le=1)


class MissingInformation(BaseModel):
    key: str
    label: str
    reason: str
    suggested_question: str
    suggested_options: list[str] = Field(default_factory=list)


class NormalizationExtraction(BaseModel):
    canonical_category: str
    category_label: str
    category_confidence: float = Field(ge=0, le=1)
    attributes: list[AttributeExtraction]
    missing_information: list[MissingInformation]
    normalized_description: str | None = None


class DashboardSummary(BaseModel):
    open_requests: int
    awaiting_clarification: int
    ready_for_sourcing: int
    active_suppliers: int


class SeedResponse(BaseModel):
    company_id: str
    user_id: str
    construction_site_id: str
    suppliers_created: int


class EventRead(BaseModel):
    id: str
    event_type: str
    title: str
    detail: str | None
    created_at: datetime
