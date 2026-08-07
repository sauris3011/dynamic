"""Agent-to-Agent interoperability (D11, FR-071).

Spec-shaped but deliberately not the full A2A task lifecycle: an agent card at
`/.well-known/agent-card.json`, plus JSON-RPC `message/send` and `tasks/get`.
Streaming, push notifications, artifact negotiation, and cancellation are out of
scope — the goal is that another agent can discover this one and ask it to price
something, not that we implement a protocol we have no second party for.

**The autonomy model applies to A2A callers exactly as it does to the UI.** A
peer agent cannot request a mode, cannot bypass the compliance veto, and cannot
push a price. It triggers a run; what happens next is governed by the operating
mode in force, decided here. An external agent is a trigger, never an authority.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from pricing import __version__
from pricing.analytics.optimizer import Objective
from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, now_iso, session
from pricing.pipeline import persistence
from pricing.pipeline.state import RunScope, RunState

logger = get_logger("pricing.routes.a2a")
router = APIRouter(tags=["a2a"])

# Task index. Ephemeral by design: the durable record of every run is app.db, and
# `tasks/get` resolves through to it once the in-memory entry is gone.
_tasks: dict[str, dict] = {}
_lock = threading.Lock()

SKILLS = [
    {
        "id": "price-recommendation",
        "name": "Generate price recommendations",
        "description": (
            "Runs the five-agent pricing pipeline over a category or SKU set and "
            "returns banded recommendations with confidence intervals, compliance "
            "verdicts, and cited rationale. Never pushes a price."
        ),
        "tags": ["pricing", "retail", "optimization"],
        "examples": ["Price the Beverages category on a balanced objective."],
    },
    {
        "id": "explain-recommendation",
        "name": "Explain a recommendation",
        "description": (
            "Returns the full decision record for one recommendation: inputs, "
            "elasticity interval, simulated distribution, rule evaluations, and "
            "the specific condition that decided its autonomy band."
        ),
        "tags": ["pricing", "audit", "explainability"],
    },
]


class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


def _error(request_id, code: int, message: str, data: Any = None) -> dict:
    body = {"code": code, "message": message}
    if data is not None:
        body["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": body}


def _ok(request_id, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.get("/.well-known/agent-card.json")
def agent_card() -> dict:
    """Discovery document. Static apart from the configured port."""
    s = get_settings()
    return {
        "protocolVersion": "0.2.0",
        "name": "Dynamic Pricing Engine",
        "description": (
            "AI pricing platform for retail. Deterministic optimization and a "
            "compliance rule engine with veto power; language models explain "
            "decisions but never make them."
        ),
        "version": __version__,
        "url": f"http://127.0.0.1:{s.pricing_port}/a2a",
        "preferredTransport": "JSONRPC",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": SKILLS,
        "provider": {"organization": "Pricing AI Platform", "url": "http://127.0.0.1"},
        "constraints": [
            "Callers cannot set the operating mode, bypass the compliance veto, "
            "or push prices. A run is triggered; approval remains governed by the "
            "mode in force.",
            "No personalized or individual-level pricing is produced under any "
            "request.",
        ],
    }


@router.post("/a2a")
def jsonrpc(request: JsonRpcRequest) -> dict:
    """JSON-RPC entry point: `message/send` and `tasks/get`."""
    if request.method == "message/send":
        return _message_send(request)
    if request.method == "tasks/get":
        return _tasks_get(request)
    return _error(
        request.id, -32601,
        f"Method '{request.method}' not found. Supported: message/send, tasks/get.",
    )


def _extract_text(params: dict) -> str:
    message = params.get("message") or {}
    parts = message.get("parts") or []
    return " ".join(
        str(p.get("text", "")) for p in parts if isinstance(p, dict)
    ).strip()


def _message_send(request: JsonRpcRequest) -> dict:
    """Trigger a pricing run on behalf of a peer agent."""
    params = request.params
    text = _extract_text(params)
    scope_kind = params.get("scope_kind", "category")
    scope_value = params.get("scope_value")
    skus = params.get("skus") or []
    objective = params.get("objective", "balanced")

    if scope_kind == "category" and not scope_value:
        return _error(
            request.id, -32602,
            "scope_value is required for scope_kind='category'.",
            {"hint": "Send params.scope_value, e.g. 'Beverages'."},
        )
    if scope_kind == "skus" and not skus:
        return _error(request.id, -32602, "skus must be non-empty for scope_kind='skus'.")
    if objective not in ("revenue", "margin", "balanced"):
        return _error(
            request.id, -32602,
            f"Unknown objective '{objective}'. Use revenue, margin, or balanced.",
        )

    from pricing.routes.runs import _current_mode

    mode = _current_mode()
    state = RunState(
        trigger="a2a",
        scope=RunScope(scope_kind, scope_value, list(skus)),
        objective=Objective(objective),
        mode=mode,
    )
    task_id = f"task-{uuid.uuid4().hex[:12]}"
    with _lock:
        _tasks[task_id] = {
            "id": task_id, "run_id": state.run_id, "state": "working",
            "created_at": now_iso(),
        }

    with session() as conn:
        audit(conn, actor="a2a-peer", event_type="a2a_message_send",
              entity_type="task", entity_id=task_id,
              run_id=state.run_id, scope=state.scope.describe(),
              objective=objective, mode=mode.value, note=text[:200])

    thread = threading.Thread(
        target=_run_task, args=(task_id, state), daemon=True, name=f"a2a-{task_id}"
    )
    thread.start()

    logger.info("a2a.message_send", task_id=task_id, run_id=state.run_id,
                scope=state.scope.describe(), mode=mode.value)
    return _ok(
        request.id,
        {
            "id": task_id,
            "contextId": state.run_id,
            "status": {"state": "working", "timestamp": now_iso()},
            "history": [
                {
                    "role": "agent",
                    "parts": [
                        {
                            "kind": "text",
                            "text": (
                                f"Pricing run {state.run_id} accepted for "
                                f"{state.scope.describe()} on the {objective} "
                                f"objective, in {mode.value} mode. Poll tasks/get "
                                f"with id={task_id}."
                            ),
                        }
                    ],
                }
            ],
        },
    )


def _run_task(task_id: str, state: RunState) -> None:
    from pricing.routes.runs import execute_run

    try:
        finished = execute_run(state)
        outcome = "completed" if finished.status == "completed" else "failed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("a2a.task_failed", task_id=task_id)
        outcome = "failed"
        state.errors.append(f"{type(exc).__name__}: {exc}")
    with _lock:
        _tasks[task_id].update(
            state=outcome, finished_at=now_iso(), errors=list(state.errors)
        )


def _tasks_get(request: JsonRpcRequest) -> dict:
    task_id = request.params.get("id")
    if not task_id:
        return _error(request.id, -32602, "params.id is required.")
    with _lock:
        task = dict(_tasks.get(task_id, {}))
    if not task:
        return _error(request.id, -32001, f"No task '{task_id}'.")

    run = persistence.get_run(task["run_id"])
    artifacts = []
    if run and run.get("status") == "completed":
        recs = persistence.list_recommendations(run_id=task["run_id"], limit=200)
        artifacts.append(
            {
                "artifactId": f"recs-{task['run_id']}",
                "name": "recommendations",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "run_id": task["run_id"],
                            "mode": run.get("mode"),
                            "sku_count": run.get("sku_count"),
                            "recommendations": [
                                {
                                    "sku": r["sku"],
                                    "current_price": r["current_price"],
                                    "recommended_price": r["recommended_price"],
                                    "delta_pct": r["delta_pct"],
                                    "confidence": r["confidence"],
                                    "band": r["band"],
                                    "band_reason": r["band_reason"],
                                    "compliance_status": r["compliance_status"],
                                    "status": r["status"],
                                }
                                for r in recs
                            ],
                        },
                    }
                ],
            }
        )

    return _ok(
        request.id,
        {
            "id": task_id,
            "contextId": task["run_id"],
            "status": {
                "state": task.get("state", "unknown"),
                "timestamp": task.get("finished_at") or task.get("created_at"),
            },
            "artifacts": artifacts,
            "errors": task.get("errors", []),
        },
    )
