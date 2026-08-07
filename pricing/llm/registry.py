"""Role-to-model registry with a boot-time gateway probe (D4, FR-004).

Model IDs are never hardcoded. Roles map to aliases resolved from configuration,
and those aliases are verified against the gateway before anything depends on
them. If a configured alias does not exist, startup says so and lists what is
actually available — rather than failing later, deep inside an agent run, with
an opaque 404 from the provider.
"""

from __future__ import annotations

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
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
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
                        return models
            except Exception:
                continue
    return []


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
