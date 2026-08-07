"""Evaluation-only endpoints.

Serves the ground-truth elasticity used to synthesize sales history (FR-002).
This exists so the scoring harness can measure how close a recommendation lands
to the true optimum (PRD 9.1).

**The pricing pipeline must never call this.** Elasticity is the thing the
system is supposed to *estimate*; reading the answer key mid-run would make the
accuracy metric meaningless. The endpoint is namespaced under /eval and flagged
in its response so misuse is obvious in a trace.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from commerce.db import session
from commerce.models import GroundTruthRecord

router = APIRouter(prefix="/eval", tags=["evaluation"])


@router.get("/ground-truth", response_model=list[GroundTruthRecord])
def ground_truth(
    sku: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
) -> list[GroundTruthRecord]:
    clauses, params = [], []
    if sku:
        clauses.append("g.sku = ?")
        params.append(sku)
    if category:
        clauses.append("p.category = ?")
        params.append(category)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    with session() as conn:
        rows = conn.execute(
            f"SELECT g.sku, g.true_elasticity, g.base_demand, g.ref_price "
            f"FROM ground_truth g JOIN products p ON p.sku = g.sku {where} "
            f"ORDER BY g.sku LIMIT ?",
            params,
        ).fetchall()
    return [GroundTruthRecord(**dict(r)) for r in rows]
