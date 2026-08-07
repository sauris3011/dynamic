"""Review queue: approve, reject, override, push (W2, FR-024, FR-025, FR-098)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from pricing.clients.commerce import CommerceClient
from pricing.core.logging import get_logger
from pricing.db.app_db import session
from pricing.pipeline import persistence
from pricing.pipeline.execution import push_recommendations, record_decision
from pricing.rules.engine import RuleConfig, evaluate

logger = get_logger("pricing.routes.recommendations")
router = APIRouter(prefix="/api/recommendations", tags=["recommendations"])


class DecisionRequest(BaseModel):
    actor: str = Field("operator", max_length=80)
    reason: str | None = Field(None, max_length=1000)


class OverrideRequest(DecisionRequest):
    price: float = Field(gt=0)
    reason: str = Field(min_length=3, max_length=1000,
                        description="Mandatory for overrides (FR-025).")


class PushRequest(BaseModel):
    rec_ids: list[str] = Field(min_length=1, max_length=5000)
    actor: str = Field("operator", max_length=80)


@router.get("")
def list_recommendations(
    run_id: str | None = None,
    band: str | None = Query(None, pattern="^(auto_approve|review|escalate)$"),
    status: str | None = None,
    category: str | None = None,
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict]:
    return persistence.list_recommendations(run_id, band, status, category, limit)


@router.get("/{rec_id}")
def get_recommendation(rec_id: str) -> dict:
    rec = persistence.get_recommendation(rec_id)
    if rec is None:
        raise HTTPException(404, f"No recommendation '{rec_id}'")
    return rec


def _load_or_404(rec_id: str) -> dict:
    rec = persistence.get_recommendation(rec_id)
    if rec is None:
        raise HTTPException(404, f"No recommendation '{rec_id}'")
    return rec


def _mode() -> str:
    from pricing.routes.runs import _current_mode

    return _current_mode().value


@router.post("/{rec_id}/approve")
def approve(rec_id: str, payload: DecisionRequest) -> dict:
    rec = _load_or_404(rec_id)
    if rec["compliance_status"] != "pass":
        # FR-033: a violation cannot be approved through the UI, in any mode.
        raise HTTPException(
            409,
            "This recommendation breaches a compliance rule and cannot be approved. "
            f"Reason: {rec['band_reason']}",
        )
    record_decision(rec_id, payload.actor, "approve", payload.reason,
                    _mode(), rec["recommended_price"])
    return {"rec_id": rec_id, "status": "approved"}


@router.post("/{rec_id}/reject")
def reject(rec_id: str, payload: DecisionRequest) -> dict:
    _load_or_404(rec_id)
    record_decision(rec_id, payload.actor, "reject", payload.reason, _mode(), None)
    return {"rec_id": rec_id, "status": "rejected"}


@router.post("/{rec_id}/override")
def override(rec_id: str, payload: OverrideRequest) -> dict:
    """Set a manual price. Re-validated by the compliance engine before it is
    accepted (FR-025) — an override is a human decision, not an exemption."""
    rec = _load_or_404(rec_id)

    with CommerceClient() as client:
        products = client.products(limit=10000)
    product = next((p for p in products if p["sku"] == rec["sku"]), None)
    if product is None:
        raise HTTPException(409, f"SKU '{rec['sku']}' no longer exists in the catalog.")

    family = [
        (p["size_value"], p["current_price"])
        for p in products
        if p["family_id"] == product["family_id"] and p["sku"] != rec["sku"]
    ]
    verdict = evaluate(
        sku=rec["sku"], proposed_price=payload.price,
        current_price=product["current_price"], unit_cost=product["unit_cost"],
        category=product["category"], map_price=product.get("map_price"),
        family_prices=family, size_value=product.get("size_value"),
        config=RuleConfig(),
    )
    if not verdict.passed:
        raise HTTPException(
            409,
            {
                "message": "Override rejected — the manual price breaches compliance.",
                "violations": [
                    {"rule": e.code, "detail": e.detail} for e in verdict.violations
                ],
            },
        )

    record_decision(rec_id, payload.actor, "override", payload.reason,
                    _mode(), payload.price)
    logger.info("recommendations.override", rec_id=rec_id, sku=rec["sku"],
                price=payload.price, actor=payload.actor)
    return {"rec_id": rec_id, "status": "overridden", "price": payload.price}


@router.post("/push")
def push(payload: PushRequest) -> dict:
    """Push approved recommendations to the Commerce Service (W5)."""
    result = push_recommendations(payload.rec_ids, payload.actor)
    return result


@router.post("/{rec_id}/revert")
def revert(rec_id: str, payload: DecisionRequest) -> dict:
    """Restore a SKU to the price it held before this recommendation (FR-098).

    Implemented as an ordinary price push rather than a special path, so it
    inherits idempotency and the receiving service's own re-validation.
    """
    rec = _load_or_404(rec_id)
    if rec["status"] != "pushed":
        raise HTTPException(409, f"Recommendation is '{rec['status']}', not pushed.")

    from pricing.pipeline.execution import new_batch_key

    batch_key = new_batch_key(f"revert-{rec_id}")
    with CommerceClient() as client:
        result = client.push_prices(
            batch_key,
            [{"sku": rec["sku"], "new_price": rec["current_price"],
              "reason": f"revert of {rec_id}: {payload.reason or 'no reason given'}"}],
        )
    record_decision(rec_id, payload.actor, "revert", payload.reason,
                    _mode(), rec["current_price"])
    return {"rec_id": rec_id, "status": "reverted",
            "restored_price": rec["current_price"], "commerce_result": result}


@router.get("/audit/{sku}")
def sku_audit(sku: str, limit: int = Query(50, ge=1, le=500)) -> dict:
    """Per-SKU timeline for compliance review (FR-058, W7)."""
    with session() as conn:
        recs = [
            dict(r) for r in conn.execute(
                "SELECT rec_id, run_id, current_price, recommended_price, band,"
                " band_reason, compliance_status, status, confidence, created_at"
                " FROM recommendations WHERE sku = ? ORDER BY created_at DESC LIMIT ?",
                (sku, limit),
            )
        ]
        events = [
            dict(r) for r in conn.execute(
                "SELECT ts, actor, event_type, entity_id, detail_json FROM audit_log"
                " WHERE entity_id IN (SELECT rec_id FROM recommendations WHERE sku = ?)"
                " ORDER BY ts DESC LIMIT ?",
                (sku, limit),
            )
        ]
    return {"sku": sku, "recommendations": recs, "audit_events": events}
