"""The deterministic stack: elasticity, Monte Carlo, optimizer (PRD 4.7).

These are the tests that matter most, because everything downstream inherits
whatever they get wrong. If elasticity is biased the optimizer confidently picks
the wrong price; if the simulation does not consume elasticity the confidence
number is decorative and the whole autonomy model rests on nothing.
"""

from __future__ import annotations

import numpy as np
import pytest

from pricing.analytics.elasticity import ElasticityEstimate, estimate_from_records
from pricing.analytics.montecarlo import UncertaintyInputs, simulate, stress_test
from pricing.analytics.optimizer import (
    Objective,
    PriceConstraints,
    candidate_grid,
    optimize,
)
from tests.conftest import sales_frame


def usable_estimate(elasticity: float = -1.8, se: float = 0.15) -> ElasticityEstimate:
    return ElasticityEstimate(
        sku="TEST-1", elasticity=elasticity, ci_low=elasticity - 2 * se,
        ci_high=elasticity + 2 * se, std_error=se, r_squared=0.6,
        sample_size=180, price_cv=0.11, usable=True,
    )


# --- Layer 1: causal ------------------------------------------------------

def test_elasticity_recovers_the_generating_coefficient():
    est = estimate_from_records("TEST-1", sales_frame(elasticity=-1.8))
    assert est.usable
    assert est.elasticity == pytest.approx(-1.8, abs=0.35)
    assert est.ci_low < est.elasticity < est.ci_high


def test_omitting_the_promo_control_biases_the_estimate():
    """The bias is systematic, not noise — it is why the control exists.

    Promotions cut price *and* buy display space. Without the dummy, the display
    lift is attributed to the price cut and elasticity is over-stated, which
    causes over-discounting in production.
    """
    from pricing.analytics.elasticity import estimate_elasticity

    rows = sales_frame(elasticity=-1.8)
    units = np.array([r["units"] for r in rows], dtype=float)
    prices = np.array([r["unit_price"] for r in rows], dtype=float)
    promo = np.array([1.0 if r["on_promo"] else 0.0 for r in rows])

    controlled = estimate_elasticity("TEST-1", units, prices, promo)
    naive = estimate_elasticity("TEST-1", units, prices, None)
    assert abs(controlled.elasticity + 1.8) < abs(naive.elasticity + 1.8)


def test_thin_history_is_excluded_rather_than_guessed():
    est = estimate_from_records("TEST-1", sales_frame(n=20))
    assert not est.usable
    assert "observations" in est.reason


def test_flat_prices_make_elasticity_unidentifiable():
    rows = sales_frame(n=180)
    for r in rows:
        r["unit_price"] = 4.0
        r["on_promo"] = False
    est = estimate_from_records("TEST-1", rows)
    assert not est.usable
    assert "not identifiable" in est.reason


def test_confidence_falls_as_the_interval_widens():
    tight, wide = usable_estimate(se=0.10), usable_estimate(se=0.60)
    assert tight.confidence > wide.confidence


# --- Layer 2: uncertainty -------------------------------------------------

def test_simulation_consumes_the_elasticity_estimate():
    """FR-081. A simulation that ignores elasticity is the failure PRD 4.7 names."""
    elastic = simulate("A", [3.6, 4.0, 4.4], 4.0, 2.0, 100.0, usable_estimate(-2.6),
                       iterations=800)
    inelastic = simulate("A", [3.6, 4.0, 4.4], 4.0, 2.0, 100.0, usable_estimate(-0.6),
                         iterations=800)
    # Raising price on elastic demand must cost more units than on inelastic.
    e_hi = elastic.at(4.4).expected_units / elastic.at(4.0).expected_units
    i_hi = inelastic.at(4.4).expected_units / inelastic.at(4.0).expected_units
    assert e_hi < i_hi


def test_simulation_is_reproducible_from_a_fixed_seed():
    """FR-087 — an audit that cannot be reproduced is not an audit."""
    kwargs = dict(sku="A", candidate_prices=[3.8, 4.0, 4.2], current_price=4.0,
                  unit_cost=2.0, base_demand=100.0, elasticity=usable_estimate(),
                  iterations=500, seed=99)
    first, second = simulate(**kwargs), simulate(**kwargs)
    assert [o.__dict__ for o in first.outcomes] == [o.__dict__ for o in second.outcomes]


def test_a_different_seed_gives_a_different_draw():
    a = simulate("A", [4.0], 4.0, 2.0, 100.0, usable_estimate(), iterations=500, seed=1)
    b = simulate("A", [4.0], 4.0, 2.0, 100.0, usable_estimate(), iterations=500, seed=2)
    assert a.outcomes[0].expected_revenue != b.outcomes[0].expected_revenue


def test_unusable_elasticity_widens_the_interval_and_says_so():
    """Degradation must be visible. A confident number on no evidence is the
    single most dangerous output this system could produce.

    Measured at a *moved* price, deliberately. At the current price the demand
    ratio is exactly 1, so `ratio ** elasticity` is 1 for every sampled draw and
    elasticity uncertainty contributes nothing to the spread — correct, but it
    would make this assertion vacuous. Uncertainty about the demand response
    only shows up once you actually move.
    """
    unusable = ElasticityEstimate(
        sku="A", elasticity=float("nan"), ci_low=float("nan"), ci_high=float("nan"),
        std_error=float("nan"), r_squared=0.0, sample_size=3, price_cv=0.0,
        usable=False, reason="thin",
    )
    weak = simulate("A", [4.4], 4.0, 2.0, 100.0, unusable, iterations=2000)
    strong = simulate("A", [4.4], 4.0, 2.0, 100.0, usable_estimate(se=0.08),
                      iterations=2000)
    assert weak.degraded and not strong.degraded
    assert weak.outcomes[0].revenue_cv > strong.outcomes[0].revenue_cv
    assert weak.note


def test_elasticity_uncertainty_is_inert_at_the_current_price():
    """The corollary, asserted so the behaviour above is understood, not assumed.

    A 'hold' therefore carries no elasticity-driven variance — which is exactly
    why the band model has a separate `elasticity_usable` escalation trigger
    rather than relying on variance alone to catch an unmodellable SKU.
    """
    unusable = ElasticityEstimate(
        sku="A", elasticity=float("nan"), ci_low=float("nan"), ci_high=float("nan"),
        std_error=float("nan"), r_squared=0.0, sample_size=3, price_cv=0.0,
        usable=False, reason="thin",
    )
    weak = simulate("A", [4.0], 4.0, 2.0, 100.0, unusable, iterations=1200)
    strong = simulate("A", [4.0], 4.0, 2.0, 100.0, usable_estimate(se=0.08),
                      iterations=1200)
    assert weak.outcomes[0].revenue_cv == strong.outcomes[0].revenue_cv


def test_probability_below_margin_floor_is_reported():
    """FR-085 — the number the Escalate band's margin trigger reads.

    Not asserted as exactly 1.0: cost drift is sampled, so a thin-margin price
    lands above the floor on a small fraction of draws. That the probability is
    *not* degenerate is the point — it is a distribution, not a flag.
    """
    thin = simulate("A", [2.2], 2.2, 2.0, 100.0, usable_estimate(), iterations=600)
    fat = simulate("A", [8.0], 8.0, 2.0, 100.0, usable_estimate(), iterations=600)
    assert thin.outcomes[0].prob_below_margin_floor >= 0.95
    assert fat.outcomes[0].prob_below_margin_floor == 0.0


def test_dispersion_inputs_change_the_interval_width():
    """FR-108's lever must actually move the interval, or refinement is inert."""
    tight = simulate("A", [4.0], 4.0, 2.0, 100.0, usable_estimate(), iterations=1500,
                     inputs=UncertaintyInputs(demand_shock_sd=0.05))
    loose = simulate("A", [4.0], 4.0, 2.0, 100.0, usable_estimate(), iterations=1500,
                     inputs=UncertaintyInputs(demand_shock_sd=0.30))
    assert loose.outcomes[0].revenue_cv > tight.outcomes[0].revenue_cv


def test_stress_scenarios_are_worse_than_the_base_case():
    """FR-086 — downside must be visible before committing, not after."""
    base = simulate("A", [4.0], 4.0, 2.0, 100.0, usable_estimate(), iterations=800)
    stressed = stress_test("A", 4.0, 4.0, 2.0, 100.0, usable_estimate(), iterations=800)
    assert set(stressed) == {"demand_collapse", "competitor_undercut", "cost_spike"}
    assert stressed["demand_collapse"].expected_revenue < base.outcomes[0].expected_revenue


# --- Layer 3: decision ----------------------------------------------------

def test_optimizer_never_trades_compliance_for_revenue():
    """An infeasible price cannot win on objective value, at any margin.

    The revenue objective would pick the cheapest candidate if left alone; the
    margin floor must override that rather than being weighed against it.
    """
    sim = simulate("A", [2.1, 2.5, 3.0, 3.5, 4.0], 3.0, 2.0, 100.0, usable_estimate(),
                   iterations=600)
    result = optimize(
        sim, 2.0,
        PriceConstraints(margin_floor_pct=40.0, max_change_pct=40.0),
        Objective.REVENUE,
    )
    assert not result.hold and result.feasible_count > 0
    margin_pct = (result.recommended_price - 2.0) / result.recommended_price * 100
    assert margin_pct >= 40.0 - 1e-6


def test_a_hold_is_returned_rather_than_the_least_bad_violation():
    """When the current price itself breaches, the optimizer holds and the rule
    engine flags it — it does not invent a compliant-looking alternative outside
    the permitted change band. Holding is the honest output; the veto is what
    stops it shipping."""
    sim = simulate("A", [2.1, 2.5, 3.0, 4.0], 3.0, 2.0, 100.0, usable_estimate(),
                   iterations=400)
    result = optimize(
        sim, 2.0,
        PriceConstraints(margin_floor_pct=40.0, max_change_pct=10.0),
        Objective.REVENUE,
    )
    assert result.hold and result.recommended_price == result.current_price

    from pricing.rules.engine import RuleConfig, evaluate

    verdict = evaluate("A", result.recommended_price, 3.0, 2.0, "Beverages",
                       config=RuleConfig(margin_floor_pct=40.0))
    assert not verdict.passed, "a held-but-breaching price escaped the veto"


def test_no_feasible_candidate_means_hold_not_least_bad_violation():
    sim = simulate("A", [2.05, 2.10], 2.10, 2.0, 100.0, usable_estimate(), iterations=400)
    result = optimize(sim, 2.0, PriceConstraints(margin_floor_pct=90.0))
    assert result.hold and result.feasible_count == 0
    assert result.recommended_price == result.current_price


def test_map_price_is_a_hard_floor():
    sim = simulate("A", [3.0, 3.5, 4.0], 4.0, 1.0, 100.0, usable_estimate(-2.5),
                   iterations=600)
    result = optimize(sim, 1.0, PriceConstraints(map_price=3.5), Objective.REVENUE)
    assert result.recommended_price >= 3.5


def test_damping_shrinks_the_permitted_move():
    """FR-088/FR-091 — the optimizer must honour the stability leash."""
    wide = candidate_grid(10.0, 10.0, damping_factor=1.0)
    damped = candidate_grid(10.0, 10.0, damping_factor=0.3)
    assert (damped.max() - damped.min()) < (wide.max() - wide.min())


def test_objectives_are_distinguishable():
    sim = simulate("A", [3.0, 3.5, 4.0, 4.5], 3.5, 2.0, 100.0, usable_estimate(-1.4),
                   iterations=900)
    revenue = optimize(sim, 2.0, PriceConstraints(max_change_pct=40.0), Objective.REVENUE)
    margin = optimize(sim, 2.0, PriceConstraints(max_change_pct=40.0), Objective.MARGIN)
    assert margin.recommended_price >= revenue.recommended_price


def test_candidate_grid_does_not_collapse_across_magnitudes():
    """DEVIATIONS D-04: a collapsed grid turns the optimizer into a silent no-op.

    Checked across magnitudes because the failure was magnitude-dependent — a
    fixed .49/.99 snap leaves a low-priced item with exactly one valid point in
    its band, and the optimizer then "recommends" no change on most of the
    catalog while looking merely conservative.
    """
    for price in (1.49, 2.49, 4.10, 12.99, 24.00, 47.50, 89.00):
        grid = candidate_grid(price, 10.0)
        assert grid.size >= 3, f"grid collapsed at {price}: {grid}"
        assert grid.min() < price < grid.max(), f"band is one-sided at {price}"


def test_a_narrow_band_may_legitimately_hold_one_point():
    """The corollary. At £0.99 a +/-10% band spans 0.891-1.089, and the only
    shelf point inside it is 0.99 itself. That is the ladder being correct, not
    the D-04 bug returning — so it is asserted rather than left ambiguous."""
    grid = candidate_grid(0.99, 10.0)
    assert list(grid) == [0.99]
