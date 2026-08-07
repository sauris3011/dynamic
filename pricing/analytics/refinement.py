"""Bounded refinement of the causal and uncertainty layers (FR-107, FR-108, FR-111).

The closed loop is the difference between a system that *reports* and one that
*learns*. After a price goes live, the realized outcome is evidence about the
demand response we estimated — evidence the static regression never saw, because
it only ever observed prices somebody else chose.

Two things get refined, and they are deliberately separate:

* **The elasticity estimate** (Layer 1). A realized quantity change against a
  known price change is a direct observation of the arc elasticity. It is folded
  into the prior by inverse-variance weighting rather than replacing it.
* **The Monte Carlo dispersion** (Layer 2). Observed forecast error tells us how
  wrong our intervals have been. If we were consistently inside the band, the
  band was too wide; if we kept missing, it was too narrow.

**Why every adjustment is capped and logged (FR-111).** An unbounded update rule
fed by its own downstream consequences is a feedback loop in the control-theory
sense, and those diverge cheerfully. A single anomalous fortnight — a stockout, a
competitor's clearance event — must not be able to move a SKU's elasticity by
more than a fraction of its interval. The cap is the difference between learning
and drifting, and drifting is worse than not learning at all because it looks
like progress.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# No single observation may move an elasticity estimate by more than this
# fraction of its own confidence-interval half-width. Deliberately small.
MAX_STEP_FRACTION = 0.25

# Cumulative guard: refinement may never carry an estimate further than this
# from the regression value the static history produced.
MAX_TOTAL_DRIFT = 0.75

# Arc elasticities outside this range are treated as noise, not evidence.
OBSERVATION_FLOOR = -8.0
OBSERVATION_CEILING = -0.02

# Dispersion multipliers are clamped so the interval can neither collapse to a
# point estimate nor blow out to uselessness.
MIN_DISPERSION_SCALE = 0.6
MAX_DISPERSION_SCALE = 1.8


@dataclass(frozen=True)
class Observation:
    """One realized price change and what demand did about it."""

    sku: str
    price_before: float
    price_after: float
    units_before: float
    units_after: float

    @property
    def usable(self) -> bool:
        return (
            self.price_before > 0
            and self.price_after > 0
            and self.units_before > 0
            and self.units_after > 0
            and abs(self.price_after - self.price_before) / self.price_before > 0.005
        )

    def arc_elasticity(self) -> float | None:
        """Log-ratio (arc) elasticity for a single observed move.

        `log(q1/q0) / log(p1/p0)` — the two-point estimate of the same quantity
        the regression fits across the whole history. Noisy on its own, which is
        exactly why it is weighted rather than trusted.
        """
        if not self.usable:
            return None
        try:
            dq = math.log(self.units_after / self.units_before)
            dp = math.log(self.price_after / self.price_before)
        except ValueError:
            return None
        if abs(dp) < 1e-6:
            return None
        value = dq / dp
        if not math.isfinite(value):
            return None
        if value < OBSERVATION_FLOOR or value > OBSERVATION_CEILING:
            return None
        return value


@dataclass
class RefinementResult:
    """What the update did, in full — this is what makes it inspectable."""

    sku: str
    applied: bool
    prior_elasticity: float
    posterior_elasticity: float
    raw_observation: float | None
    step: float
    capped: bool
    prior_ci_width: float
    posterior_ci_width: float
    observations_used: int
    reason: str

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "applied": self.applied,
            "prior_elasticity": round(self.prior_elasticity, 4),
            "posterior_elasticity": round(self.posterior_elasticity, 4),
            "raw_observation": (
                round(self.raw_observation, 4) if self.raw_observation is not None else None
            ),
            "step": round(self.step, 4),
            "capped": self.capped,
            "prior_ci_width": round(self.prior_ci_width, 4),
            "posterior_ci_width": round(self.posterior_ci_width, 4),
            "observations_used": self.observations_used,
            "reason": self.reason,
        }


def refine_elasticity(
    sku: str,
    prior_elasticity: float,
    prior_ci_low: float,
    prior_ci_high: float,
    prior_sample_size: int,
    observations: list[Observation],
    total_adjustment_so_far: float = 0.0,
) -> RefinementResult:
    """Fold realized outcomes into an elasticity estimate (FR-107).

    Weighting is inverse-variance in spirit: the prior carries the weight of its
    regression sample, each observation carries the weight of one. A SKU fitted
    on 400 days does not get overturned by a fortnight; a SKU fitted on 65 days
    moves more, which is the correct asymmetry.
    """
    half_width = max(abs(prior_ci_high - prior_ci_low) / 2.0, 1e-6)
    usable = [o.arc_elasticity() for o in observations]
    values = [v for v in usable if v is not None]

    if not values:
        return RefinementResult(
            sku, False, prior_elasticity, prior_elasticity, None, 0.0, False,
            half_width * 2, half_width * 2, 0,
            "No usable price/demand observations — a price must actually have "
            "moved, with sales on both sides of the change.",
        )

    observed = sum(values) / len(values)

    # Prior weight is the regression sample; observations are worth a fixed
    # number of days each so the update is meaningful without being violent.
    prior_weight = max(float(prior_sample_size), 1.0)
    observation_weight = 14.0 * len(values)
    target = (
        prior_elasticity * prior_weight + observed * observation_weight
    ) / (prior_weight + observation_weight)

    step = target - prior_elasticity

    # --- Cap 1: per-update step, relative to the estimate's own precision ----
    max_step = MAX_STEP_FRACTION * half_width
    capped = abs(step) > max_step
    if capped:
        step = math.copysign(max_step, step)

    # --- Cap 2: cumulative drift from the original regression ----------------
    projected_total = total_adjustment_so_far + step
    if abs(projected_total) > MAX_TOTAL_DRIFT:
        allowed = math.copysign(MAX_TOTAL_DRIFT, projected_total) - total_adjustment_so_far
        step = allowed if abs(allowed) < abs(step) else step
        capped = True

    posterior = prior_elasticity + step
    posterior = min(max(posterior, OBSERVATION_FLOOR), OBSERVATION_CEILING)

    # Evidence narrows the interval, but only a little per update and never
    # below a floor — certainty must be earned across many observations.
    shrink = 1.0 - min(0.06 * len(values), 0.18)
    new_half_width = max(half_width * shrink, 0.12)

    return RefinementResult(
        sku=sku,
        applied=abs(step) > 1e-6,
        prior_elasticity=prior_elasticity,
        posterior_elasticity=posterior,
        raw_observation=observed,
        step=step,
        capped=capped,
        prior_ci_width=half_width * 2,
        posterior_ci_width=new_half_width * 2,
        observations_used=len(values),
        reason=(
            f"{len(values)} realized price move(s) implied elasticity {observed:.2f} "
            f"against a prior of {prior_elasticity:.2f}; applied a "
            f"{'capped ' if capped else ''}step of {step:+.3f}."
        ),
    )


def posterior_interval(
    posterior_elasticity: float, posterior_ci_width: float
) -> tuple[float, float]:
    half = posterior_ci_width / 2.0
    return posterior_elasticity - half, posterior_elasticity + half


def dispersion_scale(forecast_errors: list[float], min_samples: int = 4) -> float:
    """Refine the Monte Carlo dispersion from observed forecast error (FR-108).

    Our stated 90% band should contain the realized outcome roughly 90% of the
    time. This returns a multiplier on the demand-shock standard deviation:
    below 1.0 when we have been conservatively wide, above 1.0 when reality kept
    landing outside the band. Clamped at both ends — an interval that collapses
    to a point is a lie, and one that widens without limit is useless.
    """
    errors = [abs(e) for e in forecast_errors if e is not None and math.isfinite(e)]
    if len(errors) < min_samples:
        return 1.0

    mean_error = sum(errors) / len(errors)
    # 16% mean absolute forecast error is the dispersion the default
    # UncertaintyInputs assume; scale proportionally against that anchor.
    scale = mean_error / 16.0
    return round(min(max(scale, MIN_DISPERSION_SCALE), MAX_DISPERSION_SCALE), 4)
