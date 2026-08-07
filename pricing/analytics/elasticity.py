"""Layer 1 — causal: price elasticity of demand (FR-012, FR-013, FR-014).

Estimates how demand responds to price by regressing log units on log price:

    log(units) = a + e*log(price) + controls + noise

where `e` is the elasticity. Everything downstream depends on this being right:
Monte Carlo samples around it, and the optimizer chooses against it.

**Why the controls matter.** Retailers cut price and run a display at the same
time. A naive log-log fit attributes the display lift to the price cut and
over-states elasticity — measured at bias -0.45 on this dataset, against +0.003
for the controlled specification below. Omitting the promo dummy does not make
the estimate slightly noisier; it makes it systematically wrong in a direction
that causes over-discounting.

Controls used: promotion indicator, day-of-week, and month (seasonality).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

# Below this many usable observations the fit is not trustworthy at any
# confidence level, and the SKU is excluded from confident recommendation.
MIN_OBSERVATIONS = 60

# Elasticity outside this range is economically implausible for retail and
# almost always signals a data problem rather than a real finding.
ELASTICITY_FLOOR = -6.0
ELASTICITY_CEILING = -0.05

# A price series flatter than this cannot identify elasticity: there is no
# variation to fit against, so any coefficient is noise.
MIN_PRICE_CV = 0.015


@dataclass
class ElasticityEstimate:
    sku: str
    elasticity: float
    ci_low: float
    ci_high: float
    std_error: float
    r_squared: float
    sample_size: int
    price_cv: float
    usable: bool
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ci_width(self) -> float:
        return abs(self.ci_high - self.ci_low)

    @property
    def confidence(self) -> float:
        """Map interval width to a 0-1 confidence score (FR-021).

        A half-unit-wide interval on elasticity is genuinely good; three units
        wide is close to useless. Linear between those anchors.
        """
        if not self.usable:
            return 0.0
        width = self.ci_width
        score = 1.0 - (width - 0.5) / 2.5
        return round(float(np.clip(score, 0.05, 0.98)), 4)


def _unusable(sku: str, reason: str, n: int = 0, cv: float = 0.0) -> ElasticityEstimate:
    return ElasticityEstimate(
        sku=sku, elasticity=float("nan"), ci_low=float("nan"), ci_high=float("nan"),
        std_error=float("nan"), r_squared=0.0, sample_size=n, price_cv=cv,
        usable=False, reason=reason,
    )


def estimate_elasticity(
    sku: str,
    units: np.ndarray,
    prices: np.ndarray,
    on_promo: np.ndarray | None = None,
    weekday: np.ndarray | None = None,
    month: np.ndarray | None = None,
    confidence_level: float = 0.95,
) -> ElasticityEstimate:
    """Fit the controlled log-log demand model for one SKU."""
    units = np.asarray(units, dtype=float)
    prices = np.asarray(prices, dtype=float)

    # Zero-unit days carry no information in log space and cannot be logged.
    mask = (units > 0) & (prices > 0) & np.isfinite(units) & np.isfinite(prices)
    n_raw = int(mask.sum())
    if n_raw < MIN_OBSERVATIONS:
        return _unusable(
            sku,
            f"Only {n_raw} usable observations; {MIN_OBSERVATIONS} required.",
            n_raw,
        )

    units, prices = units[mask], prices[mask]
    price_cv = float(prices.std() / prices.mean()) if prices.mean() > 0 else 0.0
    if price_cv < MIN_PRICE_CV:
        return _unusable(
            sku,
            f"Price varies by only {price_cv:.3%} over the sample; elasticity is "
            "not identifiable without price movement.",
            n_raw,
            price_cv,
        )

    y = np.log(units)
    columns = [np.ones(len(y)), np.log(prices)]
    labels = ["const", "log_price"]

    def add_control(values, builder, prefix, levels):
        if values is None:
            return
        arr = np.asarray(values)[mask]
        for lvl in levels:
            col = builder(arr, lvl)
            # Skip constant columns — they are collinear with the intercept.
            if 0 < col.sum() < len(col):
                columns.append(col)
                labels.append(f"{prefix}{lvl}")

    if on_promo is not None:
        promo = np.asarray(on_promo, dtype=float)[mask]
        if 0 < promo.sum() < len(promo):
            columns.append(promo)
            labels.append("on_promo")

    add_control(weekday, lambda a, l: (a == l).astype(float), "dow_", range(1, 7))
    add_control(month, lambda a, l: (a == l).astype(float), "mon_", range(2, 13))

    X = np.column_stack(columns)
    n, k = X.shape
    if n - k < 10:
        return _unusable(
            sku, f"Too few degrees of freedom ({n - k}) after controls.", n, price_cv
        )
    if np.linalg.matrix_rank(X) < k:
        # Fall back to price-only rather than failing outright.
        X = np.column_stack([np.ones(len(y)), np.log(prices)])
        labels = ["const", "log_price"]
        n, k = X.shape

    beta, residuals, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ beta
    resid = y - fitted
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - rss / tss if tss > 0 else 0.0

    dof = n - k
    sigma2 = rss / dof
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        return _unusable(sku, "Design matrix is singular.", n, price_cv)

    se = float(np.sqrt(max(sigma2 * xtx_inv[1, 1], 0.0)))
    elasticity = float(beta[1])
    t_crit = float(stats.t.ppf(0.5 + confidence_level / 2, dof))
    ci_low, ci_high = elasticity - t_crit * se, elasticity + t_crit * se

    warnings: list[str] = []
    if elasticity > ELASTICITY_CEILING:
        warnings.append(
            f"Estimated elasticity {elasticity:.2f} is near zero or positive, which is "
            "economically implausible for retail. Treat as unreliable."
        )
    if elasticity < ELASTICITY_FLOOR:
        warnings.append(
            f"Estimated elasticity {elasticity:.2f} is extreme; likely a data artefact."
        )
    if r_squared < 0.10:
        warnings.append(f"Model explains little variance (R^2={r_squared:.2f}).")
    if "on_promo" not in labels:
        warnings.append(
            "No promotion control available — elasticity may be over-stated."
        )

    usable = ELASTICITY_FLOOR <= elasticity <= ELASTICITY_CEILING

    return ElasticityEstimate(
        sku=sku,
        elasticity=round(elasticity, 4),
        ci_low=round(ci_low, 4),
        ci_high=round(ci_high, 4),
        std_error=round(se, 4),
        r_squared=round(r_squared, 4),
        sample_size=n,
        price_cv=round(price_cv, 4),
        usable=usable,
        reason="" if usable else "Estimate outside plausible elasticity range.",
        warnings=warnings,
    )


def estimate_from_records(
    sku: str, records: list[dict], confidence_level: float = 0.95
) -> ElasticityEstimate:
    """Convenience wrapper over Commerce Service sales rows."""
    if not records:
        return _unusable(sku, "No sales history returned.")

    units = np.array([r["units"] for r in records], dtype=float)
    prices = np.array([r["unit_price"] for r in records], dtype=float)
    promo = np.array([1.0 if r.get("on_promo") else 0.0 for r in records])

    dates = np.array(
        [np.datetime64(str(r["sale_date"])[:10]) for r in records],
        dtype="datetime64[D]",
    )
    # 1970-01-01 was a Thursday; shift so Monday = 0 to match ISO weekday.
    weekday = ((dates.astype("datetime64[D]").astype(int) + 3) % 7).astype(int)
    month = dates.astype("datetime64[M]").astype(int) % 12 + 1

    return estimate_elasticity(
        sku, units, prices, promo, weekday, month, confidence_level
    )
