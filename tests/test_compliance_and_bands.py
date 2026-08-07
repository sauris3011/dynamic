"""The compliance veto and the autonomy bands (FR-030..037, FR-089..104).

The central invariant this file defends: **autonomy changes who approves, never
what is verified** (NFR-040). Every mode, every band, every path — a rule
violation is blocked. If any test here goes green while the invariant is broken,
the test is wrong, not the invariant.
"""

from __future__ import annotations

import pytest

from pricing.config import OperatingMode
from pricing.rules.bands import (
    Band,
    BandThresholds,
    assign_band,
    may_push_automatically,
)
from pricing.rules.engine import RuleConfig, evaluate

CLEAN = dict(
    sku="A", compliance_passed=True, compliance_summary="All 6 rules passed.",
    confidence=0.92, delta_pct=3.0, revenue_cv=0.10, margin_pct=40.0,
    elasticity_samples=200, elasticity_usable=True, oscillating=False,
)


# --- Rule engine ----------------------------------------------------------

def test_every_rule_is_recorded_pass_or_fail():
    """FR-035 — an audit needs the passes too, not only the breaches."""
    # Family sibling is smaller (0.5) and cheaper (3.00) than this 1.0 at 4.00 —
    # a coherent ladder, so the check passes rather than firing.
    verdict = evaluate("A", 4.00, 4.00, 2.00, "Beverages", map_price=3.50,
                       family_prices=[(0.5, 3.00)], size_value=1.0)
    assert verdict.passed, verdict.summary
    codes = {e.code for e in verdict.evaluations}
    assert {"MARGIN_FLOOR", "MAP_FLOOR", "MAX_CHANGE", "ABOVE_COST"} <= codes
    assert all(e.detail for e in verdict.evaluations)


def test_below_map_is_a_violation():
    verdict = evaluate("A", 3.20, 3.50, 1.00, "Beverages", map_price=3.50)
    assert not verdict.passed
    assert "MAP_FLOOR" in verdict.violation_codes


def test_below_cost_is_a_violation():
    verdict = evaluate("A", 1.50, 4.00, 2.00, "Beverages")
    assert not verdict.passed
    assert {"ABOVE_COST", "MARGIN_FLOOR"} <= set(verdict.violation_codes)


def test_ladder_break_is_caught():
    """A larger pack priced under a smaller one in the same family."""
    verdict = evaluate("A", 2.00, 2.00, 0.50, "Beverages",
                       family_prices=[(0.5, 2.50)], size_value=1.0)
    assert "PRICE_LADDER" in verdict.violation_codes


def test_rules_are_configurable_without_code_change():
    """FR-032 — thresholds come from config, not constants in the engine."""
    strict = RuleConfig(margin_floor_pct=60.0)
    assert evaluate("A", 4.00, 4.00, 2.00, "Beverages").passed
    assert not evaluate("A", 4.00, 4.00, 2.00, "Beverages", config=strict).passed


def test_category_bounds_apply_when_configured():
    cfg = RuleConfig(category_max_price={"Beverages": 3.00})
    verdict = evaluate("A", 4.00, 4.00, 1.00, "Beverages", config=cfg)
    assert "CATEGORY_CEILING" in verdict.violation_codes


# --- Band assignment ------------------------------------------------------

def test_clean_confident_stable_recommendation_auto_approves():
    assert assign_band(**CLEAN).band is Band.AUTO_APPROVE


def test_compliance_violation_always_escalates():
    result = assign_band(**{**CLEAN, "compliance_passed": False,
                            "compliance_summary": "MAP breach"})
    assert result.band is Band.ESCALATE
    assert "compliance_violation" in result.triggers


@pytest.mark.parametrize(
    "override,trigger",
    [
        ({"margin_pct": 16.0}, "margin_proximity"),
        ({"revenue_cv": 0.90}, "high_volatility"),
        ({"elasticity_usable": False}, "unprecedented_scenario"),
        ({"oscillating": True}, "oscillation"),
    ],
)
def test_each_escalation_trigger_fires(override, trigger):
    """FR-099 — all five triggers, individually, on an otherwise clean item."""
    result = assign_band(**{**CLEAN, **override})
    assert result.band is Band.ESCALATE
    assert trigger in result.triggers


@pytest.mark.parametrize(
    "override,expected_trigger",
    [
        ({"confidence": 0.40}, "low_confidence"),
        ({"delta_pct": 25.0}, "large_delta"),
        ({"elasticity_samples": 10}, "thin_evidence"),
        ({"quality_warned": True}, "quality_warning"),
    ],
)
def test_failing_an_auto_approve_condition_lands_in_review(override, expected_trigger):
    result = assign_band(**{**CLEAN, **override})
    assert result.band is Band.REVIEW
    assert expected_trigger in result.triggers


def test_band_reason_states_threshold_and_actual():
    """FR-096 — 'why', with numbers, not just 'what'."""
    result = assign_band(**{**CLEAN, "confidence": 0.40})
    assert result.decisive_threshold == pytest.approx(0.75)
    assert result.decisive_actual == pytest.approx(0.40)
    assert "0.75" in result.reason and "0.40" in result.reason


def test_thresholds_are_configurable():
    """FR-100 — retuning the bands must not require a code change."""
    lenient = BandThresholds(min_confidence=0.30)
    assert assign_band(**{**CLEAN, "confidence": 0.40}).band is Band.REVIEW
    assert assign_band(**{**CLEAN, "confidence": 0.40},
                       thresholds=lenient).band is Band.AUTO_APPROVE


# --- Mode gating ----------------------------------------------------------

def test_escalate_never_pushes_automatically_in_any_mode():
    """The invariant the entire autonomy model rests on (FR-033, NFR-040)."""
    for mode in OperatingMode:
        assert may_push_automatically(Band.ESCALATE, mode) is False


def test_supervised_pushes_nothing_automatically():
    for band in Band:
        assert may_push_automatically(band, OperatingMode.SUPERVISED) is False


def test_assisted_pushes_only_the_auto_approve_band():
    assert may_push_automatically(Band.AUTO_APPROVE, OperatingMode.ASSISTED)
    assert not may_push_automatically(Band.REVIEW, OperatingMode.ASSISTED)


def test_autonomous_pushes_auto_approve_and_review_but_not_escalate():
    mode = OperatingMode.AUTONOMOUS
    assert may_push_automatically(Band.AUTO_APPROVE, mode)
    assert may_push_automatically(Band.REVIEW, mode)
    assert not may_push_automatically(Band.ESCALATE, mode)
