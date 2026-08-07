"""Catalog endpoints (FR-051)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from commerce.db import session
from commerce.models import Product

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/products", response_model=list[Product])
def list_products(
    category: str | None = Query(None),
    family_id: str | None = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    offset: int = Query(0, ge=0),
) -> list[Product]:
    clauses, params = [], []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if family_id:
        clauses.append("family_id = ?")
        params.append(family_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.extend([limit, offset])

    with session() as conn:
        rows = conn.execute(
            f"SELECT * FROM products {where} ORDER BY sku LIMIT ? OFFSET ?", params
        ).fetchall()
    return [Product(**dict(r)) for r in rows]


@router.get("/products/{sku}", response_model=Product)
def get_product(sku: str) -> Product:
    with session() as conn:
        row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"SKU '{sku}' not found")
    return Product(**dict(row))


@router.get("/categories", response_model=list[str])
def list_categories() -> list[str]:
    with session() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM products ORDER BY category"
        ).fetchall()
    return [r["category"] for r in rows]
