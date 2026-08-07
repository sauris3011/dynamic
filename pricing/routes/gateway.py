"""LLM gateway and per-agent model configuration (FR-004, FR-066, D4).

Split from `ops.py` because these endpoints share a subject the rest of
operations does not: they all read or mutate how this platform talks to the
gateway, and all of them have to invalidate the caches computed against the
previous answer.

Three capabilities, deliberately distinct:

* **Persisted settings.** URL, TLS posture and API key are written to the app
  database so they survive a restart (`pricing.core.runtime_config`), the key
  encrypted at rest (`pricing.core.secrets`).
* **A live model list.** The per-agent dropdowns are populated from the
  gateway's own listing endpoint rather than a list in this repository. D4
  forbids hardcoded model IDs; a hardcoded *menu* of them is the same mistake
  one level up.
* **Tests.** One for the gateway, one per agent. They are separate because they
  answer different questions — see `test_model` below.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pricing.config import get_settings
from pricing.core import runtime_config, secrets
from pricing.core.logging import get_logger
from pricing.core.tls import tls_status
from pricing.db.app_db import audit, session
from pricing.llm import registry

logger = get_logger("pricing.routes.gateway")
router = APIRouter(prefix="/api/config", tags=["gateway"])


class GatewayRequest(BaseModel):
    """Settings drawer (FR-066).

    Both the URL and the key persist across restarts: they are written to the
    app database, the key encrypted with a machine-local key file it is not
    stored beside (`pricing.core.secrets`). The key still never travels back to
    the browser — `/api/config` reports only whether one is set and an
    eight-character fingerprint (FR-072, NFR-011).
    """

    gateway_url: str | None = None
    api_key: str | None = None
    allow_insecure_tls: bool | None = None
    actor: str = Field("operator", max_length=80)


class ModelAssignmentRequest(BaseModel):
    """Per-agent model selection (D4, FR-004).

    Every field is optional so the drawer can save one dropdown without
    restating the rest. `embeddings` accepts "" — meaning no gateway embedding
    model, fall back to the local strategies — which is a real choice rather
    than an omission, so it is distinguished from `None`.
    """

    router: str | None = None
    narrator: str | None = None
    analyst: str | None = None
    strategist: str | None = None
    embeddings: str | None = None
    actor: str = Field("operator", max_length=80)


class ConnectionTestRequest(BaseModel):
    """Test a gateway before committing to it.

    `gateway_url`/`api_key` are the *unsaved* values typed into the drawer, so
    an operator can find out whether a URL works before it becomes the one the
    platform runs on. Omitted means "test what is currently configured".
    """

    gateway_url: str | None = None
    api_key: str | None = None


class ModelTestRequest(ConnectionTestRequest):
    """Round-trip one model. `role` selects the endpoint: the embeddings role
    is tested against /v1/embeddings, everything else against chat."""

    role: str = Field("analyst", max_length=40)
    model: str | None = None


@router.put("/gateway")
def set_gateway(payload: GatewayRequest) -> dict:
    """Update gateway settings from the UI drawer, durably.

    URL, TLS flag and API key all persist to the app database and are
    re-applied at the next boot; the key is encrypted at rest. Everything takes
    effect immediately — which is only true because the caches computed against
    the old settings are dropped here. Without that the save appears to work
    and the platform keeps talking to the previous gateway until a restart.
    """
    s = get_settings()

    if payload.gateway_url:
        runtime_config.persist("llm_gateway_url", payload.gateway_url.strip())
    if payload.allow_insecure_tls is not None:
        runtime_config.persist("allow_insecure_tls", payload.allow_insecure_tls)
    if payload.api_key is not None:
        # "" is meaningful: it clears the stored credential.
        runtime_config.persist_api_key(payload.api_key.strip())

    runtime_config.invalidate_llm_caches(embeddings=True)

    with session() as conn:
        audit(conn, actor=payload.actor, event_type="gateway_config_changed",
              entity_type="config", entity_id="gateway",
              url_set=bool(payload.gateway_url),
              key_set=bool(payload.api_key),
              insecure_tls=payload.allow_insecure_tls,
              persisted=True)

    probe = registry.probe_gateway()
    return {
        "gateway_url": s.llm_gateway_url,
        "api_key_set": bool(s.llm_gateway_api_key),
        "api_key_fingerprint": secrets.fingerprint(s.llm_gateway_api_key),
        "persisted": True,
        "tls": tls_status(s),
        "gateway": {"reachable": probe.reachable, "detail": probe.summary()},
    }


@router.get("/models")
def available_models() -> dict:
    """The gateway's live model list, plus what each agent is set to (D4).

    This is what makes the drawer's per-agent dropdowns dynamic: the options
    come from the gateway rather than from a list in this repository that would
    go stale the moment a deployment changes. Uncached on purpose — an operator
    who just added a model to the gateway expects to see it.
    """
    s = get_settings()
    listing = registry.list_models()
    assigned = {
        role: getattr(s, field) for role, field in runtime_config.ROLE_FIELDS.items()
    }
    available = set(listing["models"])
    return {
        **listing,
        "roles": assigned,
        # Assigned aliases the gateway does not list. Surfaced rather than
        # silently dropped from the dropdown, because an unlisted alias is
        # exactly the failure D4's boot probe exists to catch.
        "unknown": {
            role: alias for role, alias in assigned.items()
            if alias and available and alias not in available
        },
    }


@router.put("/models")
def set_models(payload: ModelAssignmentRequest) -> dict:
    """Assign models to agent roles. Persisted, applied immediately.

    Unlisted aliases are accepted with a warning rather than rejected: the
    listing endpoint can be unreachable or incomplete, and refusing to save a
    valid model because the gateway would not enumerate it would make the
    drawer unusable exactly when it is most needed. The warning names the
    mismatch, and the boot probe reports it again on every restart.
    """
    listing = registry.list_models()
    available = set(listing["models"])

    changed: dict[str, str] = {}
    warnings: list[str] = []
    embeddings_changed = False

    for role, field_name in runtime_config.ROLE_FIELDS.items():
        value = getattr(payload, role)
        if value is None:
            continue
        alias = value.strip()
        if alias and available and alias not in available:
            warnings.append(
                f"'{alias}' for {role} is not listed by the gateway. Saved "
                "anyway — calls with it will fail if it does not exist."
            )
        if alias == getattr(get_settings(), field_name):
            continue
        runtime_config.persist(field_name, alias)
        changed[role] = alias
        embeddings_changed = embeddings_changed or role == "embeddings"

    if changed:
        runtime_config.invalidate_llm_caches(embeddings=embeddings_changed)
        with session() as conn:
            audit(conn, actor=payload.actor, event_type="model_assignment_changed",
                  entity_type="config", entity_id="models", **changed)
        logger.info("ops.models_changed", changed=changed, actor=payload.actor)

    if embeddings_changed:
        # A Chroma collection is fixed to the width of the vectors first
        # written into it, and vectors from two models are not comparable even
        # at equal width (see rag/store.py). Saying so here is the difference
        # between a known re-ingest and a retrieval quality mystery.
        warnings.append(
            "The embedding model changed. Existing RAG collections were built "
            "with the previous one and must be reset and re-ingested before "
            "retrieval is trustworthy."
        )

    return {"changed": changed, "warnings": warnings,
            "roles": {role: getattr(get_settings(), field)
                      for role, field in runtime_config.ROLE_FIELDS.items()}}


@router.post("/gateway/test")
def test_gateway(payload: ConnectionTestRequest) -> dict:
    """Reachability check against a gateway, saved or not (FR-066).

    Answers the only question that matters before saving: does this URL, with
    this key, return a model list? The models come back too, so the drawer can
    populate its dropdowns from an unsaved gateway.
    """
    result = registry.list_models(payload.gateway_url, payload.api_key)
    logger.info("ops.gateway_tested", url=result["gateway_url"],
                reachable=result["reachable"], models=len(result["models"]))
    return {
        **result,
        "detail": (
            f"Connected — {len(result['models'])} model(s) available "
            f"via {result['endpoint']}."
            if result["reachable"]
            else f"Could not list models. {result['error'] or 'No response.'}"
        ),
    }


@router.post("/models/test")
def test_model(payload: ModelTestRequest) -> dict:
    """Round-trip one agent's model and report what came back (FR-066).

    Distinct from the connection test on purpose. The gateway listing an alias
    proves only that it is configured there; it says nothing about whether the
    upstream credential behind it works or whether this key is entitled to it.
    Only a real request answers that, so this makes one.
    """
    role = payload.role.strip().lower()
    if role not in runtime_config.ROLE_FIELDS:
        raise HTTPException(
            422,
            f"Unknown role '{payload.role}'. Known roles: "
            f"{sorted(runtime_config.ROLE_FIELDS)}",
        )

    alias = payload.model
    if alias is None:
        alias = getattr(get_settings(), runtime_config.ROLE_FIELDS[role])

    result = registry.test_model(
        alias.strip(), role=role,
        base_url=payload.gateway_url, api_key=payload.api_key,
    )
    logger.info("ops.model_tested", role=role, model=alias, ok=result["ok"],
                latency_ms=result["latency_ms"])
    return result
