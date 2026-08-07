"""Competitor price feed (FR-004, FR-005, D14).

`CompetitorFeed` is the interface; `SyntheticCompetitorFeed` is the demo
implementation. A live scraping or vendor-API adapter implements the same
protocol and drops in without touching any caller — that seam is the point, and
it is why competitor data is not simply hardcoded into the pipeline.

The synthetic feed is a deterministic function of (sku, competitor, date) rather
than a stored table. Two consequences worth having:

* A run is reproducible — re-running the same date gives identical competitor
  prices, so a demo cannot mysteriously change between rehearsal and stage.
* Prices still *drift* across days, so the continuous loop has genuine market
  movement to react to rather than a frozen snapshot.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from pricing.config import get_settings

# Fallback roster, used only when configuration cannot be read (in a unit test
# constructing the feed directly, for instance). The real roster and its
# positioning come from COMPETITOR_BIAS in .env — a different market has
# different rivals, and hardcoding them would defeat the pluggable feed
# interface this module exists to provide.
DEFAULT_BIAS: dict[str, float] = {
    "MarketFresh": 1.04,   # slightly premium
    "ValueMart": 0.93,     # discounter
    "Prime Grocer": 1.00,  # at parity
}


@dataclass(frozen=True)
class CompetitorPrice:
    sku: str
    competitor: str
    price: float
    observed_on: str
    in_stock: bool


@dataclass(frozen=True)
class CompetitivePosition:
    """Derived signal handed to the agents, not raw observations."""

    sku: str
    our_price: float
    min_competitor_price: float
    max_competitor_price: float
    mean_competitor_price: float
    market_index: float          # our price / mean competitor price
    gap_to_cheapest_pct: float   # positive means we are more expensive
    competitors_in_stock: int
    observations: list[CompetitorPrice]


@runtime_checkable
class CompetitorFeed(Protocol):
    def fetch(self, skus: list[str], on: date | None = None) -> list[CompetitorPrice]:
        ...


def _settings_or_none():
    """Settings, or None when configuration is unavailable.

    The feed must remain constructible without a valid environment — it is used
    in tests and could be imported by tooling — so a config failure degrades to
    the documented defaults rather than making the feed unusable.
    """
    try:
        return get_settings()
    except Exception:  # noqa: BLE001
        return None


def _resolve(override: float | None, configured: float) -> float:
    return configured if override is None else override


def _stable_unit(*parts: str) -> float:
    """Deterministic pseudo-random in [0, 1) from the given parts.

    Uses a hash rather than `random` so results depend only on the inputs and
    never on call order or process state.
    """
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


class SyntheticCompetitorFeed:
    """Deterministic synthetic competitor prices anchored to our own price.

    Each competitor has a persistent positioning bias (a discounter sits below
    us, a premium retailer above), plus a slow drift so the market moves over
    days, plus per-SKU idiosyncratic noise.

    The roster, the positioning, and the dispersion all come from configuration
    (COMPETITOR_BIAS and the COMPETITOR_* knobs). `bias` can be passed directly,
    which is what the tests do — a test that had to mutate the environment to
    change a competitor's positioning would be testing the config loader rather
    than the feed.
    """

    def __init__(
        self,
        reference_prices: dict[str, float],
        bias: dict[str, float] | None = None,
        idiosyncratic_pct: float | None = None,
        drift_pct: float | None = None,
        out_of_stock_rate: float | None = None,
    ) -> None:
        self._reference = reference_prices
        settings = _settings_or_none()

        self.bias = bias or (
            settings.competitor_bias_map if settings else dict(DEFAULT_BIAS)
        )
        self._idio = _resolve(
            idiosyncratic_pct,
            settings.competitor_idiosyncratic_pct if settings else 14.0,
        ) / 100.0
        self._drift = _resolve(
            drift_pct, settings.competitor_drift_pct if settings else 3.5
        ) / 100.0
        self._oos = _resolve(
            out_of_stock_rate,
            settings.competitor_out_of_stock_rate if settings else 0.085,
        )

    @property
    def competitors(self) -> tuple[str, ...]:
        return tuple(self.bias)

    def fetch(
        self, skus: list[str], on: date | None = None
    ) -> list[CompetitorPrice]:
        day = on or date.today()
        day_key = day.isoformat()
        # Drift is a slow sinusoid on day-of-year, so consecutive days move a
        # little rather than jumping randomly.
        drift_phase = 2 * math.pi * (day.timetuple().tm_yday / 365.25)

        out: list[CompetitorPrice] = []
        for sku in skus:
            ref = self._reference.get(sku)
            if not ref or ref <= 0:
                continue
            for comp, bias in self.bias.items():
                # Centred on zero, so the configured bias remains the mean
                # positioning rather than being shifted by the noise.
                idio = (_stable_unit(sku, comp) - 0.5) * self._idio
                drift = self._drift * math.sin(drift_phase + _stable_unit(comp) * 6.28)
                price = ref * bias * (1 + idio + drift)
                price = max(0.19, round(price, 2))
                # Some SKU/competitor pairs are out of stock, which materially
                # changes competitive pressure.
                in_stock = _stable_unit(sku, comp, day_key, "stock") > self._oos
                out.append(
                    CompetitorPrice(
                        sku=sku, competitor=comp, price=price,
                        observed_on=day_key, in_stock=in_stock,
                    )
                )
        return out


def build_positions(
    observations: list[CompetitorPrice], our_prices: dict[str, float]
) -> dict[str, CompetitivePosition]:
    """Collapse raw observations into one position per SKU.

    Out-of-stock competitors are excluded from the price aggregates — a price
    you cannot actually buy at exerts no competitive pressure — but still count
    toward the observation list for auditability.
    """
    by_sku: dict[str, list[CompetitorPrice]] = {}
    for obs in observations:
        by_sku.setdefault(obs.sku, []).append(obs)

    positions: dict[str, CompetitivePosition] = {}
    for sku, obs_list in by_sku.items():
        our_price = our_prices.get(sku)
        if not our_price or our_price <= 0:
            continue
        available = [o.price for o in obs_list if o.in_stock]
        if not available:
            available = [o.price for o in obs_list]
        if not available:
            continue

        mean_price = sum(available) / len(available)
        min_price = min(available)
        positions[sku] = CompetitivePosition(
            sku=sku,
            our_price=our_price,
            min_competitor_price=round(min_price, 2),
            max_competitor_price=round(max(available), 2),
            mean_competitor_price=round(mean_price, 2),
            market_index=round(our_price / mean_price, 4) if mean_price else 1.0,
            gap_to_cheapest_pct=round((our_price - min_price) / min_price * 100, 2)
            if min_price
            else 0.0,
            competitors_in_stock=sum(1 for o in obs_list if o.in_stock),
            observations=obs_list,
        )
    return positions
