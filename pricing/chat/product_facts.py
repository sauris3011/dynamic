"""Facts about a product or a category as it stands today.

Everything here is a read of the Commerce Service plus two things the platform
itself owns: the refined elasticity belief, and whatever the last run
recommended for this SKU. Those two are what make the answer more than a catalog
lookup — "it costs 3.40 and we sell it at 4.99" is a database query, "and the
loop now believes demand is more elastic than it did three runs ago" is not.
"""

from __future__ import annotations

from datetime import date

from pricing.chat.catalog import Catalog, representative_skus, resolve_from_route
from pricing.chat.facts import FactPack, margin_pct, money
from pricing.db.app_db import session
from pricing.feeds.competitor import SyntheticCompetitorFeed, build_positions
from pricing.llm.schemas import ChatRoute
from pricing.services import feedback

MAX_PRODUCTS = 3


def _latest_recommendation(sku: str) -> dict | None:
    with session() as conn:
        row = conn.execute(
            "SELECT rec_id, run_id, recommended_price, delta_pct, band, band_reason,"
            " confidence, compliance_status, status, created_at"
            " FROM recommendations WHERE sku = ? ORDER BY created_at DESC LIMIT 1",
            (sku,),
        ).fetchone()
    return dict(row) if row else None


def _competitive(products: list[dict]) -> dict:
    """Where each product sits against the market, from the same feed the
    pipeline prices against — so chat and the run cannot disagree."""
    our_prices = {p["sku"]: float(p["current_price"]) for p in products}
    feed = SyntheticCompetitorFeed(our_prices)
    return build_positions(feed.fetch(list(our_prices), on=date.today()), our_prices)


def _product_lines(product: dict, catalog: Catalog, position) -> list[str]:
    sku = product["sku"]
    price = float(product["current_price"])
    cost = float(product["unit_cost"])
    margin = margin_pct(price, cost)
    lines = [
        f"{sku} — {product.get('name')} ({product.get('brand')}, "
        f"{product.get('size_value')}{product.get('size_unit')}), category "
        f"{product.get('category')} / {product.get('subcategory')}.",
        f"{sku} shelf price {money(price)}, unit cost {money(cost)}, gross margin "
        f"{margin:.1f}% ({money(price - cost)} per unit). List price "
        f"{money(product.get('list_price'))}.",
    ]
    if product.get("map_price"):
        headroom = price - float(product["map_price"])
        lines.append(
            f"{sku} carries a MAP floor of {money(product['map_price'])} — "
            f"{money(headroom)} of headroom above it. No recommendation may go below."
        )
    else:
        lines.append(f"{sku} has no MAP agreement on file.")

    inv = catalog.inventory.get(sku)
    if inv:
        lines.append(
            f"{sku} stock: {inv['on_hand']:,} on hand, {inv['on_order']:,} on order, "
            f"{inv['weekly_velocity']:.1f} units/week, {inv['cover_days']:.0f} days cover."
        )
    if position:
        lines.append(
            f"{sku} against the market: cheapest rival {money(position.min_competitor_price)}, "
            f"mean {money(position.mean_competitor_price)}, we are "
            f"{position.gap_to_cheapest_pct:+.1f}% versus the cheapest "
            f"({position.competitors_in_stock} rival(s) in stock)."
        )

    estimate = feedback.get_estimate(sku)
    if estimate:
        lines.append(
            f"{sku} learned elasticity {estimate['elasticity']:.2f} (95% CI "
            f"{estimate['ci_low']:.2f} to {estimate['ci_high']:.2f}), refined "
            f"{estimate['refinement_count']} time(s) by the feedback loop; total "
            f"adjustment {estimate['total_adjustment']:+.2f}."
        )
    else:
        lines.append(
            f"{sku} has no refined elasticity yet — it is estimated per run from "
            "sales history until a readback measures a pushed price."
        )

    rec = _latest_recommendation(sku)
    if rec:
        lines.append(
            f"{sku} last recommendation ({rec['rec_id']}, run {rec['run_id']}): "
            f"{money(rec['recommended_price'])} ({rec['delta_pct']:+.1f}%), band "
            f"{rec['band']}, confidence {rec['confidence']:.2f}, compliance "
            f"{rec['compliance_status']}, status {rec['status']}."
        )
    else:
        lines.append(f"{sku} has not been priced by any run yet.")
    return lines


def _category_pack(category: str, catalog: Catalog) -> FactPack:
    rows = catalog.in_category(category)
    if not rows:
        return FactPack(shortfall=f"No category named '{category}' is in the catalog.")

    prices = [float(p["current_price"]) for p in rows]
    margins = [
        margin_pct(float(p["current_price"]), float(p["unit_cost"])) or 0.0 for p in rows
    ]
    mapped = sum(1 for p in rows if p.get("map_price"))
    covers = [
        catalog.inventory[p["sku"]]["cover_days"]
        for p in rows if p["sku"] in catalog.inventory
    ]
    busiest = representative_skus(catalog, category, limit=3)

    lines = [
        f"{category} holds {len(rows)} SKUs across "
        f"{len({p.get('subcategory') for p in rows})} subcategories and "
        f"{len({p.get('brand') for p in rows})} brands.",
        f"{category} shelf prices run {money(min(prices))} to {money(max(prices))}, "
        f"mean {money(sum(prices) / len(prices))}.",
        f"{category} mean gross margin {sum(margins) / len(margins):.1f}% "
        f"(thinnest {min(margins):.1f}%, fattest {max(margins):.1f}%).",
        f"{mapped} of {len(rows)} {category} SKUs are under a MAP agreement.",
    ]
    if covers:
        lines.append(
            f"{category} stock cover averages {sum(covers) / len(covers):.0f} days; "
            f"{sum(1 for c in covers if c < 14)} SKU(s) below 14 days."
        )
    if busiest:
        lines.append(
            "Busiest by velocity: "
            + ", ".join(
                f"{p['sku']} ({catalog.velocity(p['sku']):.0f}/wk)" for p in busiest
            )
            + "."
        )
    return FactPack(
        headline=f"{category} — {len(rows)} SKUs in the catalog.",
        lines=lines,
        data={"category": category, "sku_count": len(rows),
              "busiest": [p["sku"] for p in busiest]},
        sources=["Commerce Service catalog and inventory"],
        grounding_query=f"{category} pricing policy brand guidelines positioning",
        collections=["pricing_policy", "product_kb", "market_intel"],
    )


def build(route: ChatRoute, catalog: Catalog) -> FactPack:
    """Current-state facts for the products the question named."""
    if not catalog.available:
        return FactPack(shortfall=catalog.error or "The catalog is unavailable.")

    products, unknown = resolve_from_route(catalog, route, limit=MAX_PRODUCTS)
    if not products and route.category:
        return _category_pack(route.category, catalog)
    if not products:
        detail = (
            f"No catalog entry matches {', '.join(unknown)}."
            if unknown
            else "I could not tell which product you meant."
        )
        return FactPack(
            shortfall=(
                f"{detail} Name a SKU code (like {catalog.products[0]['sku']}), a "
                f"product name, or a category: {', '.join(catalog.categories)}."
            )
        )

    positions = _competitive(products)
    lines: list[str] = []
    for product in products:
        lines.extend(_product_lines(product, catalog, positions.get(product["sku"])))
    if unknown:
        lines.append(f"Not in the catalog: {', '.join(unknown)}.")

    names = ", ".join(f"{p['sku']} ({p.get('name')})" for p in products)
    return FactPack(
        headline=f"Catalog position for {names}.",
        lines=lines,
        data={"skus": [p["sku"] for p in products], "unknown": unknown},
        sources=["Commerce Service catalog, inventory and price book",
                 "Competitor feed", "Platform elasticity beliefs"],
        grounding_query=(
            f"{products[0].get('category')} {products[0].get('brand')} pricing policy "
            "margin floor MAP competitive positioning"
        ),
        collections=["pricing_policy", "product_kb", "market_intel"],
    )
