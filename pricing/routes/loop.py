"""Continuous loop control and the global kill switch (W9, FR-117, FR-102)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from pricing.config import OperatingMode
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, session, set_setting
from pricing.services import loop as loop_service

logger = get_logger("pricing.routes.loop")
router = APIRouter(prefix="/api/loop", tags=["loop"])


class LoopStartRequest(BaseModel):
    interval_seconds: int | None = Field(None, ge=30, le=86400)
    scope_kind: Literal["all", "category", "skus"] = "category"
    scope_value: str | None = None
    skus: list[str] = Field(default_factory=list)
    objective: Literal["revenue", "margin", "balanced"] = "balanced"
    actor: str = Field("operator", max_length=80)


class ActorRequest(BaseModel):
    actor: str = Field("operator", max_length=80)
    reason: str | None = Field(None, max_length=500)


@router.get("/status")
def loop_status() -> dict:
    return loop_service.status()


@router.post("/start")
def start_loop(payload: LoopStartRequest) -> dict:
    return loop_service.start(
        interval_seconds=payload.interval_seconds,
        scope_kind=payload.scope_kind,
        scope_value=payload.scope_value,
        skus=payload.skus,
        objective=payload.objective,
        actor=payload.actor,
    )


@router.post("/stop")
def stop_loop(payload: ActorRequest) -> dict:
    return loop_service.stop(actor=payload.actor)


@router.post("/kill-switch")
def kill_switch(payload: ActorRequest) -> dict:
    """Halt everything and revert to Supervised in one action (FR-102).

    Three effects, in this order: stop the loop, cancel pending automatic
    approvals, revert the operating mode. Order matters — reverting the mode
    first would still leave an in-flight iteration running under the old policy.
    """
    loop_result = loop_service.stop(actor=payload.actor)

    with session() as conn:
        cancelled = conn.execute(
            "UPDATE recommendations SET status = 'rejected' WHERE status = 'approved'"
            " AND rec_id IN (SELECT rec_id FROM approvals WHERE actor = 'system')"
        ).rowcount
        set_setting(conn, "operating_mode", OperatingMode.SUPERVISED.value)
        audit(
            conn, actor=payload.actor, event_type="kill_switch",
            entity_type="system", entity_id="global",
            reason=payload.reason, loop_was_running=loop_result.get("was_running"),
            cancelled_auto_approvals=cancelled,
        )

    logger.warning(
        "loop.kill_switch", actor=payload.actor, reason=payload.reason,
        cancelled=cancelled,
    )
    return {
        "halted": True,
        "loop_stopped": loop_result.get("was_running", False),
        "cancelled_auto_approvals": cancelled,
        "mode": OperatingMode.SUPERVISED.value,
    }
