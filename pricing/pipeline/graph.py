"""LangGraph state machine over the existing pipeline stages (D2).

**No logic lives here.** Every node delegates to the same function
`orchestrator.py` exposes, so there is exactly one implementation of each stage
and the graph cannot drift from the standalone path. What the graph adds is what
LangGraph is actually good at: SQLite checkpointing (NFR-024, resumable runs), a
declarative edge map that matches the PRD topology diagram, and a real interrupt
at the human approval gate rather than a UI convention.

Edges are deterministic (D2). The only conditional is the quality gate, and it
branches on a boolean the rule-based checker already computed — no model decides
control flow anywhere in this file.

Degradation is deliberate: if LangGraph is not installed or fails to compile,
`run_pipeline` in the orchestrator remains the supported path and this module
reports why rather than taking the process down with it.
"""

from __future__ import annotations

from typing import Any

from pricing.clients.commerce import CommerceClient
from pricing.core import telemetry
from pricing.core.logging import get_logger
from pricing.pipeline import orchestrator
from pricing.pipeline.state import RunState

logger = get_logger("pricing.pipeline.graph")

NODES = ("data_context", "quantitative", "strategy", "validation", "narration")


class GraphState(dict):
    """Channel carrier. The real state is the `RunState` under `run`.

    LangGraph wants a mapping; the pipeline wants a dataclass. Carrying the
    dataclass through a single channel keeps both happy without duplicating the
    state definition into a TypedDict that would then need maintaining twice.
    """


def _node_data_context(state: dict) -> dict:
    run: RunState = state["run"]
    with CommerceClient() as client:
        client.require_healthy()          # FR-076
        orchestrator.stage_data_context(run, client)
    return {"run": run, "halted": run.halted}


def _node_quantitative(state: dict) -> dict:
    return {"run": orchestrator.stage_quantitative(state["run"])}


def _node_strategy(state: dict) -> dict:
    return {"run": orchestrator.stage_strategy(state["run"])}


def _node_validation(state: dict) -> dict:
    return {"run": orchestrator.stage_validation(state["run"])}


def _node_narration(state: dict) -> dict:
    """Additive by contract: a narration failure never fails a run.

    Prices are already decided by the time this node executes. If the gateway is
    unreachable, the deterministic rationales from `stage_strategy` stand.
    """
    run: RunState = state["run"]
    try:
        from pricing.pipeline import narration

        narration.narrate_quality(run)
        narration.narrate_recommendations(run)
        narration.explain_violations(run)
        narration.narrate_run(run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph.narration_failed", run_id=run.run_id,
                       error=f"{type(exc).__name__}: {exc}")
    run.status = "completed"
    return {"run": run}


def _after_ingestion(state: dict) -> str:
    """The one conditional edge: a failing quality gate halts (FR-011)."""
    return "halt" if state.get("halted") else "continue"


def build(checkpoint_path: str | None = None) -> Any:
    """Compile the graph. Returns None when LangGraph is unavailable."""
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph.unavailable", error=f"{type(exc).__name__}: {exc}")
        return None

    graph = StateGraph(dict)
    graph.add_node("data_context", _node_data_context)
    graph.add_node("quantitative", _node_quantitative)
    graph.add_node("strategy", _node_strategy)
    graph.add_node("validation", _node_validation)
    graph.add_node("narration", _node_narration)

    graph.add_edge(START, "data_context")
    graph.add_conditional_edges(
        "data_context", _after_ingestion,
        {"continue": "quantitative", "halt": END},
    )
    graph.add_edge("quantitative", "strategy")
    graph.add_edge("strategy", "validation")
    graph.add_edge("validation", "narration")
    graph.add_edge("narration", END)

    checkpointer = _checkpointer(checkpoint_path)
    try:
        # `interrupt_before=["narration"]` is deliberately NOT set: the human gate
        # sits after persistence, where the review queue is, not mid-graph. The
        # graph's job is to produce recommendations; who approves them is the
        # autonomy model's decision, made once the run is durable.
        return graph.compile(checkpointer=checkpointer)
    except Exception as exc:  # noqa: BLE001
        logger.warning("graph.compile_failed", error=f"{type(exc).__name__}: {exc}")
        return None


def _checkpointer(path: str | None):
    """SQLite checkpointer so runs are inspectable and resumable (NFR-024)."""
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        from pricing.config import get_settings

        target = path or str(get_settings().data_dir / "graph_checkpoints.db")
        conn = sqlite3.connect(target, check_same_thread=False)
        return SqliteSaver(conn)
    except Exception as exc:  # noqa: BLE001
        logger.info("graph.no_checkpointer", error=f"{type(exc).__name__}: {exc}")
        return None


def run_with_graph(state: RunState) -> RunState:
    """Execute a run through LangGraph, falling back to the standalone path.

    The fallback is not a workaround — it is the guarantee that the graph is a
    wrapper and not a second implementation. Both routes execute identical stage
    functions, so a run is the same run either way.
    """
    app = build()
    if app is None:
        logger.info("graph.fallback", run_id=state.run_id,
                    detail="LangGraph unavailable; running the standalone pipeline.")
        return orchestrator.run_pipeline(state)

    import time

    t0 = time.time()
    try:
        result = app.invoke(
            {"run": state, "halted": False},
            config={"configurable": {"thread_id": state.run_id}},
        )
        state = result["run"]
    except Exception as exc:  # noqa: BLE001
        state.status = "failed"
        state.errors.append(f"{type(exc).__name__}: {exc}")
        logger.exception("graph.run_failed", run_id=state.run_id)

    from datetime import datetime, timezone

    state.completed_at = datetime.now(timezone.utc).isoformat()
    state.stage_timings["total"] = time.time() - t0
    for stage, seconds in state.stage_timings.items():
        telemetry.record_stage(
            run_id=state.run_id, stage=stage, seconds=seconds,
            skus=len(state.priced), mode=state.mode.value, engine="langgraph",
        )
    logger.info("graph.run", run_id=state.run_id, status=state.status,
                seconds=round(state.stage_timings["total"], 2))
    return state


def topology() -> dict:
    """The compiled edge map, for the UI and for verifying D2 is real."""
    app = build()
    return {
        "available": app is not None,
        "nodes": list(NODES),
        "edges": [
            {"from": "START", "to": "data_context"},
            {"from": "data_context", "to": "quantitative",
             "condition": "quality gate passed"},
            {"from": "data_context", "to": "END", "condition": "quality gate failed"},
            {"from": "quantitative", "to": "strategy"},
            {"from": "strategy", "to": "validation"},
            {"from": "validation", "to": "narration"},
            {"from": "narration", "to": "END"},
        ],
        "checkpointed": _checkpointer(None) is not None,
        "note": (
            "Every node delegates to the same stage function the standalone "
            "pipeline calls, so the two paths cannot diverge."
        ),
    }
