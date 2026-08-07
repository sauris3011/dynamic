"""Gateway and model settings that survive a restart (FR-066, FR-067, D4).

`.env` states the *defaults*; this module holds what the operator changed since.
Overrides live in `settings_kv` in the app database and are re-applied onto the
`Settings` singleton at boot, before anything reads it. The precedence is
therefore: persisted override > environment > field default.

Two things follow from that ordering and are worth stating, because both are
easy to trip over:

* An operator who edits `.env` after having changed the same value in the UI
  will not see their edit take effect. `clear_override()` exists for that, and
  `/api/config` reports which values are overridden so the situation is
  visible rather than mysterious.
* The `Settings` object is mutated in place rather than rebuilt. Rebuilding
  would invalidate the `lru_cache` identity that half the codebase holds a
  reference to; mutation keeps every existing reference correct.

The API key is stored encrypted (see `pricing.core.secrets`) — persisting it is
the point of this module, and persisting it in the clear would not be.
"""

from __future__ import annotations

from pricing.config import Settings, get_settings
from pricing.core import secrets
from pricing.core.logging import get_logger
from pricing.db.app_db import get_setting, session, set_setting

logger = get_logger("pricing.core.runtime_config")

# Agent role -> the `Settings` field carrying its model alias (D4). The keys are
# the role names the UI shows; `embeddings` is included because it is chosen the
# same way, even though it is not a chat agent.
ROLE_FIELDS: dict[str, str] = {
    "router": "model_router",
    "narrator": "model_narrator",
    "analyst": "model_analyst",
    "strategist": "model_strategist",
    "embeddings": "model_embedding",
}

# Settings field -> settings_kv key. Only these are persistable from the UI;
# anything else has to go through `.env`, which keeps the blast radius of the
# settings drawer small and auditable.
_PERSISTED: dict[str, str] = {
    "llm_gateway_url": "gateway_url",
    "allow_insecure_tls": "gateway_allow_insecure_tls",
    **{field: f"model.{role}" for role, field in ROLE_FIELDS.items()},
}

_API_KEY_KEY = "gateway_api_key_encrypted"

_BOOLEAN_FIELDS = {"allow_insecure_tls"}

# "" already means "no override stored", so a deliberately empty value needs a
# distinct encoding. Only `model_embedding` has one — empty there means "no
# gateway embeddings, use the local strategies" (config.py), which an operator
# must be able to *choose*, not merely inherit from `.env`.
_EXPLICIT_EMPTY = "__none__"


# --- Reading ---------------------------------------------------------------


def stored_overrides() -> dict[str, str]:
    """Every persisted override, by `Settings` field name. Never the key.

    An empty stored value counts as *no* override rather than as the empty
    string, which is what makes `clear_override()` a one-line write instead of
    a delete plus a re-read.
    """
    with session() as conn:
        return {
            field: value
            for field, key in _PERSISTED.items()
            if (value := get_setting(conn, key))
        }


def stored_api_key() -> str:
    """The persisted gateway key, decrypted. "" when none is stored."""
    with session() as conn:
        blob = get_setting(conn, _API_KEY_KEY)
    return secrets.decrypt(blob) if blob else ""


def apply_persisted(settings: Settings | None = None) -> dict[str, str]:
    """Overlay persisted overrides onto the settings singleton.

    Called once at startup and again after every change, so a running process
    and a freshly restarted one resolve configuration identically. Returns what
    was applied, for the startup log.
    """
    s = settings or get_settings()
    applied: dict[str, str] = {}

    for field, value in stored_overrides().items():
        if field in _BOOLEAN_FIELDS:
            setattr(s, field, value.strip().lower() in ("1", "true", "yes", "on"))
        elif value == _EXPLICIT_EMPTY:
            setattr(s, field, "")
        else:
            setattr(s, field, value)
        applied[field] = value

    key = stored_api_key()
    if key:
        s.llm_gateway_api_key = key
        applied["llm_gateway_api_key"] = "<encrypted>"

    if applied:
        logger.info("runtime_config.applied", fields=sorted(applied))
    return applied


# --- Writing ---------------------------------------------------------------


def persist(field: str, value: object) -> None:
    """Persist one override and apply it immediately."""
    if field not in _PERSISTED:
        raise KeyError(
            f"'{field}' is not persistable from the UI. Persistable fields: "
            f"{sorted(_PERSISTED)}"
        )
    if field in _BOOLEAN_FIELDS:
        encoded = "true" if value else "false"
    else:
        encoded = str(value) or _EXPLICIT_EMPTY
    with session() as conn:
        set_setting(conn, _PERSISTED[field], encoded)
    setattr(get_settings(), field, value)


def persist_api_key(api_key: str) -> None:
    """Store the gateway key encrypted, and load it into the running process.

    An empty string clears it — the way to remove a stored credential without
    reaching into the database by hand.
    """
    with session() as conn:
        set_setting(conn, _API_KEY_KEY, secrets.encrypt(api_key) if api_key else "")
    get_settings().llm_gateway_api_key = api_key
    logger.info("runtime_config.api_key_stored", cleared=not api_key,
                fingerprint=secrets.fingerprint(api_key))


def clear_override(field: str) -> None:
    """Drop a persisted override so `.env` governs again after the next restart."""
    if field == "llm_gateway_api_key":
        persist_api_key("")
        return
    with session() as conn:
        set_setting(conn, _PERSISTED[field], "")
    logger.info("runtime_config.override_cleared", field=field)


# --- Cache invalidation ----------------------------------------------------


def invalidate_llm_caches(*, embeddings: bool = False) -> None:
    """Drop every cache that was computed against the old gateway settings.

    Without this a URL or key change appears to save and then changes nothing:
    the boot-time gateway probe, the advertised cost table and the constructed
    chat clients are all cached for the process lifetime and would keep using
    the previous credentials until a restart — which is precisely what these
    settings are supposed to avoid.

    `embeddings=True` additionally drops the built embedding function. Kept
    separate because rebuilding it re-runs a warmup call against the gateway,
    and there is no reason to pay for that when only a chat model changed.
    """
    from pricing.llm import costs, registry

    registry.probe_gateway.cache_clear()
    costs.gateway_rates.cache_clear()

    try:
        from pricing.llm import grounded

        grounded._model_cache.clear()
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_config.model_cache_clear_failed", error=str(exc))

    if embeddings:
        try:
            from pricing.rag import store as rag_store

            rag_store._embedding_fn.cache_clear()
        except Exception as exc:  # noqa: BLE001
            logger.debug("runtime_config.embedding_cache_clear_failed", error=str(exc))

    logger.info("runtime_config.caches_invalidated", embeddings=embeddings)
