"""Layer 3 — decision: constrained price selection (FR-017 .. FR-020, FR-088).

Chooses over the Monte Carlo *distributions*, not point estimates. Constraints
are applied as a hard feasibility filter before scoring, so an infeasible price
can never win on objective value — the optimizer cannot trade compliance for
revenue.

If no candidate is feasible the answer is "hold the current price", not "pick
the least-bad violation".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from pricing.analytics import price_points
from pricing.analytics.montecarlo import PriceOutcome, SimulationResult


class Objective(str, Enum):
    REVENUE = "revenue"
    MARGIN = "margin"
    BALANCED = "balanced"


@dataclass(frozen=True)
class PriceConstraints:
    margin_floor_pct: float = 15.0
    max_change_pct: float = 10.0
    map_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    max_prob_below_margin: float = 0.20
    # Set when stability detects oscillation: shrinks the permitted move
    # rather than blocking it outright (FR-091).
    damping_factor: float = 1.0


@dataclass
class OptimizationResult:
    sku: str
    current_price: float
    recommended_price: float
    outcome: PriceOutcome | None
    baseline_revenue: float
    expected_revenue_delta: float
    expected_margin_delta: float
    revenue_ci_low: float
    revenue_ci_high: float
    objective: Objective
    feasible_count: int
    candidates_considered: int
    damped: bool
    hold: bool
    reason: str


def candidate_grid(
    current_price: float,
    max_change_pct: float,
    steps: int = 21,          # retained for API compatibility; ladder is exact
    damping_factor: float = 1.0,
) -> np.ndarray:
    """Valid shelf prices within the permitted change band around current.

    Uses the magnitude-scaled ladder in `price_points` rather than a fixed
    .49/.99 snap. A naive snap collapses the band to a single point on
    low-priced items, which silently turns the optimizer into a no-op.
    """
    span = max_change_pct * max(min(damping_factor, 1.0), 0.05) / 100.0
    lo, hi = current_price * (1 - span), current_price * (1 + span)

    grid = price_points.ladder(lo, hi)
    # "Hold" must always be available, even if the current price is not itself
    # a canonical ladder point (it may predate a policy change).
    current = round(current_price, 2)
    grid = np.unique(np.append(grid, current)) if grid.size else np.array([current])
    return grid[grid > 0]


def _feasible(
    outcome: PriceOutcome, current_price: float, unit_cost: float,
    c: PriceConstraints,
) -> tuple[bool, str]:
    price = outcome.price

    margin_pct = (price - unit_cost) / price * 100.0 if price > 0 else -100.0
    if margin_pct < c.margin_floor_pct:
        return False, f"margin {margin_pct:.1f}% below floor {c.margin_floor_pct:.1f}%"

    if c.map_price is not None and price < c.map_price:
        return False, f"below MAP {c.map_price:.2f}"

    if c.min_price is not None and price < c.min_price:
        return False, f"below category floor {c.min_price:.2f}"

    if c.max_price is not None and price > c.max_price:
        return False, f"above category ceiling {c.max_price:.2f}"

    if current_price > 0:
        change = abs(price - current_price) / current_price * 100.0
        allowed = c.max_change_pct * max(min(c.damping_factor, 1.0), 0.05)
        if change > allowed + 1e-9:
            return False, f"change {change:.1f}% exceeds allowed {allowed:.1f}%"

    if outcome.prob_below_margin_floor > c.max_prob_below_margin:
        return False, (
            f"P(margin<floor)={outcome.prob_below_margin_floor:.0%} exceeds "
            f"{c.max_prob_below_margin:.0%}"
        )

    return True, ""


def _score(outcome: PriceOutcome, objective: Objective,
           rev_range: tuple[float, float], mar_range: tuple[float, float]) -> float:
    def norm(v, lo, hi):
        return 0.5 if hi - lo < 1e-9 else (v - lo) / (hi - lo)

    if objective is Objective.REVENUE:
        return outcome.expected_revenue
    if objective is Objective.MARGIN:
        return outcome.expected_margin
    return 0.5 * norm(outcome.expected_revenue, *rev_range) + 0.5 * norm(
        outcome.expected_margin, *mar_range
    )


def optimize(
    simulation: SimulationResult,
    unit_cost: float,
    constraints: PriceConstraints,
    objective: Objective = Objective.BALANCED,
) -> OptimizationResult:
    """Select the best feasible price from the simulated candidates."""
    current = simulation.baseline_price
    outcomes = simulation.outcomes

    feasible: list[PriceOutcome] = []
    for o in outcomes:
        ok, _ = _feasible(o, current, unit_cost, constraints)
        if ok:
            feasible.append(o)

    damped = constraints.damping_factor < 1.0

    if not feasible:
        return OptimizationResult(
            sku=simulation.sku, current_price=current, recommended_price=current,
            outcome=simulation.at(current), baseline_revenue=simulation.baseline_revenue,
            expected_revenue_delta=0.0, expected_margin_delta=0.0,
            revenue_ci_low=0.0, revenue_ci_high=0.0, objective=objective,
            feasible_count=0, candidates_considered=len(outcomes), damped=damped,
            hold=True,
            reason=(
                "No candidate price satisfied all constraints, so the current price "
                "is held. Holding is the correct output here — the alternative would "
                "be recommending a price known to breach a constraint."
            ),
        )

    revs = [o.expected_revenue for o in feasible]
    mars = [o.expected_margin for o in feasible]
    rev_range, mar_range = (min(revs), max(revs)), (min(mars), max(mars))
    best = max(feasible, key=lambda o: _score(o, objective, rev_range, mar_range))

    hold = abs(best.price - current) < 0.005
    return OptimizationResult(
        sku=simulation.sku,
        current_price=current,
        recommended_price=best.price,
        outcome=best,
        baseline_revenue=simulation.baseline_revenue,
        expected_revenue_delta=round(best.expected_revenue - simulation.baseline_revenue, 2),
        expected_margin_delta=round(best.expected_margin - simulation.baseline_margin, 2),
        revenue_ci_low=round(best.revenue_p5 - simulation.baseline_revenue, 2),
        revenue_ci_high=round(best.revenue_p95 - simulation.baseline_revenue, 2),
        objective=objective,
        feasible_count=len(feasible),
        candidates_considered=len(outcomes),
        damped=damped,
        hold=hold,
        reason=(
            "Current price is already optimal within constraints."
            if hold
            else f"Best of {len(feasible)} feasible candidates on the "
                 f"{objective.value} objective."
        ),
    )
