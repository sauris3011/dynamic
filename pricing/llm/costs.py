"""Token cost estimation (FR-065, NFR-029).

The header monitor promises an estimated spend, and an estimate that is always
zero is worse than no estimate: it reads as "this costs nothing" rather than
"this is not measured".

Rates come from the gateway itself. LiteLLM publishes `input_cost_per_token`
and `output_cost_per_token` per model on `/model/info`, which makes the numbers
current and specific to the deployment rather than a table in this repository
that silently goes stale. Model IDs stay out of the source either way (D4).

When a model advertises no rate, a configured fallback applies and the result is
labelled `fallback` so nobody mistakes an assumption for a quote. That labelling
is the point — a cost figure whose provenance is unknown cannot be defended in a
review, and this system is meant to be defensible.

Estimation never fails a call: any error yields a zero-cost, `unknown`-sourced
rate rather than propagating. Telemetry is not on the critical path (NFR-025).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pricing.config import get_settings
from pricing.core.logging import get_logger

logger = get_logger("pricing.llm.costs")

# LiteLLM's own field names, checked in this order.
INPUT_FIELDS = ("input_cost_per_token", "input_cost_per_token_batches")
OUTPUT_FIELDS = ("output_cost_per_token", "output_cost_per_token_batches")


@dataclass(frozen=True)
class Rate:
    """Cost per single token, plus where the number came from."""

    input_per_token: float
    output_per_token: float
    source: str          # gateway | fallback | unknown

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return tokens_in * self.input_per_token + tokens_out * self.output_per_token


def _fallback_rate() -> Rate:
    s = get_settings()
    return Rate(
        input_per_token=s.llm_fallback_input_cost_per_mtok / 1_000_000.0,
        output_per_token=s.llm_fallback_output_cost_per_mtok / 1_000_000.0,
        source="fallback",
    )


@lru_cache(maxsize=1)
def gateway_rates() -> dict[str, Rate]:
    """Per-model rates advertised by the gateway. Cached for the process.

    Returns an empty map on any failure — an unreachable gateway is already
    reported by the registry probe, and repeating that here would only add
    noise to the logs.
    """
    import httpx

    from pricing.core.tls import verify_option

    s = get_settings()
    headers = (
        {"Authorization": f"Bearer {s.llm_gateway_api_key}"}
        if s.llm_gateway_api_key else {}
    )
    rates: dict[str, Rate] = {}
    try:
        with httpx.Client(timeout=8.0, verify=verify_option(s), headers=headers) as c:
            response = c.get(f"{s.llm_gateway_url.rstrip('/')}/model/info")
            if response.status_code >= 300:
                return {}
            for entry in response.json().get("data", []):
                name = entry.get("model_name")
                info = entry.get("model_info") or {}
                if not name:
                    continue
                input_cost = _first_number(info, INPUT_FIELDS)
                output_cost = _first_number(info, OUTPUT_FIELDS)
                if input_cost is None and output_cost is None:
                    continue
                rates[name] = Rate(
                    input_per_token=input_cost or 0.0,
                    output_per_token=output_cost or 0.0,
                    source="gateway",
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("costs.gateway_rates_unavailable",
                     error=f"{type(exc).__name__}: {exc}")
        return {}

    logger.info("costs.rates_loaded", models=len(rates))
    return rates


def _first_number(info: dict, fields: tuple[str, ...]) -> float | None:
    for field in fields:
        value = info.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def rate_for(model: str) -> Rate:
    """The rate for one model alias, falling back to the configured default."""
    try:
        advertised = gateway_rates().get(model)
        if advertised is not None:
            return advertised
        return _fallback_rate()
    except Exception as exc:  # noqa: BLE001
        logger.debug("costs.rate_lookup_failed", model=model, error=str(exc))
        return Rate(0.0, 0.0, "unknown")


def estimate(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimated USD for one call. Never raises."""
    if tokens_in <= 0 and tokens_out <= 0:
        return 0.0
    try:
        return round(rate_for(model).cost(tokens_in, tokens_out), 6)
    except Exception:  # noqa: BLE001
        return 0.0


def describe(model: str) -> dict:
    """Rate provenance for the settings drawer, so an operator can tell a
    quoted price from an assumed one."""
    rate = rate_for(model)
    return {
        "model": model,
        "input_per_mtok": round(rate.input_per_token * 1_000_000, 4),
        "output_per_mtok": round(rate.output_per_token * 1_000_000, 4),
        "source": rate.source,
    }
