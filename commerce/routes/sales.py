"""Sales history endpoints (FR-051).

This is the input to elasticity estimation, so the response deliberately carries
the transacted `unit_price` alongside units — without price variation in this
series, elasticity is unidentifiable downstream.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from commerce.db import session
from commerce.models import SalesRecord

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("", response_model=list[SalesRecord])
def list_sales(
    sku: str | None = Query(None),
    category: str | None = Query(None),
    since: str | None = Query(None, description="ISO date, inclusive"),
    limit: int = Query(200000, ge=1, le=1000000),
) -> list[SalesRecord]:
    clauses, params = [], []
    if sku:
        clauses.append("s.sku = ?")
        params.append(sku)
    if category:
        clauses.append("p.category = ?")
        params.append(category)
    if since:
        clauses.append("s.sale_date >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    with session() as conn:
        rows = conn.execute(
            f"SELECT s.sku, s.sale_date, s.units, s.unit_price, s.revenue, s.on_promo "
            f"FROM sales s JOIN products p ON p.sku = s.sku {where} "
            f"ORDER BY s.sku, s.sale_date LIMIT ?",
            params,
        ).fetchall()

    return [
        SalesRecord(
            sku=r["sku"],
            sale_date=r["sale_date"],
            units=r["units"],
            unit_price=r["unit_price"],
            revenue=r["revenue"],
            on_promo=bool(r["on_promo"]),
        )
        for r in rows
    ]


@router.get("/summary")
def sales_summary(since: str | None = Query(None)) -> dict:
    clause = "WHERE s.sale_date >= ?" if since else ""
    params = [since] if since else []
    with session() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS rows, COALESCE(SUM(s.units), 0) AS units, "
            f"COALESCE(SUM(s.revenue), 0) AS revenue, MIN(s.sale_date) AS first_date, "
            f"MAX(s.sale_date) AS last_date FROM sales s {clause}",
            params,
        ).fetchone()
    return dict(row)
