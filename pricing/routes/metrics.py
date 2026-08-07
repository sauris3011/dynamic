"""Dashboard endpoints (FR-056, FR-057, FR-061, FR-112, FR-113, PRD 9.1).

Thin transport over `services.metrics` and `services.scoring` — the queries live
there so they can be exercised without a running server.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from pricing.services import metrics, scoring

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/performance")
def performance(run_id: str | None = None) -> dict:
    """Revenue and margin impact, forecast vs realized, acceptance rate."""
    return metrics.performance(run_id)


@router.get("/baseline")
def baseline(run_id: str | None = None) -> dict:
    """AI recommendations against the rule-based pricer on identical data."""
    return metrics.baseline_comparison(run_id)


@router.get("/stability")
def stability() -> dict:
    """Oscillation incidents, damping applied, convergence trend."""
    return metrics.stability()


@router.get("/autonomy")
def autonomy() -> dict:
    """Band distribution, auto-approve rate, escalation reasons, mode history."""
    return metrics.autonomy()


@router.get("/runs")
def runs(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    """Run history with status, duration, token cost and outcome."""
    return metrics.run_history(limit)


@router.get("/accuracy")
def accuracy(run_id: str | None = None, category: str | None = None) -> dict:
    """Pricing accuracy against ground-truth elasticity.

    Evaluation only. The pipeline never reads the answer key — this endpoint
    exists so the claim in PRD 9.1 can be measured rather than asserted.
    """
    return scoring.score(run_id, category)


@router.get("/summary")
def summary() -> dict:
    """One call for the dashboard header — the five headline numbers."""
    perf = metrics.performance()
    auton = metrics.autonomy()
    stab = metrics.stability()
    base = metrics.baseline_comparison()
    return {
        "acceptance_rate": perf["acceptance_rate"],
        "auto_approve_rate": auton["auto_approve_rate"],
        "band_shares": auton["band_shares"],
        "oscillation_rate": stab["oscillation_rate"],
        "converging": stab["convergence"]["converging"],
        "mean_abs_forecast_error_pct": perf["realized"]["mean_abs_error_pct"],
        "ai_uplift_pct": base.get("ai_uplift_pct"),
        "recommendations": perf["recommendations"],
        "outcomes_measured": perf["realized"]["measured"],
    }
