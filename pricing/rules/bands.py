"""Autonomy band assignment (FR-089, FR-099 .. FR-104).

Separates two things that are easily conflated:

* **Enforcement** — whether compliance rules apply — is never relaxed. A
  compliance violation lands in Escalate in every mode and can never be
  auto-approved or overridden away.
* **Approval** — who signs off on a *compliant* recommendation — is graduated by
  risk. That is what makes human attention scarce and therefore valuable: the
  reviewer sees the forty recommendations that need judgement, not the five
  hundred that do not.

The three Escalate triggers from the source architecture document are preserved
verbatim: high volatility, margin proximity, and unprecedented scenarios.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pricing.config import OperatingMode


class Band(str, Enum):
    AUTO_APPROVE = "auto_approve"
    REVIEW = "review"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class BandThresholds:
    min_confidence: float = 0.75
    max_delta_pct: float = 10.0
    max_variance: float = 0.25          # revenue coefficient of variation
    margin_buffer_pct: float = 3.0      # proximity to the margin floor
    margin_floor_pct: float = 15.0
    min_elasticity_samples: int = 60


@dataclass
class BandAssignment:
    band: Band
    reason: str
    triggers: list[str]
    # Threshold vs actual for the condition that decided it, so the UI can show
    # *why* rather than just *what* (FR-096).
    decisive_threshold: float | None = None
    decisive_actual: float | None = None

    @property
    def requires_human(self) -> bool:
        return self.band in (Band.REVIEW, Band.ESCALATE)


def assign_band(
    *,
    sku: str,
    compliance_passed: bool,
    compliance_summary: str,
    confidence: float,
    delta_pct: float,
    revenue_cv: float,
    margin_pct: float,
    elasticity_samples: int,
    elasticity_usable: bool,
    oscillating: bool,
    quality_warned: bool = False,
    thresholds: BandThresholds | None = None,
) -> BandAssignment:
    """Deterministically place one recommendation in exactly one band."""
    t = thresholds or BandThresholds()
    triggers: list[str] = []

    # ---- Escalate: any one of these is sufficient (FR-099) --------------

    # 1. Compliance violation. Unconditional, mode-independent.
    if not compliance_passed:
        return BandAssignment(
            Band.ESCALATE,
            f"Compliance violation — {compliance_summary}. Blocked in every "
            "operating mode and cannot be overridden.",
            ["compliance_violation"],
        )

    # 2. Margin proximity.
    margin_headroom = margin_pct - t.margin_floor_pct
    if margin_headroom < t.margin_buffer_pct:
        triggers.append("margin_proximity")
        return BandAssignment(
            Band.ESCALATE,
            f"Margin {margin_pct:.1f}% sits only {margin_headroom:.1f} points above "
            f"the {t.margin_floor_pct:.1f}% floor, inside the "
            f"{t.margin_buffer_pct:.1f}-point buffer.",
            triggers, t.margin_buffer_pct, round(margin_headroom, 2),
        )

    # 3. High volatility.
    if revenue_cv > t.max_variance:
        triggers.append("high_volatility")
        return BandAssignment(
            Band.ESCALATE,
            f"Simulated revenue variance {revenue_cv:.2f} exceeds the "
            f"{t.max_variance:.2f} volatility limit — the outcome is too uncertain "
            "to act on unsupervised.",
            triggers, t.max_variance, revenue_cv,
        )

    # 4. Unprecedented scenario — no trustworthy demand model for this SKU.
    if not elasticity_usable:
        triggers.append("unprecedented_scenario")
        return BandAssignment(
            Band.ESCALATE,
            "No usable elasticity estimate for this SKU, so the recommendation "
            "rests on a prior rather than on evidence.",
            triggers,
        )

    # 5. Oscillation.
    if oscillating:
        triggers.append("oscillation")
        return BandAssignment(
            Band.ESCALATE,
            "Price is oscillating across recent runs; repricing again risks "
            "reinforcing the cycle.",
            triggers,
        )

    # ---- Review: fails an auto-approve condition ------------------------
    if confidence < t.min_confidence:
        return BandAssignment(
            Band.REVIEW,
            f"Confidence {confidence:.2f} is below the {t.min_confidence:.2f} "
            "auto-approve threshold.",
            ["low_confidence"], t.min_confidence, confidence,
        )

    if abs(delta_pct) > t.max_delta_pct:
        return BandAssignment(
            Band.REVIEW,
            f"Price change {delta_pct:+.1f}% exceeds the {t.max_delta_pct:.1f}% "
            "auto-approve limit.",
            ["large_delta"], t.max_delta_pct, abs(delta_pct),
        )

    if elasticity_samples < t.min_elasticity_samples:
        return BandAssignment(
            Band.REVIEW,
            f"Elasticity fitted on {elasticity_samples} observations, below the "
            f"{t.min_elasticity_samples} needed for automatic approval.",
            ["thin_evidence"], t.min_elasticity_samples, elasticity_samples,
        )

    if quality_warned:
        return BandAssignment(
            Band.REVIEW,
            "Data quality warnings were raised for this run, so automatic "
            "approval is withheld.",
            ["quality_warning"],
        )

    # ---- Auto-approve ---------------------------------------------------
    return BandAssignment(
        Band.AUTO_APPROVE,
        f"Confidence {confidence:.2f}, change {delta_pct:+.1f}%, variance "
        f"{revenue_cv:.2f}, margin {margin_pct:.1f}% — all inside auto-approve "
        "limits with a clean compliance verdict.",
        [],
    )


def may_push_automatically(band: Band, mode: OperatingMode) -> bool:
    """Does this band push without human action in this mode (FR-101)?

    Escalate never pushes automatically, in any mode. That is the invariant the
    whole autonomy model rests on.
    """
    if band is Band.ESCALATE:
        return False
    if mode is OperatingMode.SUPERVISED:
        return False
    if mode is OperatingMode.ASSISTED:
        return band is Band.AUTO_APPROVE
    # AUTONOMOUS: auto-approve immediately, review after its hold window.
    return band in (Band.AUTO_APPROVE, Band.REVIEW)
