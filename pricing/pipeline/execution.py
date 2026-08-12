"""Agent 5 — Execution. Deterministic: no LLM in the push path (PRD 4.2).

Formatting a payload, calling an API, and writing an audit record involve no
judgement, so nothing here is model-driven. The "agent" label is retained for
consistency with the source architecture document, not as a claim of AI
capability.

Two invariants this module exists to hold:

* **Nothing non-compliant is ever pushed.** Compliance is re-checked at push
  time, not trusted from when the recommendation was generated. A rule change
  between generation and approval must take effect.
* **A push is safe to retry.** Every batch carries a key; the Commerce Service
  replays rather than re-applies (FR-052).
"""

from __future__ import annotations

import uuid

from pricing.clients.commerce import CommerceClient
from pricing.config import OperatingMode, get_settings
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, now_iso, session
from pricing.rules.bands import Band, may_push_automatically

logger = get_logger("pricing.execution")

SYSTEM_ACTOR = "system"


def new_batch_key(run_id: str | None = None) -> str:
    suffix = uuid.uuid4().hex[:10]
    return f"{run_id or 'manual'}-{suffix}"


def eligible_for_auto_push(band: str, mode: OperatingMode) -> bool:
    try:
        return may_push_automatically(Band(band), mode)
    except ValueError:
        return False


def record_decision(
    rec_id: str, actor: str, action: str, reason: str | None,
    mode: str, price: float | None,
) -> None:
    with session() as conn:
        conn.execute(
            "INSERT INTO approvals (rec_id, actor, action, reason, mode, price,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (rec_id, actor, action, reason, mode, price, now_iso()),
        )
        status = {
            "approve": "approved", "reject": "rejected",
            "override": "overridden", "revert": "reverted",
        }.get(action, "pending")
        conn.execute(
            "UPDATE recommendations SET status = ?, final_price = "
            "CASE WHEN ? = 'approve' AND final_price IS NOT NULL THEN final_price "
            "ELSE COALESCE(?, final_price) END WHERE rec_id = ?",
            (status, action, price, rec_id),
        )
        audit(
            conn, actor=actor, event_type=f"recommendation_{action}",
            entity_type="recommendation", entity_id=rec_id,
            reason=reason, mode=mode, price=price,
        )


def push_recommendations(rec_ids: list[str], actor: str, run_id: str | None = None) -> dict:
    """Push approved recommendations to the Commerce Service.

    Compliance is re-verified here even though it was checked at generation
    time. That is not belt-and-braces paranoia: rules are runtime-editable
    (FR-032), so a recommendation approved an hour ago may no longer be legal.
    """
    settings = get_settings()
    if not rec_ids:
        return {"pushed": 0, "skipped": 0, "detail": "No recommendations supplied."}

    placeholders = ",".join("?" for _ in rec_ids)
    with session() as conn:
        rows = [
            dict(r) for r in conn.execute(
                f"SELECT rec_id, sku, recommended_price, final_price, band,"
                f" compliance_status, status FROM recommendations "
                f"WHERE rec_id IN ({placeholders})",
                rec_ids,
            )
        ]

    items, pushable, blocked = [], [], []
    for row in rows:
        if row["compliance_status"] != "pass":
            blocked.append(
                {"rec_id": row["rec_id"], "sku": row["sku"],
                 "reason": "Compliance violation — cannot be pushed in any mode."}
            )
            continue
        if row["status"] not in ("approved", "overridden"):
            blocked.append(
                {"rec_id": row["rec_id"], "sku": row["sku"],
                 "reason": f"Status is '{row['status']}', not approved."}
            )
            continue
        price = row["final_price"] or row["recommended_price"]
        items.append({"sku": row["sku"], "new_price": price,
                      "reason": f"rec {row['rec_id']}"})
        pushable.append(row)

    if not items:
        return {"pushed": 0, "skipped": len(blocked), "blocked": blocked,
                "detail": "Nothing eligible to push."}

    batch_key = new_batch_key(run_id)
    with CommerceClient() as client:
        result = client.push_prices(batch_key, items)

    by_sku = {r["sku"]: r for r in result.get("results", [])}
    with session() as conn:
        for row in pushable:
            outcome = by_sku.get(row["sku"], {})
            status = "pushed" if outcome.get("status") == "applied" else "failed"
            conn.execute(
                "UPDATE recommendations SET status = ? WHERE rec_id = ?",
                (status, row["rec_id"]),
            )
        conn.execute(
            "INSERT OR REPLACE INTO push_batches (batch_key, run_id, created_at,"
            " item_count, applied, rejected, result_json) VALUES (?,?,?,?,?,?,?)",
            (
                batch_key, run_id, now_iso(), result.get("item_count", 0),
                result.get("applied_count", 0), result.get("rejected_count", 0),
                __import__("json").dumps(result),
            ),
        )
        audit(
            conn, actor=actor, event_type="prices_pushed",
            entity_type="batch", entity_id=batch_key,
            applied=result.get("applied_count"), rejected=result.get("rejected_count"),
            blocked=len(blocked), mode=settings.operating_mode.value,
        )

    logger.info(
        "execution.push", batch_key=batch_key, actor=actor,
        applied=result.get("applied_count"), rejected=result.get("rejected_count"),
        blocked=len(blocked),
    )
    return {
        "batch_key": batch_key,
        "pushed": result.get("applied_count", 0),
        "rejected": result.get("rejected_count", 0),
        "skipped": len(blocked),
        "blocked": blocked,
        "commerce_result": result,
    }


def auto_approve_and_push(run_id: str, mode: OperatingMode) -> dict:
    """Apply the autonomy policy for a completed run (FR-101).

    In Supervised mode this is a no-op by design — the whole point of the
    default posture is that nothing moves without a person.
    """
    if mode is OperatingMode.SUPERVISED:
        return {"auto_approved": 0, "pushed": 0,
                "detail": "Supervised mode — no automatic approval."}

    with session() as conn:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT rec_id, band, recommended_price, current_price FROM"
                " recommendations WHERE run_id = ? AND status = 'pending'"
                " AND compliance_status = 'pass'",
                (run_id,),
            )
        ]

    eligible = [r for r in rows if eligible_for_auto_push(r["band"], mode)]
    # A "hold" recommendation needs no push — approving it would churn the
    # audit log for no change.
    actionable = [
        r for r in eligible
        if abs(r["recommended_price"] - r["current_price"]) >= 0.005
    ]

    for row in eligible:
        record_decision(
            row["rec_id"], SYSTEM_ACTOR, "approve",
            f"Auto-approved in {mode.value} mode (band={row['band']}).",
            mode.value, row["recommended_price"],
        )

    push_result = (
        push_recommendations([r["rec_id"] for r in actionable], SYSTEM_ACTOR, run_id)
        if actionable else {"pushed": 0}
    )
    return {
        "auto_approved": len(eligible),
        "held_no_change": len(eligible) - len(actionable),
        "pushed": push_result.get("pushed", 0),
        "batch_key": push_result.get("batch_key"),
        "mode": mode.value,
    }
