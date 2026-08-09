"""Live progress for a running pipeline.

In-memory and ephemeral by design: this is display state, and the durable record
of every run is in app.db. It lives here rather than in the route module so the
pipeline can report into it without importing the API layer, and so both the
standalone and the LangGraph path report identically — they call the same stage
functions, so one instrumentation point covers both.

Why a module-level registry rather than a callback on `RunState`: `RunState`
travels through LangGraph's SQLite checkpointer, which pickles it. A callable
field would break checkpointing the moment it was set.

The contract this exists to satisfy: **a long run must never look like a hung
one.** A stage that takes six minutes has to say so while it is taking them, or
the operator is left staring at a static screen with no way to tell the
difference between working and dead.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

# Fractions of a whole run, used to turn per-stage progress into one overall
# number.
#
# These are measured, not guessed. A 98-SKU category run on this machine spent
# 0.83s in ingestion, 1.69s in the quantitative stage, and under 0.01s each in
# strategy and validation — against 457s in narration, which is 99.4% of the
# run. Narration issues up to ~37 sequential gateway calls and its cost is set
# by gateway latency, not by catalog size.
#
# The weights therefore lean hard on narration. Two consequences, both
# acceptable: on a full-catalog run the quantitative stage is under-weighted and
# the bar advances more slowly than it should through the middle; and when no
# gateway is configured narration returns instantly and the bar leaps to done.
# Finishing sooner than promised is the safe direction to be wrong in.
STAGE_WEIGHTS: dict[str, float] = {
    "data_context": 0.04,
    "quantitative": 0.12,
    "strategy": 0.02,
    "validation": 0.02,
    "narration": 0.78,
    "persisted": 0.02,
}

STAGE_ORDER = ["data_context", "quantitative", "strategy", "validation", "narration"]

# What each stage is doing, in the operator's language. Kept together so the
# progress feed reads as one voice rather than five improvised strings.
STAGE_DETAIL: dict[str, str] = {
    "data_context": "Reading the catalog, sales, stock and prices.",
    "quantitative": "Working out how much each product would sell at each price.",
    "strategy": "Choosing the best price for each product.",
    "validation": "Checking every price against the policy rules.",
    "narration": "Writing the explanation for each price.",
}

# Don't rewrite the entry on every one of N items; a poll every two seconds
# cannot see finer than this anyway.
_MIN_INTERVAL_S = 0.2

_runs: dict[str, dict] = {}
_lock = threading.Lock()
_last_write: dict[str, float] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start(run_id: str) -> None:
    """Register a run as queued. Called before the background task begins."""
    with _lock:
        _runs[run_id] = {
            "run_id": run_id,
            "status": "queued",
            "stage": "queued",
            "stages": {},
            "started_at": _now(),
            "processed": 0,
            "total": 0,
            "overall": 0.0,
            "detail": "Waiting for a worker.",
        }
        _last_write[run_id] = 0.0


def update(run_id: str, **fields) -> None:
    """Merge fields into a run's progress entry, unthrottled."""
    with _lock:
        entry = _runs.setdefault(
            run_id, {"run_id": run_id, "stages": {}, "started_at": _now()}
        )
        entry.update(fields)
        _last_write[run_id] = time.monotonic()


def stage(run_id: str, name: str, *, total: int = 0, detail: str = "") -> None:
    """Announce that a stage has begun, and how many items it will work through.

    Always written immediately — a stage transition is exactly the event the
    operator is waiting to see, so it must never be dropped by the throttle.
    """
    update(
        run_id,
        status="running",
        stage=name,
        processed=0,
        total=total,
        detail=detail,
        overall=_overall(name, 0, total),
    )


def item(run_id: str, name: str, processed: int, total: int, *, detail: str = "") -> None:
    """Report progress within a stage. Throttled; the final item always lands."""
    final = total > 0 and processed >= total
    if not final:
        with _lock:
            if time.monotonic() - _last_write.get(run_id, 0.0) < _MIN_INTERVAL_S:
                return

    update(
        run_id,
        status="running",
        stage=name,
        processed=processed,
        total=total,
        detail=detail,
        overall=_overall(name, processed, total),
    )


def finish(run_id: str, **fields) -> None:
    """Terminal update — the run is completed, halted or failed."""
    update(run_id, stage="persisted", overall=1.0, **fields)


def get(run_id: str) -> dict | None:
    with _lock:
        entry = _runs.get(run_id)
        return dict(entry) if entry else None


def forget(run_id: str) -> None:
    with _lock:
        _runs.pop(run_id, None)
        _last_write.pop(run_id, None)


def _overall(name: str, processed: int, total: int) -> float:
    """Stages already finished, plus this stage's own fraction of the whole."""
    done = 0.0
    for stage_name in STAGE_ORDER:
        if stage_name == name:
            break
        done += STAGE_WEIGHTS.get(stage_name, 0.0)

    weight = STAGE_WEIGHTS.get(name, 0.0)
    within = (processed / total) if total > 0 else 0.0
    return round(min(done + weight * min(within, 1.0), 0.99), 4)
