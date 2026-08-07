"""Dashboard aggregations (FR-056, FR-057, FR-061, FR-112, FR-113, PRD 9.1).

Read-only queries over app.db. Nothing here decides anything; it counts what
already happened. Kept out of the route layer so the SQL is testable without
spinning up FastAPI, and so the route file stays a thin transport shim.

Every number is reported with its denominator. A dashboard that shows "78%
acceptance" without saying 78% of what is decoration, not measurement.
"""

from __future__ import annotations

import json

from pricing.db.app_db import session
from pricing.services import feedback


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with session() as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def _one(sql: str, params: tuple = ()) -> dict:
    with session() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def performance(run_id: str | None = None) -> dict:
    """Revenue/margin impact, forecast vs realized, acceptance rate (FR-056)."""
    clause = "WHERE run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()

    totals = _one(
        f"SELECT COUNT(*) AS recommendations,"
        f" COALESCE(SUM(expected_revenue_delta), 0) AS revenue_delta,"
        f" COALESCE(SUM(expected_margin_delta), 0) AS margin_delta,"
        f" COALESCE(AVG(confidence), 0) AS mean_confidence,"
        f" COALESCE(SUM(CASE WHEN ABS(delta_pct) > 0.01 THEN 1 ELSE 0 END), 0) AS movers"
        f" FROM recommendations {clause}",
        params,
    )
    by_status = {
        r["status"]: r["n"] for r in _rows(
            f"SELECT status, COUNT(*) AS n FROM recommendations {clause}"
            f" GROUP BY status", params,
        )
    }

    decided = sum(
        by_status.get(k, 0) for k in ("approved", "rejected", "overridden", "pushed")
    )
    accepted = by_status.get("approved", 0) + by_status.get("pushed", 0)
    # FR-058/9.1: acceptance is approvals as a share of *decided* items. Counting
    # untouched pending items as rejections would understate it; counting them as
    # acceptances would flatter it.
    acceptance_rate = round(accepted / decided, 4) if decided else None

    realized = _one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(forecast_revenue), 0) AS forecast,"
        " COALESCE(SUM(realized_revenue), 0) AS realized,"
        " COALESCE(AVG(ABS(forecast_error_pct)), 0) AS mean_abs_error"
        " FROM outcomes"
    )
    within_ci = _one(
        "SELECT COUNT(*) AS n FROM outcomes WHERE ABS(forecast_error_pct) <= 20"
    )["n"]

    return {
        "recommendations": totals.get("recommendations", 0),
        "price_changes_proposed": totals.get("movers", 0),
        "forecast_revenue_delta": round(totals.get("revenue_delta", 0.0), 2),
        "forecast_margin_delta": round(totals.get("margin_delta", 0.0), 2),
        "mean_confidence": round(totals.get("mean_confidence", 0.0), 4),
        "by_status": by_status,
        "decided": decided,
        "acceptance_rate": acceptance_rate,
        "acceptance_note": (
            f"{accepted} approved or pushed out of {decided} decided."
            if decided else "No decisions recorded yet."
        ),
        "realized": {
            "measured": realized.get("n", 0),
            "forecast_revenue": round(realized.get("forecast", 0.0), 2),
            "realized_revenue": round(realized.get("realized", 0.0), 2),
            "mean_abs_error_pct": round(realized.get("mean_abs_error", 0.0), 2),
            "within_20pct": within_ci,
        },
    }


def baseline_comparison(run_id: str | None = None) -> dict:
    """AI recommendations against the rule-based pricer on identical data (FR-057).

    Both prices are evaluated over the *same* simulated distributions and the
    same shelf-price ladder, so the comparison is not flattered by giving either
    side a different price universe.
    """
    clause = "WHERE baseline_price IS NOT NULL"
    params: tuple = ()
    if run_id:
        clause += " AND run_id = ?"
        params = (run_id,)

    rows = _rows(
        f"SELECT sku, category, current_price, recommended_price, baseline_price,"
        f" expected_revenue_delta, delta_pct, confidence, band"
        f" FROM recommendations {clause}",
        params,
    )
    if not rows:
        return {"skus": 0, "detail": "No recommendations carry a baseline price yet."}

    agree = sum(1 for r in rows if abs(r["recommended_price"] - r["baseline_price"]) < 0.005)
    ai_higher = sum(1 for r in rows if r["recommended_price"] > r["baseline_price"] + 0.005)
    ai_lower = sum(1 for r in rows if r["recommended_price"] < r["baseline_price"] - 0.005)

    ai_revenue = sum(r["expected_revenue_delta"] or 0.0 for r in rows)
    current_revenue_base = sum(
        abs(r["expected_revenue_delta"] or 0.0) for r in rows
    ) or 1.0

    by_category: dict[str, dict] = {}
    for r in rows:
        bucket = by_category.setdefault(
            r["category"] or "unknown",
            {"skus": 0, "ai_revenue_delta": 0.0, "mean_ai_price": 0.0,
             "mean_baseline_price": 0.0},
        )
        bucket["skus"] += 1
        bucket["ai_revenue_delta"] += r["expected_revenue_delta"] or 0.0
        bucket["mean_ai_price"] += r["recommended_price"]
        bucket["mean_baseline_price"] += r["baseline_price"]
    for bucket in by_category.values():
        n = max(bucket["skus"], 1)
        bucket["ai_revenue_delta"] = round(bucket["ai_revenue_delta"], 2)
        bucket["mean_ai_price"] = round(bucket["mean_ai_price"] / n, 2)
        bucket["mean_baseline_price"] = round(bucket["mean_baseline_price"] / n, 2)

    return {
        "skus": len(rows),
        "agreement": {
            "same_price": agree,
            "ai_higher": ai_higher,
            "ai_lower": ai_lower,
            "agreement_rate": round(agree / len(rows), 4),
        },
        "ai_forecast_revenue_delta": round(ai_revenue, 2),
        "ai_uplift_pct": round(ai_revenue / current_revenue_base * 100.0, 2),
        "by_category": by_category,
        "note": (
            "The baseline emits the same unqualified number regardless of how "
            "much evidence supports it. Every AI price here carries a confidence "
            "score and an interval — that difference is what makes bounded "
            "autonomy possible at all."
        ),
    }


def stability() -> dict:
    """Oscillation, damping, and the convergence trajectory (FR-112)."""
    totals = _one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(oscillating), 0) AS oscillating,"
        " COALESCE(SUM(damped), 0) AS damped FROM recommendations"
    )
    n = totals.get("n", 0) or 0
    trajectory = _rows(
        "SELECT o.measured_at, o.sku, o.forecast_error_pct FROM outcomes o"
        " ORDER BY o.measured_at ASC LIMIT 500"
    )
    refinements = _rows(
        "SELECT sku, elasticity, ci_low, ci_high, refinement_count,"
        " total_adjustment, updated_at FROM elasticity_estimates"
        " ORDER BY ABS(total_adjustment) DESC LIMIT 25"
    )
    capped = _one(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event_type = 'elasticity_refined'"
        " AND detail_json LIKE '%\"capped\": true%'"
    )["n"]

    return {
        "recommendations": n,
        "oscillating": totals.get("oscillating", 0),
        "oscillation_rate": round(totals.get("oscillating", 0) / n, 4) if n else 0.0,
        "damped": totals.get("damped", 0),
        "convergence": feedback.convergence(),
        "error_trajectory": [
            {"measured_at": r["measured_at"], "sku": r["sku"],
             "error_pct": r["forecast_error_pct"]}
            for r in trajectory
        ],
        "refinements": refinements,
        "capped_adjustments": capped,
        "capped_note": (
            "Adjustments hitting the cap are bounded, not discarded — the loop "
            "learns slowly rather than being allowed to lurch (FR-111)."
        ),
    }


def autonomy() -> dict:
    """Band distribution, auto-approve rate, escalation reasons, mode history."""
    per_run = _rows(
        "SELECT r.run_id, r.band, COUNT(*) AS n, MIN(runs.started_at) AS started_at"
        " FROM recommendations r JOIN runs ON runs.run_id = r.run_id"
        " GROUP BY r.run_id, r.band ORDER BY started_at"
    )
    runs: dict[str, dict] = {}
    for row in per_run:
        entry = runs.setdefault(
            row["run_id"],
            {"run_id": row["run_id"], "started_at": row["started_at"],
             "auto_approve": 0, "review": 0, "escalate": 0},
        )
        entry[row["band"]] = row["n"]

    totals = {
        r["band"]: r["n"] for r in _rows(
            "SELECT band, COUNT(*) AS n FROM recommendations GROUP BY band"
        )
    }
    total = sum(totals.values()) or 0

    system_approved = _one(
        "SELECT COUNT(DISTINCT rec_id) AS n FROM approvals WHERE actor = 'system'"
    )["n"]
    human_approved = _one(
        "SELECT COUNT(DISTINCT rec_id) AS n FROM approvals WHERE actor <> 'system'"
    )["n"]

    escalations = _rows(
        "SELECT band_reason, COUNT(*) AS n FROM recommendations"
        " WHERE band = 'escalate' GROUP BY band_reason ORDER BY n DESC LIMIT 12"
    )
    mode_changes = [
        {**r, "detail": json.loads(r.pop("detail_json") or "{}")}
        for r in _rows(
            "SELECT ts, actor, event_type, detail_json FROM audit_log"
            " WHERE event_type IN ('mode_changed', 'kill_switch')"
            " ORDER BY ts DESC LIMIT 50"
        )
    ]

    return {
        "band_totals": totals,
        "band_shares": {
            k: round(v / total, 4) for k, v in totals.items()
        } if total else {},
        "per_run": list(runs.values())[-30:],
        "auto_approved": system_approved,
        "human_approved": human_approved,
        "auto_approve_rate": (
            round(system_approved / (system_approved + human_approved), 4)
            if (system_approved + human_approved) else None
        ),
        "escalation_reasons": [
            {"reason": _shorten(r["band_reason"]), "count": r["n"]} for r in escalations
        ],
        "mode_history": mode_changes,
    }


def _shorten(reason: str | None, limit: int = 90) -> str:
    text = (reason or "unspecified").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_history(limit: int = 50) -> list[dict]:
    """Run list with status, duration, token cost, outcome (FR-061)."""
    return _rows(
        "SELECT r.run_id, r.started_at, r.completed_at, r.status, r.trigger,"
        " r.scope_kind, r.scope_value, r.objective, r.mode, r.sku_count,"
        " r.duration_ms, r.tokens_in, r.tokens_out, r.cost_usd, r.error,"
        " (SELECT COUNT(*) FROM recommendations c WHERE c.run_id = r.run_id"
        "   AND c.band = 'escalate') AS escalated"
        " FROM runs r ORDER BY r.started_at DESC LIMIT ?",
        (limit,),
    )
