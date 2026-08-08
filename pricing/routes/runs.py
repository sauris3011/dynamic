"""Pricing run endpoints (W1, FR-069)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field

from pricing.analytics.optimizer import Objective
from pricing.config import OperatingMode, get_settings
from pricing.core.logging import get_logger
from pricing.db.app_db import get_setting, session
from pricing.pipeline import persistence, progress
from pricing.pipeline.execution import auto_approve_and_push
from pricing.pipeline.orchestrator import run_pipeline
from pricing.pipeline.state import RunScope, RunState

logger = get_logger("pricing.routes.runs")
router = APIRouter(prefix="/api/runs", tags=["runs"])

# Live progress lives in `pipeline.progress` so the stages themselves can report
# into it without importing the API layer. This module only starts and finishes
# an entry; everything between comes from the pipeline as it works.
STAGES = ["data_context", "quantitative", "strategy", "validation", "persisted"]


class RunRequest(BaseModel):
    scope_kind: Literal["all", "category", "skus"] = "category"
    scope_value: str | None = Field(None, description="Category name when scope_kind=category")
    skus: list[str] = Field(default_factory=list)
    objective: Literal["revenue", "margin", "balanced"] = "balanced"
    trigger: Literal["manual", "loop", "a2a"] = "manual"


class RunAccepted(BaseModel):
    run_id: str
    status: str
    scope: str
    mode: str


def _current_mode() -> OperatingMode:
    """Runtime mode wins over the boot default so the UI switch takes effect
    without a restart (FR-067, FR-101)."""
    with session() as conn:
        stored = get_setting(conn, "operating_mode")
    if stored:
        try:
            return OperatingMode(stored)
        except ValueError:
            logger.warning("runs.bad_stored_mode", value=stored)
    return get_settings().operating_mode


def _execute_stages(state: RunState) -> RunState:
    """Run stages 1-4 through LangGraph when it is available (D2).

    Both routes call the same stage functions, so this switch changes
    checkpointing and tracing — never the prices produced.
    """
    if get_settings().use_langgraph:
        from pricing.pipeline.graph import run_with_graph

        return run_with_graph(state)
    return run_pipeline(state)


def execute_run(state: RunState) -> RunState:
    """Run the pipeline, persist it, then apply the autonomy policy."""
    progress.update(
        state.run_id, status="running", stage="data_context",
        detail="Starting up.",
    )
    try:
        state = _execute_stages(state)

        progress.update(
            state.run_id,
            stages={k: round(v, 2) for k, v in state.stage_timings.items()},
            stage="persisted",
            detail="Saving the results.",
        )

        if state.status in ("completed", "halted", "failed"):
            persistence.save_run(state)

        progress.finish(
            state.run_id, status=state.status,
            sku_count=len(state.priced), bands=state.band_counts(),
            errors=state.errors,
            quality=state.quality.verdict.value if state.quality else None,
            detail="Done.",
        )

        if state.status == "completed":
            auto = auto_approve_and_push(state.run_id, state.mode)
            progress.update(state.run_id, autonomy=auto)
            logger.info("runs.autonomy_applied", run_id=state.run_id, **auto)
    except Exception as exc:  # noqa: BLE001
        logger.exception("runs.execute_failed", run_id=state.run_id)
        progress.finish(state.run_id, status="failed", errors=[str(exc)])
    return state


@router.post("", response_model=RunAccepted, status_code=202)
def start_run(payload: RunRequest, background: BackgroundTasks) -> RunAccepted:
    """Trigger a pricing run. Returns immediately; poll /progress for status."""
    if payload.scope_kind == "category" and not payload.scope_value:
        raise HTTPException(422, "scope_value is required when scope_kind='category'")
    if payload.scope_kind == "skus" and not payload.skus:
        raise HTTPException(422, "skus must be non-empty when scope_kind='skus'")

    state = RunState(
        trigger=payload.trigger,
        scope=RunScope(payload.scope_kind, payload.scope_value, payload.skus),
        objective=Objective(payload.objective),
        mode=_current_mode(),
    )
    progress.start(state.run_id)
    background.add_task(execute_run, state)
    logger.info(
        "runs.started", run_id=state.run_id, scope=state.scope.describe(),
        objective=state.objective.value, mode=state.mode.value,
    )
    return RunAccepted(
        run_id=state.run_id, status="queued",
        scope=state.scope.describe(), mode=state.mode.value,
    )


@router.get("/progress/{run_id}")
def run_progress(run_id: str) -> dict:
    """Live progress for the UI (FR-069).

    Reports the current stage, how many items it has worked through, and an
    overall fraction — enough for the client to draw a bar and estimate a
    finish time, so a slow run is visibly slow rather than indistinguishable
    from a hung one.
    """
    live = progress.get(run_id)
    if live:
        return live
    stored = persistence.get_run(run_id)
    if stored is None:
        raise HTTPException(404, f"No run '{run_id}'")
    return {
        "run_id": run_id,
        "status": stored["status"],
        "stage": "persisted",
        "overall": 1.0,
    }


@router.get("/topology")
def topology() -> dict:
    """The compiled orchestration graph (D2), for the UI and for verification."""
    from pricing.pipeline.graph import topology as graph_topology

    data = graph_topology()
    data["engine"] = "langgraph" if (
        get_settings().use_langgraph and data["available"]
    ) else "standalone"
    return data


@router.get("")
def list_runs(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return persistence.list_runs(limit)


@router.get("/{run_id}")
def get_run(run_id: str) -> dict:
    run = persistence.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"No run '{run_id}'")
    with session() as conn:
        counts = conn.execute(
            "SELECT band, COUNT(*) AS n FROM recommendations WHERE run_id = ?"
            " GROUP BY band", (run_id,),
        ).fetchall()
    run["band_counts"] = {r["band"]: r["n"] for r in counts}
    return run
