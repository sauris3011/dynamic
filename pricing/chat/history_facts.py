"""What already happened — sales, revenue, and price moves over a window.

Read from the Commerce Service, which is the system of record. The platform
keeps its own price observations for oscillation detection, but they are a
by-product of running; the transaction record is the retailer's. Answering
"how did it sell" from anywhere else would be answering from a copy.

Two windows are always measured, the requested one and the one before it, so a
number can be reported as a change rather than as a bare total. "38,400 in
revenue" tells an analyst nothing on its own.
"""

from __future__ import annotations

from datetime import date, timedelta

from pricing.chat.catalog import Catalog, resolve_from_route
from pricing.chat.facts import FactPack, money
from pricing.clients.commerce import CommerceClient
from pricing.core.logging import get_logger
from pricing.llm.schemas import ChatRoute

logger = get_logger("pricing.chat.history")

DEFAULT_WINDOW_DAYS = 90
BROAD_WINDOW_DAYS = 30      # when no product is named, keep the scan cheap
MAX_SKUS = 2


def _totals(rows: list[dict]) -> dict:
    units = sum(int(r["units"]) for r in rows)
    revenue = sum(float(r["revenue"]) for r in rows)
    promo = sum(1 for r in rows if r.get("on_promo"))
    days = len({r["sale_date"] for r in rows})
    return {
        "rows": len(rows), "days": days, "units": units,
        "revenue": round(revenue, 2),
        "mean_price": round(revenue / units, 2) if units else 0.0,
        "units_per_day": round(units / days, 1) if days else 0.0,
        "promo_days": promo,
    }


def _split(rows: list[dict], boundary: str) -> tuple[list[dict], list[dict]]:
    current = [r for r in rows if r["sale_date"] >= boundary]
    prior = [r for r in rows if r["sale_date"] < boundary]
    return current, prior


def _change(now: float, before: float) -> str:
    if not before:
        return "no comparable prior period"
    return f"{(now - before) / before * 100:+.1f}% versus the previous window"


def _window(route: ChatRoute, broad: bool) -> int:
    if route.days_back:
        return route.days_back
    return BROAD_WINDOW_DAYS if broad else DEFAULT_WINDOW_DAYS


def _sku_lines(sku: str, name: str, rows: list[dict], boundary: str,
               changes: list[dict], days: int) -> list[str]:
    current, prior = _split(rows, boundary)
    now, before = _totals(current), _totals(prior)
    if not now["rows"]:
        return [f"{sku} has no sales rows in the last {days} days."]

    lines = [
        f"{sku} ({name}) last {days} days: {now['units']:,} units, "
        f"{money(now['revenue'])} revenue, {now['units_per_day']} units/day, "
        f"mean transacted price {money(now['mean_price'])}.",
        f"{sku} revenue {_change(now['revenue'], before['revenue'])}; units "
        f"{_change(now['units'], before['units'])}.",
        f"{sku} was on promotion on {now['promo_days']} of {now['days']} trading days.",
    ]
    if before["rows"]:
        lines.append(
            f"{sku} mean price moved from {money(before['mean_price'])} to "
            f"{money(now['mean_price'])} between the two windows."
        )
    recent = [c for c in changes if c["changed_at"] >= boundary]
    if recent:
        latest = recent[-1]
        lines.append(
            f"{sku} price changed {len(recent)} time(s) in the window; most recent "
            f"{money(latest['old_price'])} to {money(latest['new_price'])} on "
            f"{latest['changed_at'][:10]} (source {latest['source']}, reason "
            f"{latest.get('reason') or 'unstated'})."
        )
    else:
        lines.append(f"{sku} shelf price was unchanged across the window.")
    return lines


def _clock_line(client: CommerceClient) -> str:
    try:
        clock = client.market_clock()
    except Exception as exc:  # noqa: BLE001 - a missing clock is not fatal
        logger.info("chat.clock_unavailable", error=str(exc)[:200])
        return ""
    return (
        f"The transaction record runs to {clock.get('last_date')} "
        f"({clock.get('days_behind_today')} day(s) behind today) across "
        f"{clock.get('sales_rows'):,} rows. Anything later has not been transacted yet."
    )


def build(route: ChatRoute, catalog: Catalog) -> FactPack:
    """Historical trading facts for a SKU, a category, or the whole estate."""
    if not catalog.available:
        return FactPack(shortfall=catalog.error or "The catalog is unavailable.")

    products, unknown = resolve_from_route(catalog, route, limit=MAX_SKUS)
    category = route.category
    days = _window(route, broad=not products and not category)
    since = (date.today() - timedelta(days=days * 2)).isoformat()
    boundary = (date.today() - timedelta(days=days)).isoformat()

    try:
        with CommerceClient() as client:
            clock = _clock_line(client)
            if products:
                data = {
                    p["sku"]: (
                        client.sales(sku=p["sku"], since=since),
                        client.price_history(p["sku"], limit=60),
                    )
                    for p in products
                }
                lines = [
                    line
                    for p in products
                    for line in _sku_lines(
                        p["sku"], str(p.get("name")), data[p["sku"]][0], boundary,
                        data[p["sku"]][1], days,
                    )
                ]
                scope = ", ".join(p["sku"] for p in products)
            elif category:
                rows = client.sales(category=category, since=since)
                lines = _group_lines(rows, boundary, days, catalog, category)
                scope = category
            else:
                rows = client.sales(since=since)
                lines = _group_lines(rows, boundary, days, catalog, "")
                scope = "the whole catalog"
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat.history_failed", error=f"{type(exc).__name__}: {exc}")
        return FactPack(
            shortfall=f"Sales history could not be read from the Commerce Service: {exc}"
        )

    if clock:
        lines.append(clock)
    if unknown:
        lines.append(f"Not in the catalog: {', '.join(unknown)}.")

    return FactPack(
        headline=f"Trading history for {scope}, last {days} days.",
        lines=lines,
        data={"window_days": days, "scope": scope},
        sources=["Commerce Service sales ledger and price history"],
        grounding_query=f"{scope} demand seasonality market trend {category}",
        collections=["market_intel", "product_kb"],
    )


def _group_lines(rows: list[dict], boundary: str, days: int,
                 catalog: Catalog, category: str) -> list[str]:
    """Aggregate lines for a category or the entire estate."""
    current, prior = _split(rows, boundary)
    now, before = _totals(current), _totals(prior)
    label = category or "The catalog"
    if not now["rows"]:
        return [f"{label} has no sales rows in the last {days} days."]

    by_sku: dict[str, float] = {}
    by_category: dict[str, float] = {}
    index = catalog.by_sku
    for row in current:
        by_sku[row["sku"]] = by_sku.get(row["sku"], 0.0) + float(row["revenue"])
        cat = str(index.get(row["sku"], {}).get("category", "unknown"))
        by_category[cat] = by_category.get(cat, 0.0) + float(row["revenue"])

    top = sorted(by_sku.items(), key=lambda kv: kv[1], reverse=True)[:5]
    lines = [
        f"{label} last {days} days: {now['units']:,} units and "
        f"{money(now['revenue'])} revenue across {len(by_sku)} SKUs.",
        f"{label} revenue {_change(now['revenue'], before['revenue'])}; units "
        f"{_change(now['units'], before['units'])}.",
        f"{label} mean transacted price {money(now['mean_price'])}, "
        f"{now['promo_days']:,} promoted SKU-days in the window.",
        "Top SKUs by revenue: "
        + ", ".join(
            f"{sku} {money(revenue)} ({index.get(sku, {}).get('name', 'unknown')})"
            for sku, revenue in top
        )
        + ".",
    ]
    if not category and len(by_category) > 1:
        ranked = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)
        lines.append(
            "Revenue by category: "
            + ", ".join(f"{name} {money(value)}" for name, value in ranked)
            + "."
        )
    return lines
