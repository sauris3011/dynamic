"""Layer 2 — uncertainty: Monte Carlo over the estimated demand response.

FR-080 .. FR-087. This module answers "how confident are we?", and its output is
what makes bounded autonomy defensible: without a real variance number there is
no principled basis for deciding which recommendations are safe to auto-approve.

**The trap this module exists to avoid (FR-081, PRD 4.7).** It is tempting to
build the sampling distribution from observed sales variance. That would be
wrong. Historical variance describes how demand *has fluctuated*; it says nothing
about how demand *responds to a price we have not yet set*. Simulating revenue at
a candidate price needs E[demand | price], a causal quantity — so this module
samples around the **estimated elasticity and its standard error**, which is
exactly what elasticity.py produced. Sampling historical variance instead yields
a tight, well-formed, confidently-presented distribution of the wrong thing.

Sources of uncertainty sampled (FR-082):
  1. elasticity          — from the regression standard error
  2. competitor response — rivals may partially follow a price move
  3. cost drift          — unit cost is not fixed over the horizon
  4. demand shock        — residual volatility not explained by the model
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pricing.analytics.elasticity import ElasticityEstimate


@dataclass(frozen=True)
class UncertaintyInputs:
    """Dispersion assumptions. Defaults are deliberately conservative — it is
    better to escalate a borderline recommendation than to auto-approve one on
    an over-confident interval."""

    competitor_response_sd: float = 0.06   # rivals move +/- ~6% against us
    cost_drift_sd: float = 0.03            # unit cost drift over the horizon
    demand_shock_sd: float = 0.16          # residual demand volatility (log scale)


@dataclass
class PriceOutcome:
    """Simulated distribution at a single candidate price."""

    price: float
    expected_units: float
    expected_revenue: float
    expected_margin: float
    revenue_p5: float
    revenue_p50: float
    revenue_p95: float
    margin_p5: float
    margin_p50: float
    revenue_cv: float                # coefficient of variation = the variance signal
    prob_below_margin_floor: float
    prob_revenue_gain: float


@dataclass
class SimulationResult:
    sku: str
    baseline_price: float
    baseline_revenue: float
    baseline_margin: float
    outcomes: list[PriceOutcome]
    iterations: int
    seed: int
    degraded: bool = False
    note: str = ""

    def at(self, price: float) -> PriceOutcome | None:
        for o in self.outcomes:
            if abs(o.price - price) < 1e-6:
                return o
        return None


def simulate(
    sku: str,
    candidate_prices: np.ndarray | list[float],
    current_price: float,
    unit_cost: float,
    base_demand: float,
    elasticity: ElasticityEstimate,
    margin_floor_pct: float = 15.0,
    iterations: int = 2000,
    seed: int = 20260807,
    inputs: UncertaintyInputs | None = None,
) -> SimulationResult:
    """Simulate revenue and margin distributions across candidate prices.

    Reproducible by construction (FR-087): the RNG is seeded per SKU, so the
    same inputs always produce the same distribution and a run can be audited
    after the fact.
    """
    inputs = inputs or UncertaintyInputs()
    candidates = np.asarray(candidate_prices, dtype=float)
    candidates = candidates[candidates > 0]
    if candidates.size == 0:
        raise ValueError("No positive candidate prices supplied.")

    # Per-SKU seed derivation keeps runs reproducible while avoiding every SKU
    # sharing an identical noise draw.
    rng = np.random.default_rng(abs(hash((seed, sku))) % (2**32))

    if elasticity.usable and np.isfinite(elasticity.elasticity):
        e_mean, e_se = elasticity.elasticity, max(elasticity.std_error, 1e-4)
        degraded, note = False, ""
    else:
        # No trustworthy estimate: fall back to a wide category-typical prior and
        # say so. The wide interval will push the recommendation to Escalate,
        # which is the correct outcome — not a confident guess.
        e_mean, e_se = -1.5, 0.9
        degraded = True
        note = (
            "No usable elasticity estimate; simulated against a wide prior. "
            "Treat this recommendation as low confidence."
        )

    n = int(iterations)
    # Truncated at -0.05: a non-negative elasticity would imply demand rising
    # with price, which inverts the optimizer's logic.
    sampled_e = np.clip(rng.normal(e_mean, e_se, n), -6.0, -0.05)
    comp_factor = 1.0 + rng.normal(0.0, inputs.competitor_response_sd, n)
    cost_factor = 1.0 + rng.normal(0.0, inputs.cost_drift_sd, n)
    demand_shock = np.exp(rng.normal(0.0, inputs.demand_shock_sd, n))

    costs = np.maximum(unit_cost * cost_factor, 0.01)

    # (candidates, iterations) — vectorised so 500 SKUs stay inside the
    # 60-second Monte Carlo budget (NFR-037).
    ratio = candidates[:, None] / max(current_price, 1e-6)
    demand = (
        base_demand
        * np.power(ratio, sampled_e[None, :])
        * demand_shock[None, :]
        * comp_factor[None, :]
    )
    demand = np.maximum(demand, 0.0)

    revenue = demand * candidates[:, None]
    margin = demand * (candidates[:, None] - costs[None, :])
    margin_pct = np.divide(
        candidates[:, None] - costs[None, :],
        candidates[:, None],
        out=np.zeros_like(demand),
        where=candidates[:, None] > 0,
    ) * 100.0

    base_units = float(base_demand)
    baseline_revenue = base_units * current_price
    baseline_margin = base_units * (current_price - unit_cost)

    outcomes: list[PriceOutcome] = []
    for i, price in enumerate(candidates):
        rev, mar, mpct = revenue[i], margin[i], margin_pct[i]
        mean_rev = float(rev.mean())
        outcomes.append(
            PriceOutcome(
                price=round(float(price), 2),
                expected_units=round(float(demand[i].mean()), 2),
                expected_revenue=round(mean_rev, 2),
                expected_margin=round(float(mar.mean()), 2),
                revenue_p5=round(float(np.percentile(rev, 5)), 2),
                revenue_p50=round(float(np.percentile(rev, 50)), 2),
                revenue_p95=round(float(np.percentile(rev, 95)), 2),
                margin_p5=round(float(np.percentile(mar, 5)), 2),
                margin_p50=round(float(np.percentile(mar, 50)), 2),
                revenue_cv=round(float(rev.std() / mean_rev), 4) if mean_rev > 0 else 9.99,
                prob_below_margin_floor=round(float((mpct < margin_floor_pct).mean()), 4),
                prob_revenue_gain=round(float((rev > baseline_revenue).mean()), 4),
            )
        )

    return SimulationResult(
        sku=sku,
        baseline_price=round(current_price, 2),
        baseline_revenue=round(baseline_revenue, 2),
        baseline_margin=round(baseline_margin, 2),
        outcomes=outcomes,
        iterations=n,
        seed=seed,
        degraded=degraded,
        note=note,
    )


# --- Stress testing (FR-086) ----------------------------------------------

STRESS_SCENARIOS: dict[str, dict[str, float]] = {
    "demand_collapse": {"demand_multiplier": 0.55, "competitor_shift": 0.0,
                        "cost_multiplier": 1.0},
    "competitor_undercut": {"demand_multiplier": 0.82, "competitor_shift": -0.12,
                            "cost_multiplier": 1.0},
    "cost_spike": {"demand_multiplier": 1.0, "competitor_shift": 0.0,
                   "cost_multiplier": 1.18},
}


def stress_test(
    sku: str,
    price: float,
    current_price: float,
    unit_cost: float,
    base_demand: float,
    elasticity: ElasticityEstimate,
    margin_floor_pct: float = 15.0,
    iterations: int = 1000,
    seed: int = 20260807,
) -> dict[str, PriceOutcome]:
    """Re-simulate one price under adverse conditions so downside is visible
    before committing, not discovered afterwards."""
    results: dict[str, PriceOutcome] = {}
    for name, params in STRESS_SCENARIOS.items():
        sim = simulate(
            sku=f"{sku}::{name}",
            candidate_prices=[price * (1 + params["competitor_shift"])],
            current_price=current_price,
            unit_cost=unit_cost * params["cost_multiplier"],
            base_demand=base_demand * params["demand_multiplier"],
            elasticity=elasticity,
            margin_floor_pct=margin_floor_pct,
            iterations=iterations,
            seed=seed,
        )
        results[name] = sim.outcomes[0]
    return results
