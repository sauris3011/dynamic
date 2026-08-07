"""Deterministic rationale — the explanation that survives a gateway outage.

Every recommendation carries an explanation before any language model is
consulted. The LLM narration in `narration.py` replaces the *prose*, never the
substance: if the gateway is unreachable the prices are unchanged and this text
stands in the UI and the audit trail.

Deliberately factual rather than persuasive. It states what drove the decision —
the elasticity and its interval, the feasible set the optimizer chose from, the
competitive gap, the stock position — without dressing any of it up. A rationale
that argues rather than reports is harder to audit and easier to over-trust.
"""

from __future__ import annotations

from pricing.pipeline.state import SkuAnalysis

OVERSTOCK_COVER_DAYS = 90
STOCKOUT_COVER_DAYS = 10


def build(a: SkuAnalysis) -> str:
    """Compose the fallback rationale for one analysed SKU."""
    parts: list[str] = [
        _elasticity_sentence(a),
        *_decision_sentences(a),
        *_context_sentences(a),
    ]
    return " ".join(p for p in parts if p)


def _elasticity_sentence(a: SkuAnalysis) -> str:
    est = a.elasticity
    if est and est.usable:
        kind = "elastic" if est.elasticity < -1 else "inelastic"
        return (
            f"Demand is {kind} (elasticity {est.elasticity:.2f}, 95% CI "
            f"{est.ci_low:.2f} to {est.ci_high:.2f}, n={est.sample_size})."
        )
    return "No reliable elasticity could be estimated, so this rests on a wide prior."


def _decision_sentences(a: SkuAnalysis) -> list[str]:
    opt = a.optimization
    if opt is None:
        return []
    if opt.hold:
        return ["The current price is already optimal within constraints."]
    return [
        f"Moving to {opt.recommended_price:.2f} is the best of "
        f"{opt.feasible_count} feasible candidates on the "
        f"{opt.objective.value} objective, with expected revenue change "
        f"{opt.expected_revenue_delta:+.2f} "
        f"(90% band {opt.revenue_ci_low:+.2f} to {opt.revenue_ci_high:+.2f})."
    ]


def _context_sentences(a: SkuAnalysis) -> list[str]:
    ctx = a.context
    out: list[str] = []

    pos = ctx.competitive
    if pos:
        side = "above" if pos.gap_to_cheapest_pct > 0 else "below"
        out.append(
            f"We sit {abs(pos.gap_to_cheapest_pct):.1f}% {side} the cheapest tracked "
            f"competitor ({pos.min_competitor_price:.2f}); market index "
            f"{pos.market_index:.2f}."
        )

    if ctx.cover_days is not None:
        if ctx.cover_days > OVERSTOCK_COVER_DAYS:
            out.append(f"Stock cover is {ctx.cover_days:.0f} days — overstocked.")
        elif ctx.cover_days < STOCKOUT_COVER_DAYS:
            out.append(f"Stock cover is only {ctx.cover_days:.0f} days — stockout risk.")

    if a.stability and a.stability.oscillating:
        out.append(a.stability.detail)

    return out
