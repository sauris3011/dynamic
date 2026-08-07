"""Catalog snapshot and entity resolution for chat.

One question can touch the catalog three or four times — resolve a SKU, name a
category, price a family. Fetching all of it per question would put four HTTP
round trips on the critical path of a chat reply, so the catalog is pulled once
and held briefly. It is a read-through cache of another service's data, never a
copy of it: nothing here is written, and the TTL is short enough that a price
push is visible in the next question but one.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from pricing.clients.commerce import CommerceClient
from pricing.core.logging import get_logger

logger = get_logger("pricing.chat.catalog")

TTL_SECONDS = 45.0

# Seeded SKUs look like BEV-0001-1. Matched loosely enough to survive a
# different catalog convention, tightly enough not to claim every hyphenated
# token in a sentence is a product code.
SKU_PATTERN = re.compile(r"\b[A-Za-z]{2,5}-\d{2,6}(?:-\d{1,3})?\b")

_STOPWORDS = {
    "the", "a", "an", "for", "of", "and", "our", "my", "this", "that", "price",
    "prices", "product", "products", "sku", "what", "how", "much", "is", "are",
    "we", "sell", "sells", "sold", "much", "cost", "costs", "if", "raise",
    "cut", "increase", "decrease", "by", "to", "on", "in", "last", "next",
}


@dataclass
class Catalog:
    """Everything chat needs to name a product, gathered once."""

    products: list[dict] = field(default_factory=list)
    inventory: dict[str, dict] = field(default_factory=dict)
    fetched_at: float = 0.0
    error: str = ""

    @property
    def available(self) -> bool:
        return bool(self.products)

    @property
    def by_sku(self) -> dict[str, dict]:
        return {p["sku"]: p for p in self.products}

    @property
    def categories(self) -> list[str]:
        return sorted({p["category"] for p in self.products if p.get("category")})

    def in_category(self, category: str) -> list[dict]:
        needle = category.strip().casefold()
        return [
            p for p in self.products
            if str(p.get("category", "")).casefold() == needle
        ]

    def velocity(self, sku: str) -> float:
        return float(self.inventory.get(sku, {}).get("weekly_velocity", 0.0))


_cache: Catalog | None = None


def snapshot(force: bool = False) -> Catalog:
    """The current catalog, refetched at most every `TTL_SECONDS`.

    A failure is returned rather than raised: chat degrades to the questions it
    can still answer from app.db instead of going dark entirely because the
    Commerce Service is restarting.
    """
    global _cache
    now = time.time()
    if not force and _cache and now - _cache.fetched_at < TTL_SECONDS:
        return _cache

    try:
        with CommerceClient() as client:
            products = client.products(limit=10000)
            inventory = {row["sku"]: row for row in client.inventory()}
        _cache = Catalog(products=products, inventory=inventory, fetched_at=now)
    except Exception as exc:  # noqa: BLE001 - any failure degrades to no catalog
        logger.warning("chat.catalog_unavailable", error=f"{type(exc).__name__}: {exc}")
        _cache = Catalog(fetched_at=now, error=f"Commerce Service unreachable: {exc}")
    return _cache


def invalidate() -> None:
    global _cache
    _cache = None


def find_sku_tokens(text: str) -> list[str]:
    """SKU codes mentioned literally, uppercased and de-duplicated in order."""
    seen: list[str] = []
    for match in SKU_PATTERN.findall(text or ""):
        code = match.upper()
        if code not in seen:
            seen.append(code)
    return seen


def match_category(text: str, categories: list[str]) -> str:
    """The category named in the text, if one is. Longest match wins so
    'Coffee & Tea' is not shadowed by a bare 'Tea'."""
    lowered = (text or "").casefold()
    hits = [c for c in categories if c.casefold() in lowered]
    if hits:
        return max(hits, key=len)
    # Second pass on the distinctive word, so "coffee prices" finds Coffee & Tea.
    for category in sorted(categories, key=len, reverse=True):
        for word in re.split(r"[^a-z]+", category.casefold()):
            if len(word) >= 4 and re.search(rf"\b{word}\b", lowered):
                return category
    return ""


def _score(product: dict, terms: list[str]) -> int:
    haystack = " ".join(
        str(product.get(k, "")) for k in ("name", "brand", "subcategory", "category")
    ).casefold()
    return sum(1 for term in terms if term in haystack)


def resolve_products(
    catalog: Catalog, codes: list[str], phrase: str = "", limit: int = 5
) -> tuple[list[dict], list[str]]:
    """Turn what the analyst said into actual catalog rows.

    Returns (products, unknown_codes). A code the catalog does not have is
    reported rather than quietly dropped — "no such SKU" is a useful answer and
    silently pricing a different product is not.
    """
    by_sku = catalog.by_sku
    resolved: list[dict] = []
    unknown: list[str] = []
    for code in codes:
        product = by_sku.get(code)
        if product:
            resolved.append(product)
        else:
            unknown.append(code)

    if not resolved and phrase.strip():
        terms = [
            t for t in re.split(r"[^a-z0-9&]+", phrase.casefold())
            if len(t) > 2 and t not in _STOPWORDS
        ]
        if terms:
            ranked = sorted(
                ((_score(p, terms), p) for p in catalog.products),
                key=lambda pair: pair[0], reverse=True,
            )
            resolved = [p for score, p in ranked[:limit] if score > 0]

    return resolved[:limit], unknown


def resolve_from_route(catalog: Catalog, route, limit: int = 5) -> tuple[list[dict], list[str]]:
    """Resolve the products a classified question is about.

    A question that named a category and no SKU is about the category. Fuzzy
    matching the sentence anyway turns "how has Coffee & Tea traded?" into a
    report on two coffee SKUs — an answer to a question nobody asked, and one
    that looks authoritative because it is full of real numbers.
    """
    phrase = "" if (route.category and not route.skus) else (
        route.product_query or route.restated
    )
    return resolve_products(catalog, list(route.skus), phrase, limit)


def representative_skus(catalog: Catalog, category: str, limit: int = 3) -> list[dict]:
    """The busiest SKUs in a category — the ones worth simulating when a
    question names a category but no product. Ranked by weekly velocity so the
    sample is what actually moves, not whatever sorted first."""
    rows = catalog.in_category(category)
    rows.sort(key=lambda p: catalog.velocity(p["sku"]), reverse=True)
    return rows[:limit]
