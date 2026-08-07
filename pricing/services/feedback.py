"""Closed feedback loop: readback, refinement, convergence (FR-055, FR-107 .. FR-111).

This is what separates a system that *reports* from one that *learns*. The
pipeline forecasts; commerce transacts; this module compares the two and folds
the difference back into the beliefs that produced the forecast.

The sequence, per pushed recommendation:

1. Read realized sales either side of the price change — the platform's only
   window onto what actually happened (FR-055).
2. Score the forecast against it and persist the error (FR-109).
3. Treat the observed price/demand move as evidence about elasticity and update
   the estimate, **bounded and logged** (FR-107, FR-111).
4. Re-derive Monte Carlo dispersion from accumulated error so intervals tighten
   as evidence accrues rather than staying at their conservative prior (FR-108).

Every write here is additive: nothing is overwritten in place without the prior
value being recorded, so a refinement that turns out badly can be traced and
reversed rather than merely regretted.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from pricing.analytics.montecarlo import UncertaintyInputs
from pricing.analytics.refinement import (
    Observation,
    dispersion_scale,
    posterior_interval,
    refine_elasticity,
)
from pricing.analytics.stability import assess_convergence
from pricing.clients.commerce import CommerceClient
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, now_iso, session

logger = get_logger("pricing.services.feedback")

# Days either side of the price change used to measure the response. Long enough
# to average out weekday shape, short enough that other factors have not moved.
WINDOW_DAYS = 14


def _daily(rows: list[dict]) -> tuple[float, float, float]:
    """(mean daily units, mean daily revenue, mean price) over a window."""
    if not rows:
        return 0.0, 0.0, 0.0
    days = len({str(r["sale_date"])[:10] for r in rows}) or 1
    units = sum(float(r.get("units", 0)) for r in rows)
    revenue = sum(float(r.get("revenue", 0.0)) for r in rows)
    price = revenue / units if units > 0 else 0.0
    return units / days, revenue / days, price


def _split(rows: list[dict], boundary: date) -> tuple[list[dict], list[dict]]:
    """Window sales either side of the price change.

    **Promotion days are excluded from the before-window**, and this is
    load-bearing rather than tidiness. A promotion cuts price *and* buys display
    space, so promoted days carry a visibility lift on top of the price
    response. Comparing a promo-contaminated baseline against a clean
    post-change window would attribute the lost display lift to our own price
    move, biasing every measured outcome downward and — worse — feeding that
    bias straight into the elasticity refinement. It is the same confound
    `analytics/elasticity.py` controls for with a promo dummy; the readback has
    to control for it too or the loop learns the wrong thing.
    """
    before_from = boundary - timedelta(days=WINDOW_DAYS)
    after_to = boundary + timedelta(days=WINDOW_DAYS)
    before, promo_before, after = [], [], []
    for r in rows:
        try:
            d = date.fromisoformat(str(r["sale_date"])[:10])
        except ValueError:
            continue
        if before_from <= d < boundary:
            (promo_before if r.get("on_promo") else before).append(r)
        elif boundary <= d <= after_to:
            after.append(r)

    # A SKU on promotion throughout the window would otherwise yield no
    # baseline at all. Fall back to the contaminated series rather than
    # discarding the observation — refinement caps limit the damage, and a
    # missing measurement teaches nothing.
    if not before:
        before = promo_before
    return before, after


def _pushed_recommendations(run_id: str | None, limit: int) -> list[dict]:
    clause = "AND r.run_id = ?" if run_id else ""
    params: list = [run_id] if run_id else []
    params.append(limit)
    with session() as conn:
        rows = conn.execute(
            f"SELECT r.rec_id, r.run_id, r.sku, r.current_price, r.recommended_price,"
            f" r.final_price, r.expected_revenue_delta, r.elasticity,"
            f" r.elasticity_ci_low, r.elasticity_ci_high, r.elasticity_samples,"
            f" r.created_at"
            f" FROM recommendations r WHERE r.status = 'pushed' {clause}"
            f" AND NOT EXISTS (SELECT 1 FROM outcomes o WHERE o.rec_id = r.rec_id)"
            f" ORDER BY r.created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def readback(run_id: str | None = None, limit: int = 500) -> dict:
    """Measure realized outcomes for pushed prices and refine from them.

    Returns a summary rather than raising on partial failure: a SKU with no
    post-change sales yet is a normal state, not an error. Advance the market
    (`POST /api/feedback/advance`) and call again.
    """
    pending = _pushed_recommendations(run_id, limit)
    if not pending:
        return {
            "measured": 0, "refined": 0, "skipped": 0,
            "detail": "No pushed recommendations are awaiting measurement.",
        }

    measured: list[dict] = []
    skipped = 0
    observations_by_sku: dict[str, list[Observation]] = defaultdict(list)
    priors: dict[str, dict] = {}

    with CommerceClient() as client:
        for rec in pending:
            try:
                rows = client.sales(sku=rec["sku"])
            except Exception as exc:  # noqa: BLE001
                logger.warning("feedback.sales_failed", sku=rec["sku"], error=str(exc))
                skipped += 1
                continue

            boundary = _boundary_date(rec)
            before, after = _split(rows, boundary)
            if not before or not after:
                skipped += 1
                continue

            units_before, rev_before, _ = _daily(before)
            units_after, rev_after, _ = _daily(after)

            forecast = rev_before + float(rec["expected_revenue_delta"] or 0.0)
            error_pct = (
                (rev_after - forecast) / forecast * 100.0 if forecast > 0 else 0.0
            )

            measured.append(
                {
                    "rec_id": rec["rec_id"], "run_id": rec["run_id"], "sku": rec["sku"],
                    "forecast_revenue": round(forecast, 2),
                    "realized_revenue": round(rev_after, 2),
                    "forecast_error_pct": round(error_pct, 2),
                }
            )
            price_after = float(rec["final_price"] or rec["recommended_price"])
            observations_by_sku[rec["sku"]].append(
                Observation(
                    sku=rec["sku"],
                    price_before=float(rec["current_price"]),
                    price_after=price_after,
                    units_before=units_before,
                    units_after=units_after,
                )
            )
            priors.setdefault(rec["sku"], rec)

    _persist_outcomes(measured)
    refinements = _refine_all(observations_by_sku, priors)

    logger.info(
        "feedback.readback", measured=len(measured), refined=len(refinements),
        skipped=skipped, run_id=run_id,
    )
    return {
        "measured": len(measured),
        "refined": len(refinements),
        "skipped": skipped,
        "outcomes": measured[:50],
        "refinements": [r.to_dict() for r in refinements][:50],
        "detail": (
            f"Measured {len(measured)} outcome(s); refined {len(refinements)} "
            f"elasticity estimate(s). {skipped} skipped for want of sales either "
            "side of the price change."
        ),
    }


def _boundary_date(rec: dict) -> date:
    raw = str(rec.get("created_at") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def _persist_outcomes(measured: list[dict]) -> None:
    if not measured:
        return
    with session() as conn:
        conn.executemany(
            "INSERT INTO outcomes (sku, run_id, rec_id, forecast_revenue,"
            " realized_revenue, forecast_error_pct, measured_at)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (m["sku"], m["run_id"], m["rec_id"], m["forecast_revenue"],
                 m["realized_revenue"], m["forecast_error_pct"], now_iso())
                for m in measured
            ],
        )
        audit(
            conn, actor="system", event_type="outcomes_measured",
            entity_type="feedback", entity_id="readback",
            measured=len(measured),
            mean_abs_error_pct=round(
                sum(abs(m["forecast_error_pct"]) for m in measured) / len(measured), 2
            ),
        )


def _refine_all(
    observations: dict[str, list[Observation]], priors: dict[str, dict]
) -> list:
    results = []
    for sku, obs in observations.items():
        prior = priors.get(sku, {})
        stored = get_estimate(sku)

        elasticity = (
            stored["elasticity"] if stored else prior.get("elasticity")
        )
        if elasticity is None:
            continue
        ci_low = (stored or prior).get("ci_low", prior.get("elasticity_ci_low"))
        ci_high = (stored or prior).get("ci_high", prior.get("elasticity_ci_high"))
        if ci_low is None or ci_high is None:
            ci_low, ci_high = elasticity - 0.5, elasticity + 0.5

        result = refine_elasticity(
            sku=sku,
            prior_elasticity=float(elasticity),
            prior_ci_low=float(ci_low),
            prior_ci_high=float(ci_high),
            prior_sample_size=int(
                (stored or {}).get("sample_size") or prior.get("elasticity_samples") or 60
            ),
            observations=obs,
            total_adjustment_so_far=float((stored or {}).get("total_adjustment", 0.0)),
        )
        if not result.applied:
            continue
        _store_estimate(result, int((stored or {}).get("sample_size") or 60))
        results.append(result)
    return results


def get_estimate(sku: str) -> dict | None:
    """The current elasticity belief for a SKU, refinements included."""
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM elasticity_estimates WHERE sku = ?", (sku,)
        ).fetchone()
    return dict(row) if row else None


def _store_estimate(result, sample_size: int) -> None:
    low, high = posterior_interval(
        result.posterior_elasticity, result.posterior_ci_width
    )
    with session() as conn:
        row = conn.execute(
            "SELECT refinement_count, total_adjustment FROM elasticity_estimates"
            " WHERE sku = ?", (result.sku,),
        ).fetchone()
        count = (row["refinement_count"] if row else 0) + 1
        total = (row["total_adjustment"] if row else 0.0) + result.step
        conn.execute(
            "INSERT INTO elasticity_estimates (sku, elasticity, ci_low, ci_high,"
            " sample_size, r_squared, refinement_count, total_adjustment, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(sku) DO UPDATE SET elasticity = excluded.elasticity,"
            " ci_low = excluded.ci_low, ci_high = excluded.ci_high,"
            " sample_size = excluded.sample_size,"
            " refinement_count = excluded.refinement_count,"
            " total_adjustment = excluded.total_adjustment,"
            " updated_at = excluded.updated_at",
            (
                result.sku, round(result.posterior_elasticity, 4), round(low, 4),
                round(high, 4), sample_size + result.observations_used, None,
                count, round(total, 4), now_iso(),
            ),
        )
        # FR-111: the magnitude of every adjustment is logged, so the loop is
        # inspectable rather than a black box that quietly drifts.
        audit(
            conn, actor="system", event_type="elasticity_refined",
            entity_type="sku", entity_id=result.sku, **result.to_dict(),
        )


# --- Consumed by the pipeline -------------------------------------------

def forecast_errors(sku: str | None = None, limit: int = 200) -> list[float]:
    clause = "WHERE sku = ?" if sku else ""
    params: list = [sku] if sku else []
    params.append(limit)
    with session() as conn:
        rows = conn.execute(
            f"SELECT forecast_error_pct FROM outcomes {clause}"
            f" ORDER BY measured_at ASC LIMIT ?",
            params,
        ).fetchall()
    return [r["forecast_error_pct"] for r in rows if r["forecast_error_pct"] is not None]


def refined_uncertainty() -> UncertaintyInputs:
    """Monte Carlo dispersion, tightened by observed forecast error (FR-108)."""
    scale = dispersion_scale(forecast_errors())
    base = UncertaintyInputs()
    if scale == 1.0:
        return base
    return UncertaintyInputs(
        competitor_response_sd=base.competitor_response_sd,
        cost_drift_sd=base.cost_drift_sd,
        demand_shock_sd=round(base.demand_shock_sd * scale, 4),
    )


def confidence_penalty(sku: str) -> float:
    """Multiplier on a SKU's confidence, from its own forecast track record.

    A SKU whose forecasts have repeatedly missed does not deserve the same
    confidence as one that has landed inside its interval, even when the
    regression looks equally tight (FR-109).
    """
    errors = forecast_errors(sku, limit=20)
    if len(errors) < 2:
        return 1.0
    mean_abs = sum(abs(e) for e in errors) / len(errors)
    if mean_abs <= 10.0:
        return 1.0
    # Linear taper: 10% error is neutral, 40% error costs a third of confidence.
    return round(max(1.0 - (mean_abs - 10.0) / 90.0, 0.55), 4)


def convergence(sku: str | None = None) -> dict:
    """Is forecast error narrowing across iterations (FR-092, FR-110)?"""
    signal = assess_convergence(forecast_errors(sku))
    return {
        "samples": signal.samples,
        "mean_abs_error_pct": signal.mean_abs_error_pct,
        "recent_error_pct": signal.recent_error_pct,
        "earlier_error_pct": signal.earlier_error_pct,
        "converging": signal.converging,
        "detail": signal.detail,
        "dispersion_scale": dispersion_scale(forecast_errors(sku)),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
