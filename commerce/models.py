"""Pydantic contracts for the Commerce Service API.

These are the wire types the pricing platform codes against. Keeping them here
(rather than importing anything from the platform) is what keeps the service
boundary real (PRD 4.1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Product(BaseModel):
    sku: str
    name: str
    category: str
    subcategory: str
    brand: str
    family_id: str
    size_value: float
    size_unit: str
    unit_cost: float
    map_price: float | None = None
    list_price: float
    current_price: float
    launched_on: str


class SalesRecord(BaseModel):
    sku: str
    sale_date: str
    units: int
    unit_price: float
    revenue: float
    on_promo: bool


class InventoryRecord(BaseModel):
    sku: str
    on_hand: int
    on_order: int
    weekly_velocity: float
    cover_days: float = Field(
        description="On-hand divided by daily velocity. Infinity is clamped to 999."
    )
    updated_at: str


class PriceRecord(BaseModel):
    sku: str
    current_price: float
    unit_cost: float
    map_price: float | None
    list_price: float


class PriceChange(BaseModel):
    sku: str
    old_price: float
    new_price: float
    changed_at: str
    batch_key: str | None
    source: str
    reason: str | None


class PriceUpdateItem(BaseModel):
    """One requested price change within a batch."""

    sku: str
    new_price: float = Field(gt=0, description="Must be positive.")
    reason: str | None = None


class PriceBatchRequest(BaseModel):
    """A batch price push from the pricing platform.

    `batch_key` drives idempotency (FR-052). Re-sending a batch with a key this
    service has already processed replays the original result and applies
    nothing.
    """

    batch_key: str = Field(min_length=8, max_length=128)
    source: str = "pricing-platform"
    items: list[PriceUpdateItem] = Field(min_length=1, max_length=5000)


class PriceItemResult(BaseModel):
    sku: str
    status: Literal["applied", "rejected", "unchanged"]
    old_price: float | None = None
    new_price: float | None = None
    rejection_code: str | None = None
    rejection_reason: str | None = None


class PriceBatchResult(BaseModel):
    """Partial success is a first-class outcome, not an error (FR-053)."""

    batch_key: str
    idempotent_replay: bool = False
    item_count: int
    applied_count: int
    rejected_count: int
    unchanged_count: int
    results: list[PriceItemResult]


class GroundTruthRecord(BaseModel):
    """Evaluation-only. See the warning in db.py."""

    sku: str
    true_elasticity: float
    base_demand: float
    ref_price: float


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    seeded: bool
    product_count: int
