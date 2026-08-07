"""Evaluation harness: pricing accuracy against ground truth (PRD 9.1).

This is the strongest evidence available that the recommendations are genuinely
good rather than merely well-explained. Because the synthetic history was
generated *from* a known elasticity per SKU (FR-002), the theoretical optimum is
computable, so "pricing accuracy" is a measurement rather than an assertion.

**Why this lives outside the pipeline.** `pricing/clients/commerce.py` has no
wrapper for `/eval/ground-truth` and must never grow one: reading the answer key
mid-run would make this metric meaningless. The fetch happens here, in a module
the pipeline does not import, and the endpoint is called explicitly so its
appearance in a trace is unambiguous.

**The optimum, stated plainly.** For constant-elasticity demand `q = A·p^e` with
unit cost `c`, profit is maximized at `p* = c·e/(1+e)` when `e < -1`. When
`e > -1` demand is inelastic and profit rises monotonically with price, so the
optimum is whatever ceiling the constraints impose. Revenue alone is maximized
at the lowest feasible price for elastic SKUs and the highest for inelastic ones.
The optimum is then clamped into the same feasible band the optimizer worked in
— comparing an unconstrained ideal against a constrained recommendation would
score the constraints, not the model.
"""

from __future__ import annotations

import httpx

from pricing.analytics.optimizer import Objective
from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.core.tls import verify_option
from pricing.db.app_db import session

logger = get_logger("pricing.services.scoring")

HIGH_CONFIDENCE = 0.75
MARGIN_FLOOR_PCT = 15.0


def fetch_ground_truth(category: str | None = None) -> dict[str, dict]:
    """Read the answer key. Evaluation only — never called during a run."""
    s = get_settings()
    url = f"{s.commerce_base_url.rstrip('/')}/eval/ground-truth"
    params = {"category": category} if category else {}
    with httpx.Client(timeout=30.0, verify=verify_option(s)) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        rows = resp.json()
    return {r["sku"]: r for r in rows}


def theoretical_optimum(
    elasticity: float,
    unit_cost: float,
    current_price: float,
    max_change_pct: float,
    objective: Objective = Objective.BALANCED,
    map_price: float | None = None,
) -> float:
    """Constrained optimum under the true elasticity."""
    lo = current_price * (1 - max_change_pct / 100.0)
    hi = current_price * (1 + max_change_pct / 100.0)
    if map_price is not None:
        lo = max(lo, map_price)
    # The margin floor binds from below regardless of objective.
    lo = max(lo, unit_cost / (1 - MARGIN_FLOOR_PCT / 100.0))
    if hi < lo:
        hi = lo

    if objective is Objective.REVENUE:
        ideal = lo if elasticity < -1.0 else hi
    else:
        # Margin and balanced both chase profit; balanced is scored against the
        # profit optimum because that is the binding half of the objective.
        ideal = (
            unit_cost * elasticity / (1.0 + elasticity)
            if elasticity < -1.0001
            else hi
        )
    return round(min(max(ideal, lo), hi), 2)


def score(run_id: str | None = None, category: str | None = None) -> dict:
    """Compare recommended prices against the ground-truth optimum."""
    try:
        truth = fetch_ground_truth(category)
    except Exception as exc:  # noqa: BLE001
        return {
            "scored": 0,
            "error": f"Ground truth unavailable: {type(exc).__name__}: {exc}",
        }
    if not truth:
        return {"scored": 0, "error": "Commerce reported no ground-truth records."}

    clause, params = "", []
    if run_id:
        clause = "WHERE run_id = ?"
        params.append(run_id)
    else:
        # Most recent run only — scoring every historical run together would
        # average over models that have since been refined.
        clause = "WHERE run_id = (SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1)"
    if category:
        clause += " AND category = ?"
        params.append(category)

    with session() as conn:
        rows = [
            dict(r) for r in conn.execute(
                f"SELECT sku, category, current_price, recommended_price, unit_cost,"
                f" baseline_price, confidence, elasticity, band, objective_hint"
                f" FROM (SELECT r.*, runs.objective AS objective_hint"
                f"       FROM recommendations r JOIN runs ON runs.run_id = r.run_id)"
                f" {clause}",
                params,
            )
        ]

    settings = get_settings()
    scored, high_conf = [], []
    for row in rows:
        gt = truth.get(row["sku"])
        if not gt:
            continue
        objective = Objective(row.get("objective_hint") or "balanced")
        optimum = theoretical_optimum(
            elasticity=float(gt["true_elasticity"]),
            unit_cost=float(row["unit_cost"]),
            current_price=float(row["current_price"]),
            max_change_pct=settings.band_max_delta_pct,
            objective=objective,
        )
        if optimum <= 0:
            continue
        ai_dev = abs(row["recommended_price"] - optimum) / optimum * 100.0
        base_dev = (
            abs(row["baseline_price"] - optimum) / optimum * 100.0
            if row["baseline_price"] else None
        )
        entry = {
            "sku": row["sku"],
            "category": row["category"],
            "true_elasticity": round(float(gt["true_elasticity"]), 4),
            "estimated_elasticity": row["elasticity"],
            "optimum_price": optimum,
            "recommended_price": row["recommended_price"],
            "baseline_price": row["baseline_price"],
            "ai_deviation_pct": round(ai_dev, 2),
            "baseline_deviation_pct": round(base_dev, 2) if base_dev is not None else None,
            "confidence": row["confidence"],
            "band": row["band"],
        }
        scored.append(entry)
        if (row["confidence"] or 0) >= HIGH_CONFIDENCE:
            high_conf.append(entry)

    if not scored:
        return {"scored": 0, "error": "No recommendations matched a ground-truth SKU."}

    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 2) if values else 0.0

    ai_all = [e["ai_deviation_pct"] for e in scored]
    ai_high = [e["ai_deviation_pct"] for e in high_conf]
    base_all = [
        e["baseline_deviation_pct"] for e in scored
        if e["baseline_deviation_pct"] is not None
    ]
    within_10 = sum(1 for v in ai_high if v <= 10.0)

    return {
        "scored": len(scored),
        "high_confidence_skus": len(high_conf),
        "ai_mean_deviation_pct": mean(ai_all),
        "ai_mean_deviation_pct_high_confidence": mean(ai_high),
        "baseline_mean_deviation_pct": mean(base_all),
        "within_10pct_high_confidence": within_10,
        "within_10pct_rate": (
            round(within_10 / len(ai_high), 4) if ai_high else None
        ),
        "target": "Within 10% of the ground-truth optimum for high-confidence SKUs.",
        "worst": sorted(scored, key=lambda e: -e["ai_deviation_pct"])[:10],
        "best": sorted(scored, key=lambda e: e["ai_deviation_pct"])[:10],
    }
