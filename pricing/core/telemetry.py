"""Observability: Langfuse plus an always-on local JSONL sink (D12, NFR-028).

Two sinks, deliberately:

* **The local sink is unconditional.** It writes newline-delimited JSON to
  `data/traces.jsonl` and depends on nothing but the filesystem. Behind a
  corporate proxy, SaaS egress is the *most* likely thing to fail, and a tracing
  strategy that evaporates exactly when the network misbehaves is worse than
  useless — that is when you need the trace.
* **Langfuse is opportunistic.** It activates only when both keys are configured
  and the SDK imports. If it fails at any point it is disabled for the rest of
  the process rather than retried per call.

**Telemetry is never on the critical path (NFR-025).** Every function here
swallows its own exceptions. A pricing run that fails because a trace could not
be written would be an absurd trade, and the sequencing makes that impossible:
the local write happens first, the remote emit second, and neither can raise into
a caller.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from pricing.config import get_settings
from pricing.core.logging import _scrub_mapping, get_logger

logger = get_logger("pricing.core.telemetry")

_write_lock = threading.Lock()
_langfuse_disabled = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Local sink -----------------------------------------------------------

def _write_local(event: dict) -> None:
    """Append one JSON line. Never raises."""
    try:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, default=str, ensure_ascii=False)
        with _write_lock:
            with settings.trace_log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("telemetry.local_sink_failed", error=str(exc))


# --- Langfuse -------------------------------------------------------------

@lru_cache(maxsize=1)
def _client() -> Any | None:
    """Build the Langfuse client once, or return None and stay quiet."""
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_host,
        )
        logger.info("telemetry.langfuse_enabled", host=settings.langfuse_host)
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "telemetry.langfuse_unavailable",
            error=f"{type(exc).__name__}: {exc}",
            detail="Local JSONL sink remains active; tracing is not lost.",
        )
        return None


def langchain_config(
    *, run_id: str | None = None, user_id: str = "operator", tags: list[str] | None = None,
) -> dict[str, Any]:
    """Return the current Langfuse callback configuration for one LC invocation.

    Langfuse v3+ receives per-run attributes through LangChain's invocation
    metadata. This keeps the callback tied to the explicitly configured client
    while grouping all model calls from a pricing run in one Langfuse session.
    """
    if _langfuse_disabled or _client() is None:
        return {}
    try:
        from langfuse.langchain import CallbackHandler

        metadata: dict[str, Any] = {
            "langfuse_user_id": user_id,
            "langfuse_tags": ["pricing", *(tags or [])],
        }
        if run_id:
            metadata["langfuse_session_id"] = run_id
        return {"callbacks": [CallbackHandler()], "metadata": metadata}
    except Exception as exc:  # noqa: BLE001
        logger.debug("telemetry.handler_unavailable", error=str(exc))
        return {}


def _emit_remote(kind: str, event: dict) -> None:
    global _langfuse_disabled
    if _langfuse_disabled:
        return
    client = _client()
    if client is None:
        return
    try:
        # The SDK surface has moved between major versions; probe rather than
        # pin, and fall back to the local sink alone if neither shape exists.
        if hasattr(client, "create_event"):
            client.create_event(name=kind, metadata=event)
        elif hasattr(client, "event"):
            client.event(name=kind, metadata=event)
        else:
            raise AttributeError("No event API on the Langfuse client.")
    except Exception as exc:  # noqa: BLE001
        _langfuse_disabled = True
        logger.warning(
            "telemetry.langfuse_disabled",
            error=f"{type(exc).__name__}: {exc}",
            detail="Remote export disabled for this process. Local sink continues.",
        )


def record(kind: str, **fields: Any) -> None:
    """Record one telemetry event to both sinks.

    Fields pass through the same redaction processor the logs use, so a secret
    cannot reach a trace by a route the logger would have blocked (NFR-012).
    """
    event = _scrub_mapping({"ts": _now(), "kind": kind, **fields})
    _write_local(event)
    _emit_remote(kind, event)


def record_llm_call(
    *, role: str, model: str, run_id: str | None, cache_hit: str,
    tokens_in: int, tokens_out: int, latency_ms: int, repaired: bool,
    citations: int, error: str = "", cost_usd: float = 0.0,
    cost_source: str = "",
) -> None:
    """Per-call LLM telemetry (NFR-027, NFR-029).

    `cost_source` travels with the figure so a quoted gateway rate is never
    confused with an assumed fallback when the trace is read later.
    """
    record(
        "llm_call", role=role, model=model, run_id=run_id, cache=cache_hit,
        tokens_in=tokens_in, tokens_out=tokens_out, tokens_total=tokens_in + tokens_out,
        latency_ms=latency_ms, repaired=repaired, citations=citations,
        cost_usd=cost_usd, cost_source=cost_source or None,
        error=error or None,
    )


def record_stage(
    *, run_id: str, stage: str, seconds: float, **fields: Any
) -> None:
    """Per-agent-stage telemetry, attributable per run (NFR-029)."""
    record("stage", run_id=run_id, stage=stage, seconds=round(seconds, 3), **fields)


def flush() -> None:
    """Best-effort flush at shutdown (NFR-023)."""
    client = _client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001
        logger.debug("telemetry.flush_failed", error=str(exc))


def status() -> dict:
    """What the UI shows about tracing."""
    settings = get_settings()
    trace_path = settings.trace_log_path
    lines = 0
    try:
        if trace_path.exists():
            with trace_path.open("r", encoding="utf-8") as handle:
                lines = sum(1 for _ in handle)
    except Exception:  # noqa: BLE001
        lines = -1
    return {
        "local_sink": {
            "path": str(trace_path),
            "active": True,
            "events": lines,
        },
        "langfuse": {
            "configured": settings.langfuse_enabled,
            "active": bool(_client()) and not _langfuse_disabled,
            "host": settings.langfuse_host if settings.langfuse_enabled else None,
        },
    }
