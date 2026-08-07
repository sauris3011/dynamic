"""Continuous loop mode (W9, FR-121 .. FR-127, NFR-035, NFR-036).

**Why this does not violate the zero-daemon constraint (NFR-001).** This is a
daemon *thread inside the application process*, not a system service. It installs
nothing, requires no privileges, cannot be started by anything other than an
explicit UI action, and dies with the process that owns it. What NFR-001
prohibits is a service living outside the application; this lives inside it.

Two safety properties, both deliberate:

* **It never auto-starts** (FR-122). There is no code path that begins looping
  at boot — a human turns it on every time.
* **A failed iteration stops the loop** (FR-124, NFR-036). It does not retry
  into a broken state or keep pricing on inputs that already failed once. A loop
  that silently limps along is worse than one that stops loudly.
"""

from __future__ import annotations

import threading
import time

from pricing.analytics.optimizer import Objective
from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, now_iso, session
from pricing.pipeline.state import RunScope, RunState

logger = get_logger("pricing.services.loop")

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _read_state() -> dict:
    with session() as conn:
        row = conn.execute("SELECT * FROM loop_state WHERE id = 1").fetchone()
    return dict(row) if row else {}


def _write_state(**fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    with session() as conn:
        conn.execute(
            f"UPDATE loop_state SET {assignments} WHERE id = 1", list(fields.values())
        )


def status() -> dict:
    state = _read_state()
    running = bool(state.get("running")) and _thread is not None and _thread.is_alive()
    return {
        "running": running,
        "interval_seconds": state.get("interval_seconds", 300),
        "iterations": state.get("iterations", 0),
        "started_at": state.get("started_at"),
        "stopped_at": state.get("stopped_at"),
        "last_run_id": state.get("last_run_id"),
        "last_error": state.get("last_error"),
        "scope": state.get("scope_json", "{}"),
    }


def _iteration(scope: RunScope, objective: Objective) -> str:
    """Run one full pipeline pass, honouring the autonomy bands in force."""
    from pricing.routes.runs import _current_mode, execute_run

    state = RunState(
        trigger="loop", scope=scope, objective=objective, mode=_current_mode()
    )
    execute_run(state)
    if state.status == "failed":
        raise RuntimeError(
            f"Iteration {state.run_id} failed: {'; '.join(state.errors) or 'unknown'}"
        )
    return state.run_id


def _worker(interval: int, scope: RunScope, objective: Objective, max_iterations: int) -> None:
    iterations = 0
    while not _stop_event.is_set():
        try:
            run_id = _iteration(scope, objective)
            iterations += 1
            _write_state(iterations=iterations, last_run_id=run_id, last_error=None)
            logger.info("loop.iteration", iteration=iterations, run_id=run_id)
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            logger.error("loop.iteration_failed", error=message)
            _write_state(running=0, stopped_at=now_iso(), last_error=message)
            with session() as conn:
                audit(conn, actor="system", event_type="loop_failed",
                      entity_type="loop", entity_id="1", error=message)
            return  # FR-124: stop, do not retry into a broken state

        if max_iterations and iterations >= max_iterations:
            logger.info("loop.max_iterations", iterations=iterations)
            _write_state(running=0, stopped_at=now_iso(),
                         last_error=f"Reached max_iterations={max_iterations}")
            return

        # Sleep in slices so a stop request is honoured promptly rather than
        # after a full interval.
        waited = 0.0
        while waited < interval and not _stop_event.is_set():
            time.sleep(min(1.0, interval - waited))
            waited += 1.0

    _write_state(running=0, stopped_at=now_iso())
    logger.info("loop.stopped", iterations=iterations)


def start(
    interval_seconds: int | None = None,
    scope_kind: str = "category",
    scope_value: str | None = None,
    skus: list[str] | None = None,
    objective: str = "balanced",
    actor: str = "operator",
) -> dict:
    """Start the loop. Requires an explicit call — never invoked at boot."""
    global _thread
    settings = get_settings()

    with _lock:
        if _thread is not None and _thread.is_alive():
            return {"started": False, "detail": "Loop is already running.",
                    **status()}

        interval = int(interval_seconds or settings.loop_default_interval_seconds)
        interval = max(30, min(interval, 86400))
        scope = RunScope(scope_kind, scope_value, skus or [])

        _stop_event.clear()
        _write_state(
            running=1, interval_seconds=interval, started_at=now_iso(),
            stopped_at=None, iterations=0, last_error=None,
            scope_json=__import__("json").dumps(
                {"kind": scope_kind, "value": scope_value, "skus": skus or []}
            ),
        )
        with session() as conn:
            audit(conn, actor=actor, event_type="loop_started",
                  entity_type="loop", entity_id="1",
                  interval_seconds=interval, scope=scope.describe())

        _thread = threading.Thread(
            target=_worker,
            args=(interval, scope, Objective(objective), settings.loop_max_iterations),
            daemon=True,       # dies with the process (NFR-035)
            name="pricing-loop",
        )
        _thread.start()

    logger.info("loop.started", interval=interval, scope=scope.describe(), actor=actor)
    return {"started": True, **status()}


def stop(actor: str = "operator") -> dict:
    """Stop the loop. Safe to call when it is not running."""
    global _thread
    with _lock:
        was_running = _thread is not None and _thread.is_alive()
        _stop_event.set()
        thread = _thread

    if thread is not None:
        thread.join(timeout=10.0)

    with _lock:
        _thread = None
    _write_state(running=0, stopped_at=now_iso())

    if was_running:
        with session() as conn:
            audit(conn, actor=actor, event_type="loop_stopped",
                  entity_type="loop", entity_id="1")
        logger.info("loop.stop_requested", actor=actor)
    return {"stopped": True, "was_running": was_running, **status()}
