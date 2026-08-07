"""Read-only product views for the operator UI.

Business data remains owned by Commerce; this route only composes its public
HTTP APIs into a convenient product-detail response.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query

from pricing.clients.commerce import CommerceClient, CommerceUnavailable

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("")
def list_products(
    search: str | None = Query(None, max_length=100),
    category: str | None = Query(None),
    limit: int = Query(250, ge=1, le=1000),
) -> list[dict]:
    try:
        with CommerceClient() as client:
            products = client.products(category=category, limit=10000)
    except CommerceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Commerce catalog request failed: {exc}") from exc

    needle = (search or "").strip().casefold()
    if needle:
        searchable = ("sku", "name", "brand", "category", "subcategory", "family_id")
        products = [
            product for product in products
            if any(needle in str(product.get(field, "")).casefold() for field in searchable)
        ]
    return products[:limit]


@router.get("/{sku}")
def get_product(sku: str) -> dict:
    try:
        with CommerceClient() as client:
            product = client.product(sku)
            inventory = client.inventory_for_sku(sku)
            price_history = client.price_history(sku, limit=20)
            sales = client.sales(sku=sku, limit=90)
    except httpx.HTTPStatusError as exc:
        status = 404 if exc.response.status_code == 404 else 502
        raise HTTPException(status, f"Product '{sku}' could not be loaded") from exc
    except (CommerceUnavailable, httpx.HTTPError) as exc:
        raise HTTPException(503, f"Commerce Service unavailable: {exc}") from exc

    recent_sales = sorted(sales, key=lambda row: row.get("sale_date", ""), reverse=True)
    return {
        "product": product,
        "inventory": inventory,
        "price_history": price_history,
        "recent_sales": recent_sales[:30],
    }
