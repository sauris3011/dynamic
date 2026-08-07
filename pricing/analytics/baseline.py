"""Rule-based baseline pricer — the conventional comparator (FR-057, PRD 9.3).

The problem statement requires showing that AI *materially improves* on a
conventional approach. That cannot be asserted, only measured, which means
shipping the conventional approach and running it on identical data.

This is a faithful implementation of how retail pricing actually works today:
cost-plus markup by category, a competitive clamp, blanket clearance discounting
on slow stock, and charm rounding. It is not a strawman — it is deliberately
competent, because beating a strawman would prove nothing.

What it structurally cannot do, and what the comparison is meant to expose:

* It has no demand model, so it cannot know that a 5% cut on an elastic SKU
  raises revenue while the same cut on an inelastic one destroys margin.
* It emits the same unqualified number whether backed by three years of clean
  history or three noisy weeks. It cannot say "I don't know", which is precisely
  why a rules engine can never be trusted to act unsupervised (PRD 9.3 claim 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from pricing.analytics import price_points

# Target markup on cost, by category. Typical of category-level pricing rules.
CATEGORY_MARKUP: dict[str, float] = {
    "Beverages": 1.85,
    "Snacks": 1.95,
    "Coffee & Tea": 1.75,
    "Household": 1.70,
    "Personal Care": 2.05,
}
DEFAULT_MARKUP = 1.80

# Blanket clearance ladder on cover days — no demand model involved.
CLEARANCE_RULES: list[tuple[float, float]] = [
    (120.0, 0.80),   # >120 days cover -> 20% off
    (75.0, 0.90),    # >75  days cover -> 10% off
    (45.0, 0.95),    # >45  days cover -> 5%  off
]

# If we sit more than this above the cheapest competitor, clamp toward them.
COMPETITIVE_TOLERANCE_PCT = 8.0
MAX_STEP_PCT = 15.0


@dataclass
class BaselineResult:
    sku: str
    current_price: float
    baseline_price: float
    delta_pct: float
    rules_fired: list[str]


def _snap(price: float) -> float:
    """Same shelf-price ladder the optimizer uses, so the PRD 9.3 comparison is
    drawn from one price universe rather than flattering either side."""
    return price_points.snap(price)


def price_one(
    sku: str,
    category: str,
    unit_cost: float,
    current_price: float,
    cover_days: float | None = None,
    min_competitor_price: float | None = None,
    map_price: float | None = None,
) -> BaselineResult:
    """Apply the conventional rule chain to a single SKU."""
    fired: list[str] = []

    markup = CATEGORY_MARKUP.get(category, DEFAULT_MARKUP)
    price = unit_cost * markup
    fired.append(f"cost_plus:{markup:.2f}x")

    if cover_days is not None:
        for threshold, multiplier in CLEARANCE_RULES:
            if cover_days > threshold:
                price *= multiplier
                fired.append(f"clearance:>{threshold:.0f}d:{(1 - multiplier):.0%}")
                break

    if min_competitor_price is not None and min_competitor_price > 0:
        ceiling = min_competitor_price * (1 + COMPETITIVE_TOLERANCE_PCT / 100.0)
        if price > ceiling:
            price = ceiling
            fired.append("competitive_clamp")

    if map_price is not None and price < map_price:
        price = map_price
        fired.append("map_floor")

    # Never below cost.
    if price < unit_cost * 1.02:
        price = unit_cost * 1.02
        fired.append("cost_floor")

    # Cap the step so the baseline is not gratuitously erratic — otherwise the
    # comparison would flatter the AI for the wrong reason.
    if current_price > 0:
        change = (price - current_price) / current_price * 100.0
        if abs(change) > MAX_STEP_PCT:
            price = current_price * (1 + (MAX_STEP_PCT / 100.0) * (1 if change > 0 else -1))
            fired.append(f"step_cap:{MAX_STEP_PCT:.0f}%")

    final = _snap(price)
    delta = (final - current_price) / current_price * 100.0 if current_price > 0 else 0.0

    return BaselineResult(
        sku=sku,
        current_price=round(current_price, 2),
        baseline_price=final,
        delta_pct=round(delta, 2),
        rules_fired=fired,
    )
