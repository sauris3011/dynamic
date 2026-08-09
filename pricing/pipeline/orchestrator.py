"""The five-stage pricing pipeline (PRD 4.2, W1).

Stages run with deterministic edges — no LLM decides control flow. Each stage is
a plain function over `RunState`, which keeps the pipeline runnable standalone
and under LangGraph without two implementations of the logic.
"""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np

from pricing.analytics import baseline as baseline_mod
from pricing.analytics import quality
from pricing.analytics.elasticity import estimate_from_records
from pricing.analytics.montecarlo import simulate
from pricing.analytics.optimizer import PriceConstraints, candidate_grid, optimize
from pricing.analytics.stability import detect_oscillation
from pricing.clients.commerce import CommerceClient, CommerceUnavailable
from pricing.config import get_settings
from pricing.core import telemetry
from pricing.core.logging import get_logger
from pricing.feeds.competitor import SyntheticCompetitorFeed, build_positions
from pricing.pipeline import persistence, progress, rationale
from pricing.pipeline.state import RunState, SkuAnalysis, SkuContext
from pricing.services import feedback
from pricing.rules.bands import BandThresholds, assign_band
from pricing.rules.engine import RuleConfig, evaluate

logger = get_logger("pricing.pipeline")

RECENT_DEMAND_DAYS = 28
DETAIL = progress.STAGE_DETAIL


# =====================================================================
# Agent 1 — Data & Context
# =====================================================================
def stage_data_context(state: RunState, client: CommerceClient) -> RunState:
    """Concurrent ingestion, then the quality gate (FR-003 .. FR-011, FR-077).

    The four fetches are independent and IO-bound, so they run together rather
    than in sequence. This is where the parallelism from the seven-agent design
    was recovered after collapsing to five agents (PRD 4.2).
    """
    t0 = time.time()
    scope = state.scope
    category = scope.value if scope.kind == "category" else None
    progress.stage(state.run_id, "data_context", detail=DETAIL["data_context"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        f_products = pool.submit(client.products, category=category)
        f_sales = pool.submit(client.sales, category=category)
        f_inventory = pool.submit(client.inventory, category=category)
        f_prices = pool.submit(client.prices, category=category)

        products = f_products.result()
        sales = f_sales.result()
        inventory = f_inventory.result()
        prices = f_prices.result()

    if scope.kind == "skus" and scope.skus:
        wanted = set(scope.skus)
        products = [p for p in products if p["sku"] in wanted]
        sales = [s for s in sales if s["sku"] in wanted]
        inventory = [i for i in inventory if i["sku"] in wanted]

    # --- Quality gate. A FAIL here halts the run (FR-011). ---------------
    progress.item(state.run_id, "data_context", 1, 2,
                  detail=f"Checking {len(products)} products for usable data.")
    report = quality.assess(products, sales, inventory)
    state.quality = report
    if report.failed:
        state.status = "halted"
        state.errors.extend(report.blocking_reasons)
        state.stage_timings["data_context"] = time.time() - t0
        logger.warning(
            "pipeline.halted", run_id=state.run_id, reasons=report.blocking_reasons
        )
        return state

    # --- Third-party competitor feed (separate tier) ---------------------
    our_prices = {p["sku"]: p["current_price"] for p in products}
    feed = SyntheticCompetitorFeed(our_prices)
    positions = build_positions(feed.fetch(list(our_prices), on=date.today()), our_prices)

    sales_by_sku: dict[str, list[dict]] = defaultdict(list)
    for row in sales:
        sales_by_sku[row["sku"]].append(row)
    inv_by_sku = {i["sku"]: i for i in inventory}
    price_by_sku = {p["sku"]: p for p in prices}
    # Observed price series across previous runs — without this, oscillation
    # detection has nothing to look at and silently reports every SKU stable.
    history_by_sku = persistence.price_history_map()

    family_map: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for p in products:
        family_map[p["family_id"]].append((p["size_value"], p["current_price"], p["sku"]))

    for product in products:
        sku = product["sku"]
        records = sales_by_sku.get(sku, [])
        recent = records[-RECENT_DEMAND_DAYS:] if records else []
        base_demand = float(np.mean([r["units"] for r in recent])) if recent else 0.0
        inv = inv_by_sku.get(sku)

        # Live price from the price book wins over the catalog snapshot.
        if sku in price_by_sku:
            product = {**product, "current_price": price_by_sku[sku]["current_price"]}

        ctx = SkuContext(
            sku=sku,
            product=product,
            sales=records,
            inventory=inv,
            competitive=positions.get(sku),
            base_demand=base_demand,
            cover_days=inv["cover_days"] if inv else None,
            price_history=[product["current_price"], *history_by_sku.get(sku, [])],
            family_prices=[
                (sz, pr) for sz, pr, other in family_map[product["family_id"]]
                if other != sku
            ],
        )
        analysis = SkuAnalysis(sku=sku, context=ctx)
        if base_demand <= 0:
            analysis.skipped = "No recent sales; cannot establish base demand."
        state.analyses.append(analysis)

    state.stage_timings["data_context"] = time.time() - t0
    logger.info(
        "pipeline.data_context",
        run_id=state.run_id, products=len(products), sales=len(sales),
        verdict=report.verdict.value, seconds=round(time.time() - t0, 2),
    )
    return state


# =====================================================================
# Agent 2 — Quantitative
# =====================================================================
def stage_quantitative(state: RunState) -> RunState:
    """Elasticity (causal) then Monte Carlo (uncertainty) — in that order.

    FR-081: the simulation consumes the elasticity estimate. It never samples
    historical sales variance as a substitute; that would produce a confident
    distribution of the wrong quantity (PRD 4.7).
    """
    t0 = time.time()
    s = get_settings()

    # Dispersion refined from accumulated forecast error (FR-108). Computed once
    # per run: it is a property of the system's track record, not of one SKU.
    uncertainty = feedback.refined_uncertainty()

    priced = state.priced
    progress.stage(state.run_id, "quantitative", total=len(priced),
                   detail=DETAIL["quantitative"])

    for done, analysis in enumerate(priced, start=1):
        ctx = analysis.context
        est = _refined_estimate(estimate_from_records(analysis.sku, ctx.sales))
        analysis.elasticity = est
        # A SKU whose forecasts have repeatedly missed carries less confidence
        # than the regression alone would suggest (FR-109).
        analysis.confidence = round(
            est.confidence * feedback.confidence_penalty(analysis.sku), 4
        )

        stab = detect_oscillation(
            analysis.sku,
            ctx.price_history or [ctx.product["current_price"]],
            window=s.oscillation_window,
            max_reversals=s.oscillation_max_reversals,
            damping_factor=s.damping_factor,
        )
        analysis.stability = stab

        grid = candidate_grid(
            ctx.product["current_price"],
            s.band_max_delta_pct,
            damping_factor=stab.damping_factor,
        )
        analysis.simulation = simulate(
            sku=analysis.sku,
            candidate_prices=grid,
            current_price=ctx.product["current_price"],
            unit_cost=ctx.product["unit_cost"],
            base_demand=ctx.base_demand,
            elasticity=est,
            iterations=s.mc_iterations,
            seed=s.mc_seed,
            inputs=uncertainty,
        )
        progress.item(state.run_id, "quantitative", done, len(priced), detail=analysis.sku)

    state.stage_timings["quantitative"] = time.time() - t0
    logger.info(
        "pipeline.quantitative",
        run_id=state.run_id, skus=len(state.priced),
        seconds=round(time.time() - t0, 2),
    )
    return state


def _refined_estimate(est):
    """Overlay any refinement the feedback loop has learned for this SKU (FR-107).

    The regression over static history is the prior; realized outcomes have
    already been folded into it by `services.feedback`. Reading the stored belief
    here — rather than re-deriving it — is what makes the loop closed: the next
    run genuinely starts from what the last one learned.
    """
    stored = feedback.get_estimate(est.sku)
    if not stored or not est.usable:
        return est
    est.elasticity = float(stored["elasticity"])
    est.ci_low = float(stored["ci_low"])
    est.ci_high = float(stored["ci_high"])
    est.std_error = max(abs(est.ci_high - est.ci_low) / 3.92, 1e-4)
    est.warnings = [
        *est.warnings,
        f"Elasticity refined by {stored['refinement_count']} realized outcome(s) "
        f"(cumulative adjustment {stored['total_adjustment']:+.3f}).",
    ]
    return est


# =====================================================================
# Agent 3 — Strategy & Reasoning
# =====================================================================
def stage_strategy(state: RunState) -> RunState:
    """Constrained optimization over the simulated distributions, plus the
    rule-based baseline for comparison (FR-017 .. FR-021, FR-057)."""
    t0 = time.time()
    s = get_settings()

    priced = state.priced
    progress.stage(state.run_id, "strategy", total=len(priced), detail=DETAIL["strategy"])

    for done, analysis in enumerate(priced, start=1):
        progress.item(state.run_id, "strategy", done, len(priced), detail=analysis.sku)
        ctx, sim = analysis.context, analysis.simulation
        if sim is None:
            analysis.skipped = "Simulation unavailable."
            continue

        product = ctx.product
        constraints = PriceConstraints(
            margin_floor_pct=15.0,
            max_change_pct=s.band_max_delta_pct,
            map_price=product.get("map_price"),
            damping_factor=analysis.stability.damping_factor if analysis.stability else 1.0,
        )
        analysis.optimization = optimize(
            sim, product["unit_cost"], constraints, state.objective
        )

        pos = ctx.competitive
        bl = baseline_mod.price_one(
            sku=analysis.sku,
            category=product["category"],
            unit_cost=product["unit_cost"],
            current_price=product["current_price"],
            cover_days=ctx.cover_days,
            min_competitor_price=pos.min_competitor_price if pos else None,
            map_price=product.get("map_price"),
        )
        analysis.baseline_price = bl.baseline_price
        analysis.rationale = rationale.build(analysis)

    state.stage_timings["strategy"] = time.time() - t0
    return state


# =====================================================================
# Agent 4 — Validation & Compliance  (holds the veto)
# =====================================================================
def stage_validation(state: RunState) -> RunState:
    """Compliance rules, stability, and band assignment (FR-030 .. FR-035,
    FR-089 .. FR-104). No LLM participates in any decision here."""
    t0 = time.time()
    s = get_settings()
    rule_cfg = RuleConfig(margin_floor_pct=15.0, max_change_pct=s.band_max_delta_pct)
    thresholds = BandThresholds(
        min_confidence=s.band_min_confidence,
        max_delta_pct=s.band_max_delta_pct,
        max_variance=s.band_max_variance,
        margin_buffer_pct=s.band_margin_buffer_pct,
    )
    quality_warned = bool(state.quality and state.quality.verdict.value == "warn")

    priced = state.priced
    progress.stage(state.run_id, "validation", total=len(priced), detail=DETAIL["validation"])

    for done, analysis in enumerate(priced, start=1):
        progress.item(state.run_id, "validation", done, len(priced), detail=analysis.sku)
        ctx, opt = analysis.context, analysis.optimization
        if opt is None:
            continue
        product = ctx.product
        price = opt.recommended_price

        analysis.compliance = evaluate(
            sku=analysis.sku,
            proposed_price=price,
            current_price=product["current_price"],
            unit_cost=product["unit_cost"],
            category=product["category"],
            map_price=product.get("map_price"),
            family_prices=ctx.family_prices,
            size_value=product.get("size_value"),
            config=rule_cfg,
        )

        outcome = opt.outcome
        margin_pct = (price - product["unit_cost"]) / price * 100.0 if price > 0 else -100.0
        est = analysis.elasticity

        analysis.band = assign_band(
            sku=analysis.sku,
            compliance_passed=analysis.compliance.passed,
            compliance_summary=analysis.compliance.summary,
            confidence=analysis.confidence,
            delta_pct=analysis.delta_pct,
            revenue_cv=outcome.revenue_cv if outcome else 9.99,
            margin_pct=margin_pct,
            elasticity_samples=est.sample_size if est else 0,
            elasticity_usable=bool(est and est.usable),
            oscillating=bool(analysis.stability and analysis.stability.oscillating),
            quality_warned=quality_warned,
            thresholds=thresholds,
        )

    state.stage_timings["validation"] = time.time() - t0
    logger.info(
        "pipeline.validation",
        run_id=state.run_id, bands=state.band_counts(),
        seconds=round(time.time() - t0, 2),
    )
    return state


# =====================================================================
# Pipeline driver
# =====================================================================
def run_pipeline(state: RunState) -> RunState:
    """Execute stages 1-4. Persistence and push (Agent 5) are handled by the
    caller so a run can be inspected before anything reaches commerce."""
    t0 = time.time()
    try:
        with CommerceClient() as client:
            client.require_healthy()          # FR-076
            state = stage_data_context(state, client)
            if state.halted:
                return state
            state = stage_quantitative(state)
            state = stage_strategy(state)
            state = stage_validation(state)

        # Narration is additive and must never fail a run: the prices are
        # already decided by this point. If the gateway is down the
        # deterministic rationales from stage_strategy stand as-is.
        try:
            from pricing.pipeline import narration

            t_narrate = time.time()
            narration.narrate_quality(state)
            narration.narrate_recommendations(state)
            narration.explain_violations(state)
            narration.narrate_run(state)
            state.stage_timings["narration"] = time.time() - t_narrate
        except Exception as exc:  # noqa: BLE001
            logger.warning("pipeline.narration_failed", run_id=state.run_id,
                           error=f"{type(exc).__name__}: {exc}")

        state.status = "completed"
        for stage, seconds in state.stage_timings.items():
            telemetry.record_stage(
                run_id=state.run_id, stage=stage, seconds=seconds,
                skus=len(state.priced), mode=state.mode.value,
            )
    except CommerceUnavailable as exc:
        state.status = "failed"
        state.errors.append(str(exc))
        logger.error("pipeline.commerce_unavailable", run_id=state.run_id, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller and audited
        state.status = "failed"
        state.errors.append(f"{type(exc).__name__}: {exc}")
        logger.exception("pipeline.failed", run_id=state.run_id)

    state.completed_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    state.stage_timings["total"] = time.time() - t0
    return state
