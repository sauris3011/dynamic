"""Operations: health, runtime config, telemetry, cache stats (W8, FR-065 .. FR-067)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pricing import __version__
from pricing.clients.commerce import CommerceClient
from pricing.config import OperatingMode, get_settings
from pricing.core import telemetry
from pricing.core.logging import get_logger
from pricing.core.tls import tls_status
from pricing.db import cache_db
from pricing.db.app_db import audit, get_setting, session, set_setting

logger = get_logger("pricing.routes.ops")
router = APIRouter(prefix="/api", tags=["ops"])


class ModeRequest(BaseModel):
    mode: OperatingMode
    actor: str = Field("operator", max_length=80)


class ThresholdRequest(BaseModel):
    min_confidence: float | None = Field(None, ge=0.0, le=1.0)
    max_delta_pct: float | None = Field(None, gt=0.0, le=100.0)
    max_variance: float | None = Field(None, gt=0.0)
    margin_buffer_pct: float | None = Field(None, ge=0.0, le=50.0)
    actor: str = Field("operator", max_length=80)


class GatewayRequest(BaseModel):
    """Settings drawer (FR-066).

    The API key is held in process memory only and never written to disk
    (NFR-011), so it does not survive a restart. That is deliberate.
    """

    gateway_url: str | None = None
    api_key: str | None = None
    allow_insecure_tls: bool | None = None
    actor: str = Field("operator", max_length=80)


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    try:
        with CommerceClient() as client:
            commerce = client.health()
        commerce_ok = commerce.get("status") == "ok"
    except Exception as exc:
        commerce, commerce_ok = {"error": type(exc).__name__}, False

    return {
        "status": "ok" if commerce_ok else "degraded",
        "service": "pricing-platform",
        "version": __version__,
        "commerce": commerce,
        "tls": tls_status(settings),
        "mode": _stored_mode().value,
    }


def _stored_mode() -> OperatingMode:
    with session() as conn:
        stored = get_setting(conn, "operating_mode")
    if stored:
        try:
            return OperatingMode(stored)
        except ValueError:
            pass
    return get_settings().operating_mode


@router.get("/config")
def get_config() -> dict:
    """Current runtime configuration. Secrets are never returned (NFR-011)."""
    s = get_settings()
    with session() as conn:
        overrides = {
            k: get_setting(conn, k)
            for k in ("min_confidence", "max_delta_pct", "max_variance",
                      "margin_buffer_pct", "gateway_url")
        }
    return {
        "mode": _stored_mode().value,
        "modes_available": [m.value for m in OperatingMode],
        "thresholds": {
            "min_confidence": float(overrides["min_confidence"] or s.band_min_confidence),
            "max_delta_pct": float(overrides["max_delta_pct"] or s.band_max_delta_pct),
            "max_variance": float(overrides["max_variance"] or s.band_max_variance),
            "margin_buffer_pct": float(
                overrides["margin_buffer_pct"] or s.band_margin_buffer_pct
            ),
        },
        "gateway_url": overrides["gateway_url"] or s.llm_gateway_url,
        "api_key_set": bool(s.llm_gateway_api_key),
        "tls": tls_status(s),
        "monte_carlo": {"iterations": s.mc_iterations, "seed": s.mc_seed},
        "chunking": {
            "strategy": s.chunk_strategy,
            "chunk_size": s.chunk_size,
            "chunk_overlap": s.chunk_overlap,
        },
        "competitor_bias": s.competitor_bias_map,
        "embedding_model": s.model_embedding or None,
        "models": {
            "router": s.model_router, "narrator": s.model_narrator,
            "analyst": s.model_analyst, "strategist": s.model_strategist,
        },
        "ports": {"commerce": s.commerce_port, "pricing": s.pricing_port,
                  "ui": s.ui_port},
    }


@router.put("/config/mode")
def set_mode(payload: ModeRequest) -> dict:
    """Change the operating mode at runtime (FR-101, FR-103)."""
    previous = _stored_mode()
    with session() as conn:
        set_setting(conn, "operating_mode", payload.mode.value)
        audit(conn, actor=payload.actor, event_type="mode_changed",
              entity_type="config", entity_id="operating_mode",
              previous=previous.value, current=payload.mode.value)
    logger.info("ops.mode_changed", previous=previous.value,
                current=payload.mode.value, actor=payload.actor)
    return {"mode": payload.mode.value, "previous": previous.value}


@router.put("/config/thresholds")
def set_thresholds(payload: ThresholdRequest) -> dict:
    """Band thresholds are runtime-editable (FR-100)."""
    changed = {}
    with session() as conn:
        for key in ("min_confidence", "max_delta_pct", "max_variance",
                    "margin_buffer_pct"):
            value = getattr(payload, key)
            if value is not None:
                set_setting(conn, key, str(value))
                changed[key] = value
        if changed:
            audit(conn, actor=payload.actor, event_type="thresholds_changed",
                  entity_type="config", entity_id="bands", **changed)
    return {"changed": changed}


@router.put("/config/gateway")
def set_gateway(payload: GatewayRequest) -> dict:
    """Update gateway settings from the UI drawer.

    The URL and TLS flag persist; the API key does not touch disk.
    """
    s = get_settings()
    with session() as conn:
        if payload.gateway_url:
            set_setting(conn, "gateway_url", payload.gateway_url)
        if payload.allow_insecure_tls is not None:
            s.allow_insecure_tls = payload.allow_insecure_tls
        if payload.api_key:
            s.llm_gateway_api_key = payload.api_key      # memory only
        audit(conn, actor=payload.actor, event_type="gateway_config_changed",
              entity_type="config", entity_id="gateway",
              url_set=bool(payload.gateway_url),
              key_set=bool(payload.api_key),
              insecure_tls=payload.allow_insecure_tls)
    return {"gateway_url": payload.gateway_url or s.llm_gateway_url,
            "api_key_set": bool(s.llm_gateway_api_key),
            "tls": tls_status(s)}


@router.get("/telemetry/status")
def telemetry_status() -> dict:
    """Which trace sinks are live (D12, NFR-028).

    The local JSONL sink is unconditional; Langfuse is opportunistic. This
    endpoint exists so an operator can see at a glance whether SaaS egress is
    working without inferring it from missing dashboards.
    """
    return telemetry.status()


@router.get("/cache/stats")
def cache_stats() -> dict:
    """Cache hit/miss ratios for the settings drawer (FR-066, FR-073)."""
    return cache_db.stats()


@router.delete("/cache")
def clear_cache() -> dict:
    cache_db.clear()
    return {"cleared": True}


@router.get("/telemetry/live")
def telemetry_live() -> dict:
    """Header monitor feed (FR-065).

    Token counts and cost come from the backend because the client must never
    contact the gateway directly (FR-072).
    """
    with session() as conn:
        totals = conn.execute(
            "SELECT COALESCE(SUM(tokens_in),0) AS tin,"
            " COALESCE(SUM(tokens_out),0) AS tout,"
            " COALESCE(SUM(cost_usd),0) AS cost, COUNT(*) AS runs FROM runs"
        ).fetchone()
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE status = 'running'"
        ).fetchone()["n"]
    stats = cache_db.stats()
    return {
        "active_llm_calls": active,
        "tokens_in": totals["tin"],
        "tokens_out": totals["tout"],
        "tokens_total": totals["tin"] + totals["tout"],
        "estimated_cost_usd": round(totals["cost"], 4),
        "runs": totals["runs"],
        "cache": stats,
    }


@router.get("/audit")
def audit_log(limit: int = 200, automatic_only: bool = False) -> list[dict]:
    """Audit trail. `automatic_only` filters to system-approved decisions so a
    reviewer can inspect exactly what ran without a human (FR-114)."""
    clause = "WHERE actor = 'system'" if automatic_only else ""
    with session() as conn:
        rows = conn.execute(
            f"SELECT ts, actor, event_type, entity_type, entity_id, detail_json"
            f" FROM audit_log {clause} ORDER BY ts DESC LIMIT ?",
            (min(limit, 2000),),
        ).fetchall()
    return [dict(r) for r in rows]
