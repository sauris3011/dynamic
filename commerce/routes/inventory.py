"""Inventory endpoints (FR-051).

Cover days are computed here rather than by the caller so every consumer sees
the same definition: on-hand divided by daily velocity.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from commerce.db import session
from commerce.models import InventoryRecord

COVER_DAYS_CEILING = 999.0


def _cover_days(on_hand: int, weekly_velocity: float) -> float:
    daily = weekly_velocity / 7.0
    if daily <= 0:
        return COVER_DAYS_CEILING
    return round(min(on_hand / daily, COVER_DAYS_CEILING), 2)


router = APIRouter(prefix="/inventory", tags=["inventory"])


def _to_record(row) -> InventoryRecord:
    return InventoryRecord(
        sku=row["sku"],
        on_hand=row["on_hand"],
        on_order=row["on_order"],
        weekly_velocity=row["weekly_velocity"],
        cover_days=_cover_days(row["on_hand"], row["weekly_velocity"]),
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[InventoryRecord])
def list_inventory(
    category: str | None = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
) -> list[InventoryRecord]:
    clause = "WHERE p.category = ?" if category else ""
    params = [category] if category else []
    params.append(limit)
    with session() as conn:
        rows = conn.execute(
            f"SELECT i.* FROM inventory i JOIN products p ON p.sku = i.sku {clause} "
            f"ORDER BY i.sku LIMIT ?",
            params,
        ).fetchall()
    return [_to_record(r) for r in rows]


@router.get("/{sku}", response_model=InventoryRecord)
def get_inventory(sku: str) -> InventoryRecord:
    with session() as conn:
        row = conn.execute("SELECT * FROM inventory WHERE sku = ?", (sku,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No inventory for SKU '{sku}'")
    return _to_record(row)
