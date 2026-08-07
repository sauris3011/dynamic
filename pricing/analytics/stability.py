"""Price stability: oscillation detection, damping, convergence (FR-090 .. FR-095).

Oscillation is a failure mode distinct from a single bad price, and the
max-change-% cap does not prevent it. A SKU can ping-pong between two price
points across consecutive runs with every individual step comfortably inside the
cap — each run "correctly" reacting to the state the last run created. The
customer sees a price that will not sit still; sustained algorithmic oscillation
in a competitive market also carries signalling risk.

Detection therefore looks across *runs*, not within one.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean


@dataclass
class StabilitySignal:
    sku: str
    oscillating: bool
    reversals: int
    observations: int
    damping_factor: float          # 1.0 = no damping; lower = tighter leash
    price_range_pct: float
    detail: str


@dataclass
class ConvergenceSignal:
    """Is the system's forecasting getting better or worse over time (FR-092)?"""

    samples: int
    mean_abs_error_pct: float
    recent_error_pct: float
    earlier_error_pct: float
    converging: bool
    detail: str


def detect_oscillation(
    sku: str,
    price_history: list[float],
    window: int = 4,
    max_reversals: int = 2,
    damping_factor: float = 0.4,
) -> StabilitySignal:
    """Flag a SKU whose recent price direction keeps reversing.

    `price_history` is most-recent-first. Direction changes are counted over the
    last `window` moves; more than `max_reversals` means the SKU is chasing its
    own tail rather than tracking the market.
    """
    prices = [p for p in price_history if p and p > 0][: window + 1]

    if len(prices) < 3:
        return StabilitySignal(
            sku=sku, oscillating=False, reversals=0, observations=len(prices),
            damping_factor=1.0, price_range_pct=0.0,
            detail="Not enough price history to assess stability.",
        )

    # price_history is newest-first; reverse so deltas read forward in time.
    series = list(reversed(prices))
    deltas = [series[i + 1] - series[i] for i in range(len(series) - 1)]
    directions = [1 if d > 1e-9 else (-1 if d < -1e-9 else 0) for d in deltas]
    moves = [d for d in directions if d != 0]

    reversals = sum(1 for i in range(len(moves) - 1) if moves[i] != moves[i + 1])
    lo, hi = min(series), max(series)
    range_pct = (hi - lo) / lo * 100.0 if lo > 0 else 0.0

    oscillating = reversals > max_reversals
    return StabilitySignal(
        sku=sku,
        oscillating=oscillating,
        reversals=reversals,
        observations=len(series),
        damping_factor=damping_factor if oscillating else 1.0,
        price_range_pct=round(range_pct, 2),
        detail=(
            f"Price direction reversed {reversals} times in the last "
            f"{len(moves)} moves (limit {max_reversals}), swinging {range_pct:.1f}%. "
            f"Damping to {damping_factor:.0%} of the normal step."
            if oscillating
            else f"Stable: {reversals} reversal(s) over {len(moves)} move(s)."
        ),
    )


def assess_convergence(forecast_errors: list[float], min_samples: int = 6) -> ConvergenceSignal:
    """Compare recent forecast error against earlier error (FR-092, FR-110).

    A system that is learning should see this narrow. A widening gap is
    surfaced as a warning rather than buried — a feedback loop that is drifting
    is worse than no feedback loop, because it looks like progress.
    """
    errors = [abs(e) for e in forecast_errors if e is not None]
    n = len(errors)

    if n < min_samples:
        return ConvergenceSignal(
            samples=n, mean_abs_error_pct=round(mean(errors), 2) if errors else 0.0,
            recent_error_pct=0.0, earlier_error_pct=0.0, converging=False,
            detail=f"Only {n} outcome(s) recorded; {min_samples} needed to judge "
                   "convergence.",
        )

    half = n // 2
    earlier, recent = mean(errors[:half]), mean(errors[half:])
    converging = recent <= earlier

    return ConvergenceSignal(
        samples=n,
        mean_abs_error_pct=round(mean(errors), 2),
        recent_error_pct=round(recent, 2),
        earlier_error_pct=round(earlier, 2),
        converging=converging,
        detail=(
            f"Forecast error narrowed from {earlier:.1f}% to {recent:.1f}% "
            "across recent outcomes."
            if converging
            else f"WARNING: forecast error widened from {earlier:.1f}% to "
                 f"{recent:.1f}%. The model is drifting, not learning."
        ),
    )
