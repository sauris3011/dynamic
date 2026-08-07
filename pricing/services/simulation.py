"""Scenario simulation (FR-045 .. FR-050, FR-105, FR-106).

**The same Monte Carlo engine as the pricing pipeline** — deliberately, and this
is the point of FR-105. If simulation had its own projection code, an analyst's
what-if and the recommendation's forecast could disagree while both were
"correct", and nobody could tell which to believe. One engine means a simulated
outcome and a recommendation forecast are directly comparable because they are
the same computation over the same distributions.

What this adds on top of the engine is framing, not mathematics: a horizon,
the assumptions written out in full, the rule-based baseline alongside the AI
recommendation, and the downside under stress.

Results are distributions with stated assumptions, never bare point estimates
(FR-047). A single projected number implies a precision the data does not have.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from pricing.analytics import baseline as baseline_mod
from pricing.analytics.elasticity import ElasticityEstimate, estimate_from_records
from pricing.analytics.montecarlo import PriceOutcome, simulate, stress_test
from pricing.analytics.optimizer import Objective, PriceConstraints, candidate_grid, optimize
from pricing.clients.commerce import CommerceClient
from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.db.app_db import now_iso, session
from pricing.feeds.competitor import SyntheticCompetitorFeed, build_positions
from pricing.rules.engine import RuleConfig, evaluate
from pricing.services import feedback

logger = get_logger("pricing.services.simulation")

RECENT_DEMAND_DAYS = 28
MARGIN_FLOOR_PCT = 15.0


@dataclass
class SkuInputs:
    """Everything one SKU needs to be simulated, gathered once."""

    product: dict
    elasticity: ElasticityEstimate
    base_demand: float
    cover_days: float | None
    min_competitor_price: float | None
    family_prices: list[tuple[float, float]]


def _load(skus: list[str]) -> dict[str, SkuInputs]:
    """Pull context for the requested SKUs from the Commerce Service."""
    wanted = set(skus)
    with CommerceClient() as client:
        client.require_healthy()
        products = [p for p in client.products(limit=10000) if p["sku"] in wanted]
        if not products:
            return {}
        families = {p["family_id"] for p in products}
        siblings = [
            p for p in client.products(limit=10000) if p["family_id"] in families
        ]
        inventory = {i["sku"]: i for i in client.inventory()}
        sales_by_sku: dict[str, list[dict]] = {}
        for sku in wanted:
            sales_by_sku[sku] = client.sales(sku=sku)

    our_prices = {p["sku"]: p["current_price"] for p in products}
    feed = SyntheticCompetitorFeed(our_prices)
    positions = build_positions(feed.fetch(list(our_prices), on=date.today()), our_prices)

    out: dict[str, SkuInputs] = {}
    for product in products:
        sku = product["sku"]
        records = sales_by_sku.get(sku, [])
        recent = records[-RECENT_DEMAND_DAYS:]
        base_demand = (
            sum(r["units"] for r in recent) / len(recent) if recent else 0.0
        )
        est = feedback_adjusted(estimate_from_records(sku, records))
        inv = inventory.get(sku)
        pos = positions.get(sku)
        out[sku] = SkuInputs(
            product=product,
            elasticity=est,
            base_demand=float(base_demand),
            cover_days=inv["cover_days"] if inv else None,
            min_competitor_price=pos.min_competitor_price if pos else None,
            family_prices=[
                (p["size_value"], p["current_price"])
                for p in siblings
                if p["family_id"] == product["family_id"] and p["sku"] != sku
            ],
        )
    return out


def feedback_adjusted(est: ElasticityEstimate) -> ElasticityEstimate:
    """Use the refined belief so a what-if matches what the pipeline would do."""
    stored = feedback.get_estimate(est.sku)
    if stored and est.usable:
        est.elasticity = float(stored["elasticity"])
        est.ci_low = float(stored["ci_low"])
        est.ci_high = float(stored["ci_high"])
        est.std_error = max(abs(est.ci_high - est.ci_low) / 3.92, 1e-4)
    return est


def _outcome_dict(o: PriceOutcome, horizon: int) -> dict:
    """Scale a per-day distribution to the requested horizon (FR-046)."""
    return {
        "price": o.price,
        "expected_units": round(o.expected_units * horizon, 1),
        "expected_revenue": round(o.expected_revenue * horizon, 2),
        "expected_margin": round(o.expected_margin * horizon, 2),
        "revenue_p5": round(o.revenue_p5 * horizon, 2),
        "revenue_p50": round(o.revenue_p50 * horizon, 2),
        "revenue_p95": round(o.revenue_p95 * horizon, 2),
        "margin_p5": round(o.margin_p5 * horizon, 2),
        "margin_p50": round(o.margin_p50 * horizon, 2),
        "revenue_cv": o.revenue_cv,
        "prob_below_margin_floor": o.prob_below_margin_floor,
        "prob_revenue_gain": o.prob_revenue_gain,
    }


def _assumptions(inp: SkuInputs, horizon: int, iterations: int, seed: int) -> list[str]:
    """Every assumption behind the number, written out (FR-047, W3)."""
    est = inp.elasticity
    items = [
        f"Demand response is modelled from {RECENT_DEMAND_DAYS}-day mean volume of "
        f"{inp.base_demand:.1f} units/day, held flat across the {horizon}-day horizon.",
        f"Unit cost {inp.product['unit_cost']:.2f} with drift sampled at 3% SD.",
        "Competitor response sampled at 6% SD — rivals may partially follow.",
        f"{iterations:,} Monte Carlo iterations, seed {seed}: identical inputs "
        "reproduce this distribution exactly.",
        f"Margin floor {MARGIN_FLOOR_PCT:.0f}% is the threshold behind "
        "P(below margin floor).",
    ]
    if est.usable:
        items.insert(
            0,
            f"Elasticity {est.elasticity:.2f} (95% CI {est.ci_low:.2f} to "
            f"{est.ci_high:.2f}, n={est.sample_size}); the simulation samples "
            "across that interval, not the point estimate.",
        )
    else:
        items.insert(
            0,
            "No usable elasticity estimate for this SKU — simulated against a wide "
            "category prior (-1.5 ± 0.9). Treat the interval as indicative only.",
        )
    if inp.cover_days is not None:
        items.append(f"Opening stock cover {inp.cover_days:.0f} days.")
    return items


def simulate_sku(
    sku: str,
    inp: SkuInputs,
    candidate_prices: list[float] | None,
    horizon_days: int,
    objective: Objective,
    include_stress: bool,
) -> dict:
    """Project one SKU across candidate prices, with assumptions and downside."""
    s = get_settings()
    product = inp.product
    current = float(product["current_price"])

    grid = (
        sorted({round(float(p), 2) for p in candidate_prices if p and p > 0})
        if candidate_prices
        else [round(float(p), 2) for p in candidate_grid(current, s.band_max_delta_pct)]
    )
    if current not in grid:
        grid.append(current)
        grid.sort()

    sim = simulate(
        sku=sku,
        candidate_prices=grid,
        current_price=current,
        unit_cost=product["unit_cost"],
        base_demand=inp.base_demand,
        elasticity=inp.elasticity,
        margin_floor_pct=MARGIN_FLOOR_PCT,
        iterations=s.mc_iterations,
        seed=s.mc_seed,
        inputs=feedback.refined_uncertainty(),
    )

    # The AI recommendation on this same distribution — so a scenario can be
    # compared against what the system would actually have proposed (FR-049).
    opt = optimize(
        sim,
        product["unit_cost"],
        PriceConstraints(
            margin_floor_pct=MARGIN_FLOOR_PCT,
            max_change_pct=s.band_max_delta_pct,
            map_price=product.get("map_price"),
        ),
        objective,
    )
    bl = baseline_mod.price_one(
        sku=sku,
        category=product["category"],
        unit_cost=product["unit_cost"],
        current_price=current,
        cover_days=inp.cover_days,
        min_competitor_price=inp.min_competitor_price,
        map_price=product.get("map_price"),
    )

    compliance = {
        f"{o.price:.2f}": _compliance_for(sku, o.price, inp)
        for o in sim.outcomes
    }

    result = {
        "sku": sku,
        "product_name": product.get("name"),
        "category": product.get("category"),
        "current_price": round(current, 2),
        "unit_cost": product["unit_cost"],
        "horizon_days": horizon_days,
        "baseline": {
            "revenue": round(sim.baseline_revenue * horizon_days, 2),
            "margin": round(sim.baseline_margin * horizon_days, 2),
            "units": round(inp.base_demand * horizon_days, 1),
            "cover_days": inp.cover_days,
        },
        "candidates": [_outcome_dict(o, horizon_days) for o in sim.outcomes],
        "compliance": compliance,
        "ai_recommendation": {
            "price": opt.recommended_price,
            "hold": opt.hold,
            "objective": objective.value,
            "expected_revenue_delta": round(opt.expected_revenue_delta * horizon_days, 2),
            "revenue_ci_low": round(opt.revenue_ci_low * horizon_days, 2),
            "revenue_ci_high": round(opt.revenue_ci_high * horizon_days, 2),
            "reason": opt.reason,
        },
        "rule_based_baseline": {
            "price": bl.baseline_price,
            "delta_pct": bl.delta_pct,
            "rules_fired": bl.rules_fired,
            "note": "Cost-plus with category rules — no demand model, no interval.",
        },
        "elasticity": {
            "value": inp.elasticity.elasticity if inp.elasticity.usable else None,
            "ci_low": inp.elasticity.ci_low if inp.elasticity.usable else None,
            "ci_high": inp.elasticity.ci_high if inp.elasticity.usable else None,
            "sample_size": inp.elasticity.sample_size,
            "usable": inp.elasticity.usable,
            "reason": inp.elasticity.reason,
        },
        "assumptions": _assumptions(inp, horizon_days, sim.iterations, sim.seed),
        "degraded": sim.degraded,
        "note": sim.note,
    }

    if include_stress:
        target = grid[len(grid) // 2] if len(grid) == 1 else opt.recommended_price
        stressed = stress_test(
            sku=sku,
            price=target,
            current_price=current,
            unit_cost=product["unit_cost"],
            base_demand=inp.base_demand,
            elasticity=inp.elasticity,
            margin_floor_pct=MARGIN_FLOOR_PCT,
            iterations=max(s.mc_iterations // 2, 500),
            seed=s.mc_seed,
        )
        result["stress"] = {
            "price_tested": target,
            "scenarios": {
                name: _outcome_dict(o, horizon_days) for name, o in stressed.items()
            },
        }
    return result


def _compliance_for(sku: str, price: float, inp: SkuInputs) -> dict:
    """A simulated price is still subject to the veto — showing an attractive
    projection for a price that could never ship would be misleading."""
    product = inp.product
    verdict = evaluate(
        sku=sku,
        proposed_price=price,
        current_price=float(product["current_price"]),
        unit_cost=product["unit_cost"],
        category=product["category"],
        map_price=product.get("map_price"),
        family_prices=inp.family_prices,
        size_value=product.get("size_value"),
        config=RuleConfig(margin_floor_pct=MARGIN_FLOOR_PCT),
    )
    return {
        "passed": verdict.passed,
        "violations": [e.code for e in verdict.violations],
        "summary": verdict.summary,
    }


def run(
    skus: list[str],
    candidate_prices: dict[str, list[float]] | None = None,
    horizon_days: int = 28,
    objective: Objective = Objective.BALANCED,
    include_stress: bool = False,
) -> dict:
    """Simulate one or many SKUs (FR-045)."""
    started = datetime.now()
    inputs = _load(skus)
    missing = [s for s in skus if s not in inputs]

    results = [
        simulate_sku(
            sku, inputs[sku], (candidate_prices or {}).get(sku),
            horizon_days, objective, include_stress,
        )
        for sku in skus
        if sku in inputs
    ]

    totals = {
        "skus": len(results),
        "baseline_revenue": round(sum(r["baseline"]["revenue"] for r in results), 2),
        "ai_revenue_delta": round(
            sum(r["ai_recommendation"]["expected_revenue_delta"] for r in results), 2
        ),
    }
    logger.info(
        "simulation.run", skus=len(results), horizon=horizon_days,
        seconds=round((datetime.now() - started).total_seconds(), 2),
    )
    return {
        "horizon_days": horizon_days,
        "objective": objective.value,
        "results": results,
        "totals": totals,
        "missing_skus": missing,
        "generated_at": now_iso(),
    }


# --- Saved scenarios (FR-048) -------------------------------------------

def save_scenario(name: str, request: dict, result: dict, actor: str = "operator") -> dict:
    scenario_id = f"scn-{uuid.uuid4().hex[:10]}"
    with session() as conn:
        conn.execute(
            "INSERT INTO scenarios (scenario_id, name, created_at, created_by,"
            " horizon_days, request_json, result_json) VALUES (?,?,?,?,?,?,?)",
            (
                scenario_id, name, now_iso(), actor,
                int(result.get("horizon_days", 28)),
                json.dumps(request, default=str), json.dumps(result, default=str),
            ),
        )
    return {"scenario_id": scenario_id, "name": name}


def list_scenarios(limit: int = 50) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT scenario_id, name, created_at, created_by, horizon_days"
            " FROM scenarios ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_scenario(scenario_id: str) -> dict | None:
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["request"] = json.loads(data.pop("request_json"))
    data["result"] = json.loads(data.pop("result_json"))
    return data
