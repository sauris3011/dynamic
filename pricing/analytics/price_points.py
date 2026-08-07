"""Realistic retail price points, shared by the optimizer and the baseline.

Recommending £4.5137 is not actionable in a store, so candidate prices must snap
to points a retailer would actually use. But snapping naively to only .49/.99
destroys the optimizer: within a +/-10% band, a £2.49 item has exactly one
valid point (£2.49 itself), so the optimizer is handed a single option and
"recommends" no change on most of the catalog. That is a grid-resolution bug
masquerading as conservatism.

Real retail ladders get finer as prices get lower — 1.09, 1.19, 1.29 are all
ordinary shelf prices, while £47.30 is not. Granularity therefore scales with
magnitude, which restores resolution at the low end without producing absurd
precision at the high end.

Both the AI optimizer and the rule-based baseline use this module, so the
comparison in PRD 9.3 comes from the same price universe rather than flattering
one side.
"""

from __future__ import annotations

import math

import numpy as np

MIN_PRICE = 0.19


def granularity(price: float) -> float:
    """Step size between adjacent shelf prices at this magnitude."""
    if price < 5.0:
        return 0.10
    if price < 20.0:
        return 0.50
    if price < 50.0:
        return 1.00
    return 2.00


# All arithmetic below is in integer cents. Working in floats here is actively
# unsafe: 4.10 / 0.10 evaluates to 40.99999999999999 in IEEE-754, so a
# float-based cursor silently fails to advance and the ladder collapses to a
# single point — which then shows up much later as "the optimizer recommends no
# change on most of the catalog".
MIN_CENTS = int(round(MIN_PRICE * 100))


def _step_cents(price_cents: int) -> int:
    return int(round(granularity(price_cents / 100.0) * 100))


def snap(price: float) -> float:
    """Snap a single price to the nearest valid shelf point."""
    if price <= MIN_PRICE:
        return MIN_PRICE
    cents = int(round(price * 100))
    step = _step_cents(cents)
    base = (cents // step) * step
    below = base - 1                 # charm point one cent under the step
    above = base + step - 1
    if below < MIN_CENTS:
        return round(above / 100.0, 2)
    nearest = above if abs(above - cents) <= abs(below - cents) else below
    return round(nearest / 100.0, 2)


def ladder(low: float, high: float, max_points: int = 60) -> np.ndarray:
    """All valid shelf prices in [low, high], ascending.

    Returns an empty array when the interval contains no valid point. The caller
    must handle that rather than widening the band — quietly stepping outside
    the permitted range would breach the change cap the optimizer was given.
    """
    if high < low:
        low, high = high, low

    lo_c = max(int(math.ceil(low * 100)), MIN_CENTS)
    hi_c = int(math.floor(high * 100))
    if hi_c < lo_c:
        return np.array([])

    points: list[float] = []
    cursor = lo_c
    guard = 0
    while cursor <= hi_c and guard < max_points * 4:
        step = _step_cents(cursor)
        candidate = (cursor // step) * step + step - 1
        if candidate < cursor:
            candidate += step
        if candidate > hi_c:
            break
        points.append(round(candidate / 100.0, 2))
        cursor = candidate + 1       # strictly advances, so no stall
        guard += 1

    return np.array(sorted(set(points))) if points else np.array([])
