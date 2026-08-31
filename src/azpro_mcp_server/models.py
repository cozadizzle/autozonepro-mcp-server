"""Pydantic models for AutoZone Pro MCP tools."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ToolEnvelope(BaseModel):
    ok: bool = True
    error_code: Literal[
        "ok",
        "not_logged_in",
        "no_hits",
        "timeout",
        "invalid_args",
        "nav_failed",
        "vehicle_mismatch",
        "setup_required",
        "cookie_import_failed",
    ] = "ok"
    message: Optional[str] = None
    elapsed_ms: int = 0
    data: Any = None


class VehicleSummary(BaseModel):
    year: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    engine: Optional[str] = None
    submodel: Optional[str] = None
    vin: Optional[str] = None
    nickname: Optional[str] = None
    atg_vehicle_id: Optional[str] = None
    is_current: bool = False
    make_id: Optional[str] = None
    model_id: Optional[str] = None
    engine_base_id: Optional[str] = None
    submodel_id: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class PartHit(BaseModel):
    item_id: Optional[str] = None
    part_number: Optional[str] = None
    brand: Optional[str] = None
    description: Optional[str] = None
    part_group: Optional[str] = None
    part_group_id: Optional[str] = None
    line_code: Optional[str] = None
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    vehicle_fitment: Optional[str] = None
    score: Optional[float] = None
    # Commercial pricing / availability (from /ecomm/b2b/v1/catalog/skus)
    cost: Optional[float] = None
    list_price: Optional[float] = None
    core: Optional[float] = None
    store_qty: Optional[int] = None
    availability_level: Optional[str] = None
    attributes: Dict[str, str] = Field(default_factory=dict)


class PartSearchResult(BaseModel):
    query: str = ""
    response_type: Optional[str] = None
    total: int = 0
    part_group_id: Optional[str] = None
    part_group_name: Optional[str] = None
    redirect_url: Optional[str] = None
    parts: List[PartHit] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    vehicle_name: Optional[str] = None
    store_number: Optional[str] = None
    cheapest: Optional[PartHit] = None


class CreditSnapshot(BaseModel):
    """Read-only AutoZone Pro commercial credit (balance / past-due / available)."""

    balance: Optional[float] = None
    overdue: Optional[float] = None
    past_due: Optional[float] = None
    available_credit: Optional[float] = None
    credit_limit: Optional[float] = None
    amt_due_current: Optional[float] = None
    last_payment_amount: Optional[float] = None
    last_payment_date: Optional[str] = None
    credit_status: Optional[str] = None
    account_type: Optional[str] = None


class InvoiceHit(BaseModel):
    """One invoice, return, payment receipt, or similar account document."""

    id: Optional[str] = None
    invoice_id: Optional[str] = None
    date: Optional[str] = None
    amount: Optional[float] = None
    type: Optional[str] = None
    status: Optional[str] = None
    store_number: Optional[str] = None
    po: Optional[str] = None
    part_number: Optional[str] = None
    vehicle: Optional[str] = None
