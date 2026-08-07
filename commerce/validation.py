"""Commerce-side price validation — deliberately independent of the platform.

FR-053 requires this service to re-validate incoming prices on its own terms and
reject what it does not like. That is not redundancy for its own sake: it is what
proves the service boundary is enforced on both sides rather than merely drawn on
a diagram. If the pricing platform is ever wrong, buggy, or compromised, these
rules are the last thing standing between it and the live price book.

The rules here intentionally do NOT mirror the platform's compliance rules. The
platform enforces pricing *policy* (margin floors, change caps, ladder
consistency). This service enforces *system-of-record integrity* — never sell
below cost, never accept an implausible jump, never let one SKU churn. Overlap is
partial and that is the design.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# A single-step move larger than this is treated as a runaway algorithm rather
# than a pricing decision, regardless of what the sender claims.
MAX_SINGLE_STEP_PCT = 50.0

# Guards against a loop repricing the same SKU repeatedly within one day.
MAX_CHANGES_PER_SKU_PER_DAY = 6


@dataclass(frozen=True)
class Rejection:
    code: str
    reason: str


def validate_price_change(
    conn: sqlite3.Connection,
    sku: str,
    new_price: float,
    today: str,
) -> tuple[Rejection | None, sqlite3.Row | None]:
    """Return (rejection, product_row). A None rejection means accept.

    Checks run cheapest-first and short-circuit on the first failure so the
    caller gets one clear reason rather than a list.
    """
    row = conn.execute(
        "SELECT sku, unit_cost, map_price, current_price FROM products WHERE sku = ?",
        (sku,),
    ).fetchone()

    if row is None:
        return Rejection("UNKNOWN_SKU", f"SKU '{sku}' is not in the catalog."), None

    if new_price <= 0:
        return Rejection("NON_POSITIVE_PRICE", "Price must be greater than zero."), row

    if new_price < row["unit_cost"]:
        return (
            Rejection(
                "BELOW_COST",
                f"Price {new_price:.2f} is below unit cost {row['unit_cost']:.2f}. "
                "This service never sells below cost.",
            ),
            row,
        )

    map_price = row["map_price"]
    if map_price is not None and new_price < map_price:
        return (
            Rejection(
                "BELOW_MAP",
                f"Price {new_price:.2f} breaches the minimum advertised price "
                f"{map_price:.2f}.",
            ),
            row,
        )

    current = row["current_price"]
    if current > 0:
        delta_pct = abs(new_price - current) / current * 100.0
        if delta_pct > MAX_SINGLE_STEP_PCT:
            return (
                Rejection(
                    "IMPLAUSIBLE_JUMP",
                    f"Requested change of {delta_pct:.1f}% exceeds the "
                    f"{MAX_SINGLE_STEP_PCT:.0f}% single-step limit.",
                ),
                row,
            )

    changes_today = conn.execute(
        "SELECT COUNT(*) AS n FROM price_history "
        "WHERE sku = ? AND substr(changed_at, 1, 10) = ?",
        (sku, today),
    ).fetchone()["n"]
    if changes_today >= MAX_CHANGES_PER_SKU_PER_DAY:
        return (
            Rejection(
                "CHURN_LIMIT",
                f"SKU already changed {changes_today} times today; limit is "
                f"{MAX_CHANGES_PER_SKU_PER_DAY}.",
            ),
            row,
        )

    return None, row
