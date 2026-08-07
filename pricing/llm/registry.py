"""Role-to-model registry with a boot-time gateway probe (D4, FR-004).

Model IDs are never hardcoded. Roles map to aliases resolved from configuration,
and those aliases are verified against the gateway before anything depends on
them. If a configured alias does not exist, startup says so and lists what is
actually available — rather than failing later, deep inside an agent run, with
an opaque 404 from the provider.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from functools import lru_cache

import httpx

from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.core.tls import verify_option

logger = get_logger("pricing.llm.registry")

ROLES = ("router", "narrator", "analyst", "strategist")


@dataclass
class GatewayProbe:
    reachable: bool
    available_models: list[str] = field(default_factory=list)
    configured: dict[str, str] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.reachable and not self.missing

    def summary(self) -> str:
        if not self.reachable:
            return f"LLM gateway unreachable: {self.error}"
        if self.missing:
            return (
                "Configured model aliases not found on the gateway: "
                + ", ".join(f"{role}='{alias}'" for role, alias in self.missing.items())
                + ". Available: "
                + (", ".join(sorted(self.available_models)[:20]) or "none reported")
            )
        return f"Gateway OK; {len(self.available_models)} model(s) available."


def _fetch_models(base_url: str, api_key: str, verify) -> list[str]:
    """Try the LiteLLM and OpenAI-compatible listing endpoints in turn."""
    return _fetch_models_detailed(base_url, api_key, verify)[0]


def _fetch_models_detailed(
    base_url: str, api_key: str, verify
) -> tuple[list[str], str, str]:
    """As `_fetch_models`, but reporting *which* endpoint answered and why not.

    The settings drawer needs the failure text, not just an empty list: "404 on
    every listing endpoint" and "certificate verify failed" call for completely
    different fixes, and an operator staring at an empty dropdown cannot tell
    them apart.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    errors: list[str] = []
    with httpx.Client(timeout=8.0, verify=verify, headers=headers) as client:
        for path, extract in (
            ("/model/info", lambda d: [
                m.get("model_name") for m in d.get("data", []) if m.get("model_name")
            ]),
            ("/v1/models", lambda d: [m.get("id") for m in d.get("data", []) if m.get("id")]),
            ("/models", lambda d: [m.get("id") for m in d.get("data", []) if m.get("id")]),
        ):
            try:
                resp = client.get(f"{base_url.rstrip('/')}{path}")
                if resp.status_code < 300:
                    models = [m for m in extract(resp.json()) if m]
                    if models:
                        return sorted(set(models)), path, ""
                    errors.append(f"{path}: 200 but no models listed")
                else:
                    errors.append(f"{path}: HTTP {resp.status_code}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {type(exc).__name__}: {str(exc)[:120]}")
    return [], "", "; ".join(errors)


def list_models(base_url: str | None = None, api_key: str | None = None) -> dict:
    """Live model list for the settings drawer (FR-004, D4).

    Deliberately uncached and deliberately not `probe_gateway`: this is what
    populates the per-agent dropdowns, so it must reflect the gateway *now*,
    including a URL and key the operator has typed but not yet saved. Passing
    those explicitly is what makes "Test" meaningful before "Save".
    """
    s = get_settings()
    url = (base_url or s.llm_gateway_url).strip()
    key = s.llm_gateway_api_key if api_key is None else api_key

    started = time.time()
    try:
        models, endpoint, error = _fetch_models_detailed(url, key, verify_option(s))
    except Exception as exc:  # noqa: BLE001
        models, endpoint, error = [], "", f"{type(exc).__name__}: {exc}"

    return {
        "gateway_url": url,
        "reachable": bool(models),
        "models": models,
        "endpoint": endpoint,
        "error": error,
        "latency_ms": int((time.time() - started) * 1000),
    }


def test_model(
    alias: str,
    *,
    role: str = "",
    base_url: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Send one real request to a model and report what came back.

    A listing endpoint says a model is *configured*; it does not say the
    upstream credential behind it works, that the deployment is not retired,
    or that this key is entitled to it. Only a real call does — which is why
    the drawer's per-agent test issues one rather than checking membership in
    the list it just rendered.

    Kept out of `grounded.call` on purpose: no grounding, no cache write, no
    telemetry. A connectivity check must not seed the response cache, and a
    cached hit would make a broken model look healthy.
    """
    s = get_settings()
    url = (base_url or s.llm_gateway_url).strip().rstrip("/")
    key = s.llm_gateway_api_key if api_key is None else api_key
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    embedding = role == "embeddings"

    if not alias:
        return {"ok": False, "role": role, "model": alias, "latency_ms": 0,
                "detail": "No model selected for this role."}

    if embedding:
        path, payload = "/v1/embeddings", {"model": alias, "input": "ping"}
    else:
        path, payload = "/v1/chat/completions", {
            "model": alias,
            "messages": [{"role": "user", "content": "Reply with the word: ok"}],
            "max_tokens": 8,
        }

    started = time.time()
    try:
        with httpx.Client(timeout=30.0, verify=verify_option(s), headers=headers) as c:
            resp = c.post(f"{url}{path}", json=payload)
            if resp.status_code >= 300 and "max_tokens" in resp.text:
                # Newer model families renamed the field to
                # `max_completion_tokens` and reject the old one outright.
                # Retrying without any cap keeps a working model from being
                # reported as broken over a parameter name.
                payload.pop("max_tokens", None)
                resp = c.post(f"{url}{path}", json=payload)
        elapsed = int((time.time() - started) * 1000)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "role": role, "model": alias,
            "latency_ms": int((time.time() - started) * 1000),
            "detail": f"{type(exc).__name__}: {str(exc)[:300]}",
        }

    if resp.status_code >= 300:
        # The gateway wraps the provider's message; it usually names the real
        # problem ("model not found", "quota exceeded"), so pass it through.
        return {"ok": False, "role": role, "model": alias, "latency_ms": elapsed,
                "detail": f"HTTP {resp.status_code}: {resp.text[:300]}"}

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "role": role, "model": alias, "latency_ms": elapsed,
                "detail": "Gateway returned a non-JSON response."}

    if embedding:
        vectors = body.get("data") or []
        dimensions = len(vectors[0].get("embedding", [])) if vectors else 0
        if not dimensions:
            return {"ok": False, "role": role, "model": alias, "latency_ms": elapsed,
                    "detail": "Gateway returned no embedding vector."}
        return {"ok": True, "role": role, "model": alias, "latency_ms": elapsed,
                "detail": f"{dimensions}-dimensional embedding returned."}

    choices = body.get("choices") or []
    text = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""
    usage = body.get("usage") or {}
    if not text:
        return {"ok": False, "role": role, "model": alias, "latency_ms": elapsed,
                "detail": "Model responded with empty content."}
    return {
        "ok": True, "role": role, "model": alias, "latency_ms": elapsed,
        "detail": f'Replied "{text[:60]}" ({usage.get("total_tokens", 0)} tokens).',
    }


@lru_cache(maxsize=1)
def probe_gateway() -> GatewayProbe:
    """Verify the configured aliases exist. Cached — called at boot."""
    s = get_settings()
    configured = {role: s.model_for_role(role) for role in ROLES}

    try:
        models = _fetch_models(s.llm_gateway_url, s.llm_gateway_api_key,
                               verify_option(s))
    except Exception as exc:
        probe = GatewayProbe(
            reachable=False, configured=configured, error=f"{type(exc).__name__}: {exc}"
        )
        logger.warning("registry.probe_failed", error=probe.error)
        return probe

    if not models:
        probe = GatewayProbe(
            reachable=False, configured=configured,
            error=f"No model list returned by {s.llm_gateway_url}",
        )
        logger.warning("registry.no_models", url=s.llm_gateway_url)
        return probe

    available = set(models)
    missing = {
        role: alias for role, alias in configured.items() if alias not in available
    }
    probe = GatewayProbe(
        reachable=True, available_models=sorted(available),
        configured=configured, missing=missing,
    )
    if missing:
        # Loud, and it names the real alternatives (D4).
        logger.error("registry.missing_models", missing=missing,
                     available=sorted(available)[:20])
    else:
        logger.info("registry.ok", models=len(available), configured=configured)
    return probe


def resolve(role: str) -> str:
    """Model alias for a role, falling back to `analyst` for unknown roles."""
    s = get_settings()
    try:
        return s.model_for_role(role)
    except KeyError:
        logger.warning("registry.unknown_role", role=role)
        return s.model_analyst
