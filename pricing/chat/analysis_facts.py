"""Facts about analysis this platform has already produced.

This is the half of the assistant that makes a completed run interrogable. A run
console shows what happened; an analyst wants to ask why this SKU escalated,
what is still waiting on them, and whether the last forecast turned out to be
right. All three are already recorded in app.db — the work here is selecting the
right slice, not computing anything new.

Nothing in this module decides or changes anything. Approving a price is an
operator action with an audit trail, and it stays that way.
"""

from __future__ import annotations

import re

from pricing.chat.facts import FactPack, money
from pricing.core.logging import get_logger
from pricing.db.app_db import session
from pricing.llm.schemas import ChatRoute
from pricing.pipeline import persistence
from pricing.services import feedback, metrics

logger = get_logger("pricing.chat.analysis")

REC_ID = re.compile(r"\brec-[0-9a-f]{6,16}\b", re.IGNORECASE)
RUN_ID = re.compile(r"\brun-[0-9a-f]{6,16}\b", re.IGNORECASE)
_QUEUE = re.compile(
    r"\b(queue|pending|waiting|review|approve|to do|outstanding|escalat)\w*\b",
    re.IGNORECASE,
)
_ACCURACY = re.compile(
    r"\b(accura\w*|error|realized|realised|outcome|baseline|versus the rule|"
    r"how well|convergen\w*|learn\w*|drift)\b",
    re.IGNORECASE,
)


def _run_lines(run: dict) -> list[str]:
    counts = {}
    with session() as conn:
        for row in conn.execute(
            "SELECT band, COUNT(*) AS n FROM recommendations WHERE run_id = ?"
            " GROUP BY band", (run["run_id"],),
        ):
            counts[row["band"]] = row["n"]
    lines = [
        f"Run {run['run_id']} ({run['trigger']}-triggered) covered "
        f"{run['scope_kind']} {run['scope_value'] or ''} on the {run['objective']} "
        f"objective in {run['mode']} mode; status {run['status']}, "
        f"{run['sku_count']} SKUs, {(run['duration_ms'] or 0) / 1000:.1f}s.",
        f"Run {run['run_id']} bands: "
        + ", ".join(f"{band} {n}" for band, n in counts.items())
        + ". Auto-approve is the only band that can move without a human, and only "
        "outside supervised mode.",
        f"Run {run['run_id']} cost {run['tokens_in'] or 0:,} in / "
        f"{run['tokens_out'] or 0:,} out tokens, ${run['cost_usd'] or 0:.4f}.",
    ]
    if run.get("error"):
        lines.append(f"Run {run['run_id']} recorded errors: {run['error']}")
    return lines


def _movers(run_id: str, limit: int = 5) -> list[str]:
    rows = persistence.list_recommendations(run_id=run_id, limit=limit)
    return [
        f"{r['sku']} ({r['product_name']}): {money(r['current_price'])} to "
        f"{money(r['recommended_price'])} ({r['delta_pct']:+.1f}%), expected revenue "
        f"{money(r['expected_revenue_delta'])}, confidence {r['confidence']:.2f}, "
        f"band {r['band']} — {r['band_reason']}"
        for r in rows
    ]


def _recommendation_lines(rec: dict) -> list[str]:
    lines = [
        f"{rec['rec_id']} for {rec['sku']} ({rec['product_name']}, "
        f"{rec['category']}) in run {rec['run_id']}: {money(rec['current_price'])} to "
        f"{money(rec['recommended_price'])} ({rec['delta_pct']:+.1f}%), status "
        f"{rec['status']}.",
        f"{rec['sku']} band {rec['band']} because: {rec['band_reason']}",
        f"{rec['sku']} confidence {rec['confidence']:.2f}; expected revenue "
        f"{money(rec['expected_revenue_delta'])} (90% CI "
        f"{money(rec['revenue_ci_low'])} to {money(rec['revenue_ci_high'])}); "
        f"expected margin {money(rec['expected_margin_delta'])}.",
        f"{rec['sku']} compliance {rec['compliance_status']}; "
        f"{'damped' if rec['damped'] else 'not damped'}; "
        f"{'flagged oscillating' if rec['oscillating'] else 'no oscillation flagged'}.",
    ]
    if rec.get("elasticity") is not None:
        lines.append(
            f"{rec['sku']} elasticity {rec['elasticity']:.2f} (95% CI "
            f"{rec['elasticity_ci_low']:.2f} to {rec['elasticity_ci_high']:.2f}, n="
            f"{rec['elasticity_samples']})."
        )
    failed = [e for e in rec.get("compliance_evals", []) if not e["passed"]]
    for check in failed:
        lines.append(
            f"{rec['sku']} failed {check['rule_code']}: actual "
            f"{check['actual_value']}, threshold {check['threshold']} — "
            f"{check['detail']}"
        )
    for approval in rec.get("approvals", []):
        lines.append(
            f"{rec['sku']} {approval['action']} by {approval['actor']} in "
            f"{approval['mode']} mode at {approval['created_at']}: "
            f"{approval['reason'] or 'no reason given'}."
        )
    if rec.get("rationale"):
        lines.append(f"Recorded rationale for {rec['sku']}: {rec['rationale']}")
    return lines


def _queue_lines() -> list[str]:
    with session() as conn:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT band, status, COUNT(*) AS n FROM recommendations"
                " GROUP BY band, status"
            )
        ]
        top = [
            dict(r) for r in conn.execute(
                "SELECT sku, rec_id, band, delta_pct, expected_revenue_delta,"
                " band_reason FROM recommendations WHERE status = 'pending'"
                " AND band IN ('review','escalate')"
                " ORDER BY ABS(expected_revenue_delta) DESC LIMIT 5"
            )
        ]
    pending = {r["band"]: r["n"] for r in rows if r["status"] == "pending"}
    lines = [
        "Pending by band: "
        + (", ".join(f"{band} {n}" for band, n in pending.items()) or "nothing pending")
        + ".",
        "All statuses: "
        + ", ".join(f"{r['band']}/{r['status']} {r['n']}" for r in rows)
        + ".",
    ]
    lines.extend(
        f"Waiting: {r['sku']} ({r['rec_id']}) {r['delta_pct']:+.1f}%, expected revenue "
        f"{money(r['expected_revenue_delta'])}, band {r['band']} — {r['band_reason']}"
        for r in top
    )
    return lines


def _accuracy_lines() -> list[str]:
    performance = metrics.performance()
    baseline = metrics.baseline_comparison()
    convergence = feedback.convergence()
    realized = performance["realized"]
    lines = [
        f"Across every run: {performance['recommendations']} recommendations, "
        f"{performance['price_changes_proposed']} of them price changes, mean "
        f"confidence {performance['mean_confidence']:.2f}, forecast revenue effect "
        f"{money(performance['forecast_revenue_delta'])}.",
        f"Acceptance: {performance['acceptance_note']}",
        (
            f"Realized outcomes: {realized['measured']} measured, forecast "
            f"{money(realized['forecast_revenue'])} against realized "
            f"{money(realized['realized_revenue'])}, mean absolute forecast error "
            f"{realized['mean_abs_error_pct']:.1f}%, {realized['within_20pct']} within "
            "20%."
            if realized["measured"]
            else "No outcomes have been measured yet — push prices and run a readback "
                 "to compare forecast against realized."
        ),
    ]
    if baseline.get("skus"):
        agreement = baseline["agreement"]
        lines.append(
            f"Against the rule-based baseline on {baseline['skus']} SKUs: identical "
            f"price on {agreement['same_price']}, AI higher on "
            f"{agreement['ai_higher']}, AI lower on {agreement['ai_lower']} "
            f"(agreement {agreement['agreement_rate']:.0%})."
        )
    if convergence.get("samples"):
        lines.append(
            f"Convergence over {convergence['samples']} measurements: mean absolute "
            f"error {convergence['mean_abs_error_pct']:.1f}%, "
            f"{'narrowing' if convergence['converging'] else 'not yet narrowing'} — "
            f"{convergence['detail']}"
        )
    return lines


def build(route: ChatRoute, question: str, context: dict | None = None) -> FactPack:
    """Facts drawn from the run record for questions about our own analysis."""
    context = context or {}
    text = f"{question} {route.restated}"
    rec_match = REC_ID.search(text) or REC_ID.search(str(context.get("rec_id") or ""))
    run_match = RUN_ID.search(text)
    run_id = run_match.group(0).lower() if run_match else context.get("run_id")

    lines: list[str] = []
    sources = ["Platform run record (app.db)"]

    if rec_match:
        rec = persistence.get_recommendation(rec_match.group(0).lower())
        if rec is None:
            return FactPack(shortfall=f"No recommendation '{rec_match.group(0)}' exists.")
        lines.extend(_recommendation_lines(rec))
        run_id = run_id or rec["run_id"]
    elif route.skus:
        wanted = set(route.skus)
        recent = [
            r for r in persistence.list_recommendations(limit=2000)
            if r["sku"] in wanted
        ][:4]
        for row in recent:
            detail = persistence.get_recommendation(row["rec_id"]) or row
            lines.extend(_recommendation_lines(detail))
        if not recent:
            lines.append(
                f"No run has produced a recommendation for {', '.join(route.skus)} yet."
            )

    run = persistence.get_run(run_id) if run_id else None
    if run is None:
        runs = persistence.list_runs(limit=1)
        run = runs[0] if runs else None
    if run:
        lines.extend(_run_lines(run))
        if not rec_match:
            movers = _movers(run["run_id"])
            if movers:
                lines.append("Largest expected revenue movers in that run:")
                lines.extend(f"  {m}" for m in movers)
    elif not lines:
        return FactPack(
            shortfall=(
                "No pricing run has been recorded yet, so there is no analysis to "
                "ask about. Start one from the Run Console — pick a category and an "
                "objective — and I can explain whatever it produces."
            )
        )

    if _QUEUE.search(text):
        lines.extend(_queue_lines())
    if _ACCURACY.search(text) or not route.skus:
        lines.extend(_accuracy_lines())

    headline = f"Analysis on record — run {run['run_id']}." if run else "Analysis on record."
    return FactPack(
        headline=headline,
        lines=lines,
        data={"run_id": run["run_id"] if run else None},
        sources=sources,
        grounding_query=(
            f"pricing policy compliance escalation approval {route.category} "
            f"{' '.join(route.skus)}"
        ),
        collections=["pricing_policy", "market_intel"],
    )
