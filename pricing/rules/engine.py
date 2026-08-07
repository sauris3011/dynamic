"""Compliance rule engine (FR-030 .. FR-035).

Every recommendation passes through here. A violation blocks it outright: it
cannot be force-pushed, overridden, or auto-approved, in any operating mode
(FR-033, NFR-040). Autonomy governs *who approves* a compliant price; it never
governs *whether the rules apply*.

Rules are data-driven (FR-032) so thresholds can change without code edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuleConfig:
    """Tunable thresholds. Persisted in settings_kv and editable at runtime."""

    margin_floor_pct: float = 15.0
    max_change_pct: float = 10.0
    enforce_map: bool = True
    enforce_ladder: bool = True
    enforce_charm: bool = False       # advisory by default; a .37 price is odd, not illegal
    category_min_price: dict[str, float] = field(default_factory=dict)
    category_max_price: dict[str, float] = field(default_factory=dict)


@dataclass
class RuleEval:
    code: str
    passed: bool
    actual_value: float | None
    threshold: float | None
    detail: str


@dataclass
class ComplianceVerdict:
    sku: str
    passed: bool
    evaluations: list[RuleEval]

    @property
    def violations(self) -> list[RuleEval]:
        return [e for e in self.evaluations if not e.passed]

    @property
    def violation_codes(self) -> list[str]:
        return [e.code for e in self.violations]

    @property
    def summary(self) -> str:
        if self.passed:
            return f"All {len(self.evaluations)} rules passed."
        return "; ".join(e.detail for e in self.violations)


def evaluate(
    sku: str,
    proposed_price: float,
    current_price: float,
    unit_cost: float,
    category: str,
    map_price: float | None = None,
    family_prices: list[tuple[float, float]] | None = None,
    size_value: float | None = None,
    config: RuleConfig | None = None,
) -> ComplianceVerdict:
    """Evaluate every rule and record the outcome — passes included (FR-035).

    `family_prices` is a list of (size_value, price) for the *other* SKUs in the
    same product family, used for ladder consistency.
    """
    cfg = config or RuleConfig()
    evals: list[RuleEval] = []

    # --- Margin floor ---------------------------------------------------
    margin_pct = (proposed_price - unit_cost) / proposed_price * 100.0 if proposed_price > 0 else -100.0
    evals.append(
        RuleEval(
            "MARGIN_FLOOR",
            margin_pct >= cfg.margin_floor_pct,
            round(margin_pct, 2),
            cfg.margin_floor_pct,
            f"Margin {margin_pct:.1f}% against floor {cfg.margin_floor_pct:.1f}%"
            + ("" if margin_pct >= cfg.margin_floor_pct else " — BREACH"),
        )
    )

    # --- MAP floor ------------------------------------------------------
    if cfg.enforce_map and map_price is not None:
        ok = proposed_price >= map_price
        evals.append(
            RuleEval(
                "MAP_FLOOR", ok, round(proposed_price, 2), round(map_price, 2),
                f"Price {proposed_price:.2f} against MAP {map_price:.2f}"
                + ("" if ok else " — BREACH of minimum advertised price agreement"),
            )
        )

    # --- Maximum single-run change --------------------------------------
    if current_price > 0:
        change = abs(proposed_price - current_price) / current_price * 100.0
        ok = change <= cfg.max_change_pct + 1e-9
        evals.append(
            RuleEval(
                "MAX_CHANGE", ok, round(change, 2), cfg.max_change_pct,
                f"Change {change:.1f}% against cap {cfg.max_change_pct:.1f}%"
                + ("" if ok else " — BREACH"),
            )
        )

    # --- Price ladder consistency ---------------------------------------
    # A larger pack must not cost less than a smaller one in the same family.
    if cfg.enforce_ladder and family_prices and size_value is not None:
        breaches = []
        for other_size, other_price in family_prices:
            if other_size < size_value and other_price > proposed_price:
                breaches.append(f"{other_size:g} at {other_price:.2f}")
            if other_size > size_value and other_price < proposed_price:
                breaches.append(f"{other_size:g} at {other_price:.2f}")
        ok = not breaches
        evals.append(
            RuleEval(
                "PRICE_LADDER", ok, round(proposed_price, 2), None,
                "Ladder consistent within family" if ok
                else f"Ladder BREACH — {proposed_price:.2f} for size {size_value:g} "
                     f"conflicts with " + ", ".join(breaches[:3]),
            )
        )

    # --- Category bounds -------------------------------------------------
    lo = cfg.category_min_price.get(category)
    if lo is not None:
        ok = proposed_price >= lo
        evals.append(
            RuleEval("CATEGORY_FLOOR", ok, round(proposed_price, 2), lo,
                     f"Price {proposed_price:.2f} against category floor {lo:.2f}"
                     + ("" if ok else " — BREACH"))
        )
    hi = cfg.category_max_price.get(category)
    if hi is not None:
        ok = proposed_price <= hi
        evals.append(
            RuleEval("CATEGORY_CEILING", ok, round(proposed_price, 2), hi,
                     f"Price {proposed_price:.2f} against category ceiling {hi:.2f}"
                     + ("" if ok else " — BREACH"))
        )

    # --- Charm pricing policy -------------------------------------------
    if cfg.enforce_charm:
        cents = round((proposed_price - int(proposed_price)) * 100)
        ok = cents in (49, 99, 0)
        evals.append(
            RuleEval("CHARM_PRICING", ok, float(cents), None,
                     f"Price ends in .{cents:02d}"
                     + ("" if ok else " — policy requires .49, .99 or .00"))
        )

    # --- Never below cost (absolute) -------------------------------------
    ok = proposed_price > unit_cost
    evals.append(
        RuleEval("ABOVE_COST", ok, round(proposed_price, 2), round(unit_cost, 2),
                 f"Price {proposed_price:.2f} against unit cost {unit_cost:.2f}"
                 + ("" if ok else " — BREACH, would sell at a loss"))
    )

    return ComplianceVerdict(
        sku=sku,
        passed=all(e.passed for e in evals),
        evaluations=evals,
    )
