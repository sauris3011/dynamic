"""Scenario simulation endpoints (W3, FR-045 .. FR-050, FR-105, FR-106)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from pricing.analytics.optimizer import Objective
from pricing.clients.commerce import CommerceUnavailable
from pricing.core.logging import get_logger
from pricing.services import simulation

logger = get_logger("pricing.routes.simulation")
router = APIRouter(prefix="/api/simulate", tags=["simulation"])


class SimulateRequest(BaseModel):
    skus: list[str] = Field(min_length=1, max_length=50)
    prices: dict[str, list[float]] = Field(
        default_factory=dict,
        description=(
            "Candidate prices per SKU. Omit to sweep the standard band around "
            "the current price."
        ),
    )
    horizon_days: int = Field(28, ge=1, le=365)
    objective: Literal["revenue", "margin", "balanced"] = "balanced"
    include_stress: bool = Field(
        False, description="Also project demand collapse, competitor undercut, "
                           "and cost spike (FR-086, FR-106)."
    )


class SaveScenarioRequest(SimulateRequest):
    name: str = Field(min_length=1, max_length=120)
    actor: str = Field("operator", max_length=80)


@router.post("")
def run_simulation(payload: SimulateRequest) -> dict:
    """What-if projection over the same Monte Carlo engine the pipeline uses."""
    try:
        result = simulation.run(
            skus=payload.skus,
            candidate_prices=payload.prices,
            horizon_days=payload.horizon_days,
            objective=Objective(payload.objective),
            include_stress=payload.include_stress,
        )
    except CommerceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    if not result["results"]:
        raise HTTPException(
            404, f"None of {payload.skus} exist in the catalog."
        )
    return result


@router.post("/stress")
def run_stress(payload: SimulateRequest) -> dict:
    """Stress-test shortcut — the same call with downside always included."""
    payload.include_stress = True
    return run_simulation(payload)


@router.post("/scenarios")
def save_scenario(payload: SaveScenarioRequest) -> dict:
    """Save a scenario with its full projection, so a later comparison shows
    what was actually forecast at the time (FR-048)."""
    request = payload.model_dump(exclude={"name", "actor"})
    result = run_simulation(SimulateRequest(**request))
    saved = simulation.save_scenario(payload.name, request, result, payload.actor)
    return {**saved, "result": result}


@router.get("/scenarios")
def list_scenarios(limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    return simulation.list_scenarios(limit)


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    scenario = simulation.get_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(404, f"No scenario '{scenario_id}'")
    return scenario


@router.get("/scenarios/compare/{left_id}/{right_id}")
def compare_scenarios(left_id: str, right_id: str) -> dict:
    """Side-by-side comparison of two saved scenarios (FR-048, FR-049)."""
    left, right = simulation.get_scenario(left_id), simulation.get_scenario(right_id)
    for scenario_id, scenario in ((left_id, left), (right_id, right)):
        if scenario is None:
            raise HTTPException(404, f"No scenario '{scenario_id}'")

    def totals(s: dict) -> dict:
        return {
            "name": s["name"],
            "created_at": s["created_at"],
            "horizon_days": s["horizon_days"],
            **s["result"]["totals"],
        }

    lt, rt = totals(left), totals(right)
    if lt["horizon_days"] != rt["horizon_days"]:
        note = (
            "Horizons differ — totals are not directly comparable without "
            "normalising to the same period."
        )
    else:
        note = "Same horizon; totals are directly comparable."
    return {
        "left": lt,
        "right": rt,
        "delta": {
            "baseline_revenue": round(
                rt["baseline_revenue"] - lt["baseline_revenue"], 2
            ),
            "ai_revenue_delta": round(
                rt["ai_revenue_delta"] - lt["ai_revenue_delta"], 2
            ),
        },
        "note": note,
    }
