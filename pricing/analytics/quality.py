"""Data quality gate (FR-007 .. FR-011).

Runs before any analysis. A FAIL verdict halts the run — the system must never
price on data it has already judged unfit, because a confident recommendation
built on broken inputs is more dangerous than no recommendation at all.

Severity semantics:
  PASS  — proceed
  WARN  — proceed, but the issue is attached to the run and lowers confidence
  FAIL  — halt with a specific, actionable reason
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum


class Severity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class QualityCheck:
    code: str
    severity: Severity
    message: str
    affected_records: int = 0
    detail: dict = field(default_factory=dict)


@dataclass
class QualityReport:
    checks: list[QualityCheck]
    verdict: Severity
    sku_count: int
    sales_rows: int
    generated_at: str

    @property
    def failed(self) -> bool:
        return self.verdict is Severity.FAIL

    @property
    def blocking_reasons(self) -> list[str]:
        return [c.message for c in self.checks if c.severity is Severity.FAIL]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "sku_count": self.sku_count,
            "sales_rows": self.sales_rows,
            "generated_at": self.generated_at,
            "checks": [
                {
                    "code": c.code, "severity": c.severity.value, "message": c.message,
                    "affected_records": c.affected_records, "detail": c.detail,
                }
                for c in self.checks
            ],
        }


# Thresholds
MAX_STALENESS_DAYS = 7
MIN_SKU_COVERAGE = 0.80
MIN_HISTORY_DAYS = 90
MAX_OUTLIER_RATE = 0.05
REQUIRED_PRODUCT_FIELDS = ("sku", "unit_cost", "current_price", "category")


def assess(
    products: list[dict],
    sales: list[dict],
    inventory: list[dict],
    today: date | None = None,
) -> QualityReport:
    """Profile the ingested data and return a verdict."""
    today = today or date.today()
    checks: list[QualityCheck] = []
    sku_set = {p["sku"] for p in products}

    # --- Schema conformance --------------------------------------------
    malformed = [
        p.get("sku", "<missing>")
        for p in products
        if any(p.get(f) is None for f in REQUIRED_PRODUCT_FIELDS)
    ]
    checks.append(
        QualityCheck(
            "SCHEMA_CONFORMANCE",
            Severity.FAIL if malformed else Severity.PASS,
            f"{len(malformed)} product(s) missing required fields "
            f"{list(REQUIRED_PRODUCT_FIELDS)}." if malformed
            else "All products carry the required fields.",
            len(malformed),
            {"examples": malformed[:5]},
        )
    )

    if not products:
        checks.append(
            QualityCheck("EMPTY_CATALOG", Severity.FAIL,
                         "No products returned for the requested scope.", 0)
        )
    if not sales:
        checks.append(
            QualityCheck("EMPTY_SALES", Severity.FAIL,
                         "No sales history returned; elasticity cannot be estimated.", 0)
        )

    if sales:
        dates = sorted({str(s["sale_date"])[:10] for s in sales})
        latest = datetime.strptime(dates[-1], "%Y-%m-%d").date()
        earliest = datetime.strptime(dates[0], "%Y-%m-%d").date()
        staleness = (today - latest).days
        span = (latest - earliest).days

        # --- Freshness -------------------------------------------------
        checks.append(
            QualityCheck(
                "FRESHNESS",
                Severity.PASS if staleness <= MAX_STALENESS_DAYS
                else Severity.WARN if staleness <= MAX_STALENESS_DAYS * 4
                else Severity.FAIL,
                f"Most recent sales record is {staleness} day(s) old "
                f"(threshold {MAX_STALENESS_DAYS}).",
                0,
                {"latest": dates[-1], "staleness_days": staleness},
            )
        )

        # --- History depth ---------------------------------------------
        checks.append(
            QualityCheck(
                "HISTORY_DEPTH",
                Severity.PASS if span >= MIN_HISTORY_DAYS else Severity.WARN,
                f"Sales history spans {span} day(s); {MIN_HISTORY_DAYS} recommended "
                "for reliable elasticity.",
                0,
                {"span_days": span, "earliest": dates[0]},
            )
        )

        # --- Referential integrity -------------------------------------
        orphans = {s["sku"] for s in sales if s["sku"] not in sku_set}
        checks.append(
            QualityCheck(
                "REFERENTIAL_INTEGRITY",
                Severity.WARN if orphans else Severity.PASS,
                f"{len(orphans)} SKU(s) appear in sales but not in the catalog."
                if orphans else "All sales rows reference a known SKU.",
                len(orphans),
                {"examples": sorted(orphans)[:5]},
            )
        )

        # --- Coverage ---------------------------------------------------
        skus_with_sales = {s["sku"] for s in sales} & sku_set
        coverage = len(skus_with_sales) / len(sku_set) if sku_set else 0.0
        checks.append(
            QualityCheck(
                "SKU_COVERAGE",
                Severity.PASS if coverage >= MIN_SKU_COVERAGE
                else Severity.WARN if coverage >= 0.5 else Severity.FAIL,
                f"{coverage:.0%} of catalog SKUs have sales history "
                f"(threshold {MIN_SKU_COVERAGE:.0%}).",
                len(sku_set) - len(skus_with_sales),
                {"coverage": round(coverage, 4)},
            )
        )

        # --- Outliers ---------------------------------------------------
        negatives = sum(
            1 for s in sales if s.get("units", 0) < 0 or s.get("unit_price", 0) <= 0
        )
        checks.append(
            QualityCheck(
                "IMPOSSIBLE_VALUES",
                Severity.FAIL if negatives else Severity.PASS,
                f"{negatives} sales row(s) have negative units or a non-positive price."
                if negatives else "No impossible values in sales.",
                negatives,
            )
        )

        by_sku: dict[str, list[float]] = defaultdict(list)
        for s in sales:
            by_sku[s["sku"]].append(float(s.get("units", 0)))
        outlier_skus = 0
        for values in by_sku.values():
            if len(values) < 10:
                continue
            avg = sum(values) / len(values)
            if avg <= 0:
                continue
            if max(values) > avg * 25:
                outlier_skus += 1
        rate = outlier_skus / len(by_sku) if by_sku else 0.0
        checks.append(
            QualityCheck(
                "DEMAND_OUTLIERS",
                Severity.PASS if rate <= MAX_OUTLIER_RATE else Severity.WARN,
                f"{outlier_skus} SKU(s) ({rate:.1%}) show a daily spike over 25x their "
                "own mean.",
                outlier_skus,
                {"rate": round(rate, 4)},
            )
        )

        # --- Price variation (elasticity identifiability) ---------------
        prices_by_sku: dict[str, list[float]] = defaultdict(list)
        for s in sales:
            prices_by_sku[s["sku"]].append(float(s.get("unit_price", 0)))
        flat = 0
        for values in prices_by_sku.values():
            if len(values) < 10:
                continue
            avg = sum(values) / len(values)
            if avg <= 0:
                continue
            var = sum((v - avg) ** 2 for v in values) / len(values)
            if (var ** 0.5) / avg < 0.015:
                flat += 1
        flat_rate = flat / len(prices_by_sku) if prices_by_sku else 0.0
        checks.append(
            QualityCheck(
                "PRICE_VARIATION",
                Severity.PASS if flat_rate < 0.30
                else Severity.WARN if flat_rate < 0.70 else Severity.FAIL,
                f"{flat} SKU(s) ({flat_rate:.0%}) have effectively flat price history; "
                "elasticity is not identifiable for these.",
                flat,
                {"flat_rate": round(flat_rate, 4)},
            )
        )

    # --- Inventory completeness ----------------------------------------
    inv_skus = {i["sku"] for i in inventory}
    missing_inv = sku_set - inv_skus
    checks.append(
        QualityCheck(
            "INVENTORY_COMPLETENESS",
            Severity.PASS if not missing_inv else Severity.WARN,
            f"{len(missing_inv)} SKU(s) have no inventory record; stock pressure "
            "signals will be unavailable for them."
            if missing_inv else "Inventory present for every SKU.",
            len(missing_inv),
            {"examples": sorted(missing_inv)[:5]},
        )
    )

    if any(c.severity is Severity.FAIL for c in checks):
        verdict = Severity.FAIL
    elif any(c.severity is Severity.WARN for c in checks):
        verdict = Severity.WARN
    else:
        verdict = Severity.PASS

    return QualityReport(
        checks=checks,
        verdict=verdict,
        sku_count=len(products),
        sales_rows=len(sales),
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )
