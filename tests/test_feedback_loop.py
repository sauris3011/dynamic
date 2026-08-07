"""The closed loop: bounded refinement and convergence (FR-107 .. FR-111).

An unbounded update rule fed by its own downstream consequences diverges. These
tests exist to prove the caps are real, because a feedback loop that drifts is
worse than none at all — it looks like progress.
"""

from __future__ import annotations

import pytest

from pricing.analytics.refinement import (
    MAX_TOTAL_DRIFT,
    Observation,
    dispersion_scale,
    posterior_interval,
    refine_elasticity,
)
from pricing.analytics.stability import assess_convergence, detect_oscillation

PRIOR = dict(sku="A", prior_elasticity=-2.0, prior_ci_low=-2.5, prior_ci_high=-1.5,
             prior_sample_size=180)


def observation(p0=4.0, p1=3.6, q0=100.0, q1=120.0) -> Observation:
    return Observation("A", p0, p1, q0, q1)


# --- Arc elasticity from a realized move ----------------------------------

def test_arc_elasticity_matches_the_observed_move():
    # -10% price, +11.1% units -> roughly unit elastic.
    obs = observation(p0=4.0, p1=3.6, q0=100.0, q1=111.0)
    assert obs.arc_elasticity() == pytest.approx(-1.0, abs=0.1)


def test_a_price_that_did_not_move_teaches_nothing():
    assert observation(p0=4.0, p1=4.0).arc_elasticity() is None


def test_implausible_observations_are_discarded_not_absorbed():
    """A stockout or a competitor clearance is noise, not evidence."""
    assert observation(p0=4.0, p1=3.99, q0=10.0, q1=900.0).arc_elasticity() is None


# --- Bounded refinement ---------------------------------------------------

def test_refinement_moves_the_estimate_toward_the_observation():
    result = refine_elasticity(**PRIOR, observations=[observation()])
    assert result.applied
    assert result.posterior_elasticity != PRIOR["prior_elasticity"]
    assert result.reason


def test_a_single_observation_cannot_overturn_the_prior():
    """FR-111. One wild fortnight must not move the belief far."""
    wild = [observation(p0=4.0, p1=3.99, q0=100.0, q1=101.0)] * 3
    result = refine_elasticity(**PRIOR, observations=wild)
    half_width = abs(PRIOR["prior_ci_high"] - PRIOR["prior_ci_low"]) / 2
    assert abs(result.step) <= half_width * 0.25 + 1e-9


def test_cumulative_drift_is_capped():
    """No sequence of updates may walk the estimate away without limit.

    The observation is a plausible one (-10% price, +45% units -> arc ≈ -3.8):
    an implausible one would be discarded by the sanity filter and would test
    that filter rather than the cap.
    """
    # Prior drift is negative and the new step is negative too, so this pushes
    # *further* out. A step back toward the original estimate is never capped —
    # the cap bounds distance from the regression, not movement as such.
    already = -(MAX_TOTAL_DRIFT - 0.01)
    result = refine_elasticity(
        **PRIOR, observations=[observation(q1=145.0)],
        total_adjustment_so_far=already,
    )
    assert result.applied, result.reason
    assert result.step < 0, "expected the observation to push the estimate lower"
    assert abs(already + result.step) <= MAX_TOTAL_DRIFT + 1e-9
    assert result.capped


def test_a_step_back_toward_the_prior_is_not_capped():
    """The cap bounds drift from the original regression, not motion.

    Without this, a system that had drifted to its limit could never recover
    when fresh evidence pointed home — the cap would lock in the error it
    exists to prevent.
    """
    result = refine_elasticity(
        **PRIOR, observations=[observation(q1=104.0)],
        total_adjustment_so_far=-(MAX_TOTAL_DRIFT - 0.01),
    )
    assert result.applied and result.step > 0
    assert not result.capped


def test_a_large_prior_sample_resists_more_than_a_small_one():
    """The correct asymmetry: 400 days of history is not overturned by a fortnight."""
    obs = [observation(q1=140.0)]
    thin = refine_elasticity(**{**PRIOR, "prior_sample_size": 65}, observations=obs)
    thick = refine_elasticity(**{**PRIOR, "prior_sample_size": 800}, observations=obs)
    assert abs(thin.step) >= abs(thick.step)


def test_no_usable_observation_is_a_no_op_with_a_reason():
    result = refine_elasticity(**PRIOR, observations=[observation(p0=4.0, p1=4.0)])
    assert not result.applied and result.step == 0.0
    assert "observations" in result.reason


def test_the_interval_narrows_but_never_collapses():
    result = refine_elasticity(**PRIOR, observations=[observation()] * 5)
    assert result.posterior_ci_width < result.prior_ci_width
    assert result.posterior_ci_width >= 0.24


def test_posterior_interval_brackets_the_estimate():
    low, high = posterior_interval(-1.8, 0.6)
    assert low < -1.8 < high


def test_every_refinement_is_fully_reportable():
    """FR-111 — inspectable, not a black box."""
    result = refine_elasticity(**PRIOR, observations=[observation()])
    payload = result.to_dict()
    for key in ("prior_elasticity", "posterior_elasticity", "step", "capped",
                "raw_observation", "observations_used", "reason"):
        assert key in payload


# --- Dispersion refinement (FR-108) ---------------------------------------

def test_dispersion_is_neutral_without_enough_evidence():
    assert dispersion_scale([5.0, 6.0]) == 1.0


def test_consistently_tight_forecasts_narrow_the_interval():
    assert dispersion_scale([3.0, -2.0, 4.0, -3.0, 2.0, 1.0]) < 1.0


def test_consistently_wide_misses_widen_the_interval():
    assert dispersion_scale([40.0, -38.0, 45.0, -41.0, 39.0, 44.0]) > 1.0


def test_dispersion_scale_is_clamped_at_both_ends():
    assert dispersion_scale([0.0] * 10) >= 0.6
    assert dispersion_scale([500.0] * 10) <= 1.8


# --- Stability and convergence --------------------------------------------

def test_oscillation_is_detected_across_runs_not_within_one():
    """A max-change cap cannot prevent ping-ponging; this is what does."""
    ping_pong = detect_oscillation("A", [4.0, 3.6, 4.0, 3.6, 4.0], max_reversals=2)
    trending = detect_oscillation("A", [4.4, 4.3, 4.2, 4.1, 4.0], max_reversals=2)
    assert ping_pong.oscillating and not trending.oscillating
    assert ping_pong.damping_factor < 1.0 and trending.damping_factor == 1.0


def test_too_little_history_is_not_an_oscillation_claim():
    signal = detect_oscillation("A", [4.0])
    assert not signal.oscillating and "Not enough" in signal.detail


def test_convergence_reports_narrowing_error():
    signal = assess_convergence([30.0, -28.0, 25.0, 9.0, -7.0, 6.0])
    assert signal.converging and signal.recent_error_pct < signal.earlier_error_pct


def test_widening_error_raises_a_visible_warning():
    """A drifting loop must say so rather than be smoothed away (FR-110)."""
    signal = assess_convergence([4.0, -5.0, 6.0, 30.0, -35.0, 40.0])
    assert not signal.converging
    assert "WARNING" in signal.detail


def test_convergence_withholds_judgement_on_thin_evidence():
    signal = assess_convergence([10.0, 12.0])
    assert not signal.converging and "needed to judge" in signal.detail
