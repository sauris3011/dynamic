"""What-if projection for chat.

Delegates to `pricing.services.simulation` — the same Monte Carlo engine the
pipeline prices with (FR-105). A chat answer and a recommendation are therefore
directly comparable: if the assistant says a 5% rise is worth 1,200 over four
weeks, that is the number the pipeline would forecast for the same price, not a
second opinion produced by a second implementation.

The projection is always a distribution with its assumptions attached
(FR-047). A chat interface invites bare point estimates precisely because it
reads conversationally, which is the reason to refuse them here.
"""

from __future__ import annotations

import re

from pricing.analytics.optimizer import Objective
from pricing.chat.catalog import Catalog, representative_skus, resolve_from_route
from pricing.chat.facts import FactPack, money
from pricing.clients.commerce import CommerceUnavailable
from pricing.core.logging import get_logger
from pricing.llm.schemas import ChatRoute
from pricing.services import simulation

logger = get_logger("pricing.chat.whatif")

MAX_SKUS = 3
DEFAULT_HORIZON = 28
_STRESS = re.compile(
    r"\b(stress|downside|worst|risk|collapse|undercut|spike|recession)\b", re.IGNORECASE
)


def _scenario_price(current: float, route: ChatRoute) -> float | None:
    if route.target_price:
        return round(float(route.target_price), 2)
    if route.delta_pct:
        return round(current * (1 + route.delta_pct / 100.0), 2)
    return None


def _find(candidates: list[dict], price: float) -> dict | None:
    for candidate in candidates:
        if abs(float(candidate["price"]) - price) < 0.005:
            return candidate
    return None


def _scenario_lines(result: dict, price: float, horizon: int) -> list[str]:
    sku, current = result["sku"], float(result["current_price"])
    outcome = _find(result["candidates"], price)
    baseline = result["baseline"]
    if outcome is None:
        return [
            f"{sku}: {money(price)} could not be projected — it falls outside the "
            f"±{abs((price - current) / current * 100):.0f}% band the engine sweeps."
        ]

    delta_pct = (price - current) / current * 100 if current else 0.0
    revenue_delta = outcome["expected_revenue"] - baseline["revenue"]
    margin_delta = outcome["expected_margin"] - baseline["margin"]
    compliance = result["compliance"].get(f"{price:.2f}", {})

    lines = [
        f"{sku} at {money(price)} ({delta_pct:+.1f}% from {money(current)}) over "
        f"{horizon} days: expected revenue {money(outcome['expected_revenue'])} "
        f"against {money(baseline['revenue'])} today — a change of "
        f"{money(revenue_delta)}.",
        f"{sku} 90% revenue band {money(outcome['revenue_p5'])} to "
        f"{money(outcome['revenue_p95'])}; probability of a revenue gain "
        f"{outcome['prob_revenue_gain']:.0%}.",
        f"{sku} expected margin {money(outcome['expected_margin'])} against "
        f"{money(baseline['margin'])} today ({money(margin_delta)}); probability of "
        f"falling below the 15% margin floor {outcome['prob_below_margin_floor']:.0%}.",
        f"{sku} expected volume {outcome['expected_units']:,.0f} units over the "
        f"horizon against {baseline['units']:,.0f} at today's price.",
    ]
    if compliance:
        verdict = "passes every rule" if compliance.get("passed") else (
            "is BLOCKED by " + ", ".join(compliance.get("violations", []))
        )
        lines.append(f"{sku} at {money(price)} {verdict}. {compliance.get('summary', '')}".strip())
    return lines


def _comparison_lines(result: dict) -> list[str]:
    sku = result["sku"]
    ai = result["ai_recommendation"]
    rule = result["rule_based_baseline"]
    elasticity = result["elasticity"]
    lines = [
        f"{sku}: on the {ai['objective']} objective the platform would itself "
        + (
            f"hold at {money(result['current_price'])}."
            if ai["hold"]
            else f"recommend {money(ai['price'])}, worth "
                 f"{money(ai['expected_revenue_delta'])} (90% CI "
                 f"{money(ai['revenue_ci_low'])} to {money(ai['revenue_ci_high'])})."
        ),
        f"{sku}: the conventional rule-based pricer would say {money(rule['price'])} "
        f"({rule['delta_pct']:+.1f}%) from rules {', '.join(rule['rules_fired']) or 'none'} "
        "— no demand model and no interval behind it.",
    ]
    if elasticity["usable"]:
        lines.append(
            f"{sku} elasticity {elasticity['value']:.2f} (95% CI "
            f"{elasticity['ci_low']:.2f} to {elasticity['ci_high']:.2f}, n="
            f"{elasticity['sample_size']}) — the projection samples across that "
            "interval rather than the point estimate."
        )
    else:
        lines.append(
            f"{sku} has no usable elasticity estimate ({elasticity['reason']}); the "
            "projection falls back to a wide category prior and should be read as "
            "indicative only."
        )
    return lines


def _stress_lines(result: dict) -> list[str]:
    stress = result.get("stress")
    if not stress:
        return []
    lines = [
        f"{result['sku']} stress-tested at {money(stress['price_tested'])}:"
    ]
    for name, outcome in stress["scenarios"].items():
        lines.append(
            f"  under {name.replace('_', ' ')}: revenue "
            f"{money(outcome['expected_revenue'])}, margin "
            f"{money(outcome['expected_margin'])}, P(below margin floor) "
            f"{outcome['prob_below_margin_floor']:.0%}."
        )
    return lines


def build(route: ChatRoute, catalog: Catalog, question: str) -> FactPack:
    """Project the hypothetical the analyst asked about."""
    if not catalog.available:
        return FactPack(shortfall=catalog.error or "The catalog is unavailable.")

    products, unknown = resolve_from_route(catalog, route, limit=MAX_SKUS)
    note = ""
    if not products and route.category:
        products = representative_skus(catalog, route.category, limit=MAX_SKUS)
        note = (
            f"No SKU was named, so the {len(products)} busiest {route.category} SKUs "
            "by weekly velocity stand in for the category. Ask about a specific SKU "
            "for a projection you can act on."
        )
    if not products:
        return FactPack(
            shortfall=(
                "I need a product to project. Name a SKU code, a product name, or a "
                f"category ({', '.join(catalog.categories)}) and the move you are "
                "considering — for example 'what if we raise "
                f"{catalog.products[0]['sku']} by 5%'."
            )
        )

    horizon = route.horizon_days or DEFAULT_HORIZON
    objective = Objective(route.objective or "balanced")
    prices = {}
    for product in products:
        price = _scenario_price(float(product["current_price"]), route)
        if price:
            prices[product["sku"]] = [price, float(product["current_price"])]

    try:
        result = simulation.run(
            skus=[p["sku"] for p in products],
            candidate_prices=prices or None,
            horizon_days=horizon,
            objective=objective,
            include_stress=bool(_STRESS.search(question)),
        )
    except CommerceUnavailable as exc:
        return FactPack(shortfall=f"The scenario could not be run: {exc}")

    lines: list[str] = []
    for entry in result["results"]:
        scenario = _scenario_price(float(entry["current_price"]), route)
        if scenario:
            lines.extend(_scenario_lines(entry, scenario, horizon))
        else:
            lines.append(
                f"{entry['sku']}: no specific price was named, so the engine swept the "
                f"standard band around {money(entry['current_price'])}."
            )
        lines.extend(_comparison_lines(entry))
        lines.extend(_stress_lines(entry))

    first = result["results"][0] if result["results"] else {}
    if first.get("assumptions"):
        lines.extend(f"Assumption: {a}" for a in first["assumptions"])
    if note:
        lines.append(note)
    if unknown:
        lines.append(f"Not in the catalog: {', '.join(unknown)}.")

    move = (
        f"{route.delta_pct:+.1f}%" if route.delta_pct
        else (f"a move to {money(route.target_price)}" if route.target_price
              else "the standard band sweep")
    )
    return FactPack(
        headline=(
            f"Scenario: {move} on {', '.join(p['sku'] for p in products)} over "
            f"{horizon} days, {objective.value} objective."
        ),
        lines=lines,
        data={"horizon_days": horizon, "objective": objective.value,
              "simulation": result},
        sources=[
            f"Monte Carlo engine, {len(result['results'])} SKU(s), same engine as "
            "the pricing pipeline",
            "Compliance rule engine",
        ],
        grounding_query=(
            f"{products[0].get('category')} price change policy competitive response "
            "margin floor"
        ),
        collections=["pricing_policy", "market_intel"],
    )
