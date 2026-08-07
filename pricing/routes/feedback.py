"""Closed feedback loop endpoints (W6, FR-055, FR-107 .. FR-111)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from pricing.clients.commerce import CommerceClient, CommerceUnavailable
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, session
from pricing.services import feedback

logger = get_logger("pricing.routes.feedback")
router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class ReadbackRequest(BaseModel):
    run_id: str | None = None
    limit: int = Field(500, ge=1, le=5000)


class AdvanceRequest(BaseModel):
    days: int = Field(7, ge=1, le=90)
    actor: str = Field("operator", max_length=80)


@router.post("/readback")
def readback(payload: ReadbackRequest) -> dict:
    """Measure realized outcomes for pushed prices and refine from them.

    Idempotent: a recommendation that already has a measured outcome is not
    re-measured, so calling this twice does not double-count evidence.
    """
    try:
        return feedback.readback(payload.run_id, payload.limit)
    except CommerceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/advance-market")
def advance_market(payload: AdvanceRequest) -> dict:
    """Ask the Commerce Service to transact forward at current prices.

    Sales history is generated up to today, so a price pushed now has no future
    to be observed in. This asks the system of record to move its own clock; the
    platform then reads back ordinary sales rows and infers the response. It
    never sees the ground-truth elasticity that generated them.
    """
    try:
        with CommerceClient() as client:
            result = client.advance_market(payload.days)
    except CommerceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    with session() as conn:
        audit(conn, actor=payload.actor, event_type="market_advanced",
              entity_type="commerce", entity_id="market",
              days=payload.days, rows=result.get("rows_written"))
    return result


@router.get("/clock")
def clock() -> dict:
    """Where the transaction record ends, and how far behind today it is."""
    try:
        with CommerceClient() as client:
            return client.market_clock()
    except CommerceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/convergence")
def convergence(sku: str | None = None) -> dict:
    """Forecast-error trend across iterations (FR-092, FR-110).

    A widening delta is reported as a warning, not smoothed away — a loop that
    is drifting is worse than no loop, because it looks like progress.
    """
    return feedback.convergence(sku)


@router.get("/outcomes")
def outcomes(
    sku: str | None = None,
    run_id: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
) -> list[dict]:
    clauses, params = [], []
    if sku:
        clauses.append("sku = ?")
        params.append(sku)
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with session() as conn:
        rows = conn.execute(
            f"SELECT * FROM outcomes {where} ORDER BY measured_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/estimates")
def estimates(limit: int = Query(200, ge=1, le=2000)) -> list[dict]:
    """Current elasticity beliefs with their refinement history (FR-111)."""
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM elasticity_estimates"
            " ORDER BY ABS(total_adjustment) DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
