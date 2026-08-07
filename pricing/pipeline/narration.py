"""LLM narration layer for agents 1, 3 and 4.

This is where the language model earns its place: turning a vector of numbers
into something a pricing manager can act on and a compliance officer can audit.

What it explicitly does **not** do: decide a price, decide compliance, or decide
a band. Those are computed in `pricing.analytics` and `pricing.rules` before any
model is consulted. If every call here fails, the run still produces the same
prices with deterministic explanations â€” narration is additive, never
load-bearing.
"""

from __future__ import annotations

from pricing.core.logging import get_logger
from pricing.llm import grounded
from pricing.llm.schemas import (
    DataQualityNarration,
    PricingRationale,
    RunNarrative,
    ViolationExplanation,
)
from pricing.pipeline.state import RunState, SkuAnalysis

logger = get_logger("pricing.pipeline.narration")

# Narrating 500 SKUs individually would be slow and expensive for little gain â€”
# most recommendations are unremarkable. Only the ones a human will actually
# read get a model call.
MAX_NARRATED_SKUS = 25


def _account(state: RunState, result) -> None:
    """Bank one call's tokens and cost onto the run (FR-065, NFR-029).

    A single helper rather than three lines repeated at every call site: the
    cost half was previously missing everywhere, which is exactly the failure
    mode duplicated accounting invites.
    """
    state.tokens_in += result.tokens_in
    state.tokens_out += result.tokens_out
    state.cost_usd = round(state.cost_usd + result.cost_usd, 6)


def _facts(a: SkuAnalysis) -> str:
    """Pack the computed evidence into a compact factual brief.

    The model is given conclusions to explain, not data to analyse. Asking it to
    re-derive the numbers would invite it to contradict the optimizer.
    """
    p, est, opt, ctx = a.context.product, a.elasticity, a.optimization, a.context
    lines = [
        f"SKU: {a.sku} ({p.get('name')}), category {p.get('category')}",
        f"Current price: {a.current_price:.2f}; unit cost {p.get('unit_cost'):.2f}",
        f"Recommended price: {a.recommended_price:.2f} ({a.delta_pct:+.1f}%)",
    ]
    if est and est.usable:
        lines.append(
            f"Estimated elasticity {est.elasticity:.2f} (95% CI {est.ci_low:.2f} to "
            f"{est.ci_high:.2f}, n={est.sample_size}, R2={est.r_squared:.2f})"
        )
    else:
        lines.append("Elasticity could not be reliably estimated for this SKU.")
    if opt and opt.outcome:
        o = opt.outcome
        lines.append(
            f"Simulated expected revenue {o.expected_revenue:.0f} "
            f"(90% band {o.revenue_p5:.0f}-{o.revenue_p95:.0f}); change vs today "
            f"{opt.expected_revenue_delta:+.0f}; revenue variance {o.revenue_cv:.2f}; "
            f"P(margin below floor) {o.prob_below_margin_floor:.0%}"
        )
    if ctx.competitive:
        c = ctx.competitive
        lines.append(
            f"Competitors: cheapest {c.min_competitor_price:.2f}, mean "
            f"{c.mean_competitor_price:.2f}, our gap {c.gap_to_cheapest_pct:+.1f}%"
        )
    if ctx.cover_days is not None:
        lines.append(f"Stock cover: {ctx.cover_days:.0f} days")
    if a.baseline_price is not None:
        lines.append(f"Conventional rule-based pricer would say: {a.baseline_price:.2f}")
    if a.band:
        lines.append(f"Autonomy band: {a.band.band.value} â€” {a.band.reason}")
    return "\n".join(lines)


def narrate_quality(state: RunState) -> None:
    """Agent 1 â€” anomaly narration (FR-010)."""
    if state.quality is None or not grounded.available():
        return
    issues = [
        f"[{c.severity.value}] {c.code}: {c.message}"
        for c in state.quality.checks if c.severity.value != "pass"
    ]
    if not issues:
        return

    result = grounded.call(
        role="narrator",
        system=(
            "You are a retail data steward. Explain data quality findings to a "
            "pricing manager in plain business language. Be specific and brief. "
            "Do not speculate beyond the findings given."
        ),
        user=(
            f"Run scope: {state.scope.describe()}. Overall verdict: "
            f"{state.quality.verdict.value}.\nFindings:\n" + "\n".join(issues)
        ),
        schema=DataQualityNarration,
        collections=["pricing_policy", "market_intel"],
        run_id=state.run_id,
    )
    if result.ok and result.data:
        state.narrative["data_quality"] = result.data.headline
        _account(state, result)


def narrate_recommendations(state: RunState) -> None:
    """Agent 3 â€” grounded rationale for the SKUs a human will actually read."""
    if not grounded.available():
        logger.info("narration.skipped", reason="gateway unavailable")
        return

    ranked = sorted(
        state.priced,
        key=lambda a: abs(a.optimization.expected_revenue_delta) if a.optimization else 0,
        reverse=True,
    )[:MAX_NARRATED_SKUS]

    for a in ranked:
        if a.optimization is None:
            continue
        result = grounded.call(
            role="strategist",
            system=(
                "You are a retail pricing strategist. Explain a price "
                "recommendation that has already been computed. Do NOT propose a "
                "different price and do NOT recompute anything â€” your job is to "
                "explain the decision that was made, honestly, including its "
                "uncertainty. Cite the source ids you rely on."
            ),
            user=_facts(a),
            schema=PricingRationale,
            grounding_query=(
                f"pricing policy {a.context.product.get('category')} "
                f"{a.context.product.get('brand')} margin competitive positioning"
            ),
            run_id=state.run_id,
        )
        if result.ok and result.data:
            data = result.data
            a.rationale = data.rationale
            # Keep only citations that map to context actually retrieved for
            # this call â€” a model-invented id is not evidence (FR-023).
            valid = {c["id"] for c in result.citations}
            a.citations = [c for c in result.citations if c["id"] in valid and
                           (not data.citations or c["id"] in data.citations)] \
                or result.citations
            _account(state, result)


def explain_violations(state: RunState) -> None:
    """Agent 4 â€” natural-language explanation of a blocked recommendation.

    The verdict was already decided deterministically; this only puts it into
    words (FR-034).
    """
    if not grounded.available():
        return
    blocked = [
        a for a in state.priced
        if a.compliance and not a.compliance.passed
    ][:10]
    for a in blocked:
        result = grounded.call(
            role="strategist",
            system=(
                "You are a retail pricing compliance analyst. A rule engine has "
                "already blocked this price. Explain why it matters commercially "
                "and what could be done instead. Do not dispute the verdict."
            ),
            user=f"{_facts(a)}\n\nRule violations:\n{a.compliance.summary}",
            schema=ViolationExplanation,
            collections=["pricing_policy"],
            run_id=state.run_id,
        )
        if result.ok and result.data:
            a.rationale = f"{result.data.summary} {result.data.suggested_action}".strip()
            _account(state, result)


def narrate_run(state: RunState) -> None:
    """Run-level summary for the dashboard."""
    if not grounded.available() or not state.priced:
        return
    bands = state.band_counts()
    movers = [a for a in state.priced if abs(a.delta_pct) > 0.01]
    total_rev = sum(
        a.optimization.expected_revenue_delta for a in state.priced if a.optimization
    )
    result = grounded.call(
        role="analyst",
        system=(
            "You are summarising a completed pricing run for a pricing manager. "
            "Be factual and brief. Flag risks honestly rather than reassuringly."
        ),
        user=(
            f"Scope: {state.scope.describe()}. Objective: {state.objective.value}. "
            f"Mode: {state.mode.value}.\n"
            f"{len(state.priced)} SKUs priced, {len(movers)} price changes proposed.\n"
            f"Bands: {bands}.\n"
            f"Total expected revenue change: {total_rev:+.0f}.\n"
            f"Data quality verdict: "
            f"{state.quality.verdict.value if state.quality else 'unknown'}."
        ),
        schema=RunNarrative,
    run_id=state.run_id,
    )
    if result.ok and result.data:
        state.narrative["run_summary"] = result.data.summary
        state.narrative["headline"] = result.data.headline
        _account(state, result)
