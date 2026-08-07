"""Market clock — advance time so the closed feedback loop has something to read.

Sales history is generated up to today. The moment the pricing platform pushes a
price, there is by construction no *future* in the dataset to observe, so
"realized outcome" would be permanently empty and FR-055/FR-107..111 could only
ever be demonstrated as a schema rather than as behaviour.

This endpoint advances the market by N days, generating transactions at
**whatever price the price book currently holds** using the same generative model
that produced the seed history — the ground-truth elasticity, seasonality,
weekday shape, and multiplicative noise. The retailer's market responding to a
price change is the Commerce Service's business, not the platform's.

Two properties that keep the evaluation honest:

* The platform still never reads ground truth. It observes ordinary sales rows
  through `/sales` and must infer the response, exactly as it would in reality.
* Advancing is idempotent per date: a day that already has transactions is not
  regenerated, so calling this twice does not double-count demand.

This is a demonstration affordance, recorded in docs/DEVIATIONS.md. It is
namespaced under /market so its presence in a trace is obvious.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from commerce.db import session
from commerce.seed import _seasonal_factor, _weekday_factor

router = APIRouter(prefix="/market", tags=["market"])

DEMAND_NOISE_SD = 0.16
MAX_ADVANCE_DAYS = 90


class AdvanceRequest(BaseModel):
    days: int = Field(7, ge=1, le=MAX_ADVANCE_DAYS)
    seed: int = Field(20260807, description="Fixed seed keeps the demo reproducible.")


class AdvanceResult(BaseModel):
    days_generated: int
    skus: int
    rows_written: int
    first_date: str | None
    last_date: str | None
    note: str


@router.get("/clock")
def market_clock() -> dict:
    """Where the transaction record currently ends."""
    with session() as conn:
        row = conn.execute(
            "SELECT MIN(sale_date) AS first_date, MAX(sale_date) AS last_date,"
            " COUNT(*) AS rows FROM sales"
        ).fetchone()
    last = row["last_date"]
    behind = None
    if last:
        behind = (date.today() - date.fromisoformat(last[:10])).days
    return {
        "first_date": row["first_date"],
        "last_date": last,
        "sales_rows": row["rows"],
        "days_behind_today": behind,
    }


@router.post("/advance", response_model=AdvanceResult)
def advance_market(payload: AdvanceRequest) -> AdvanceResult:
    """Generate transactions for the next N days at current price-book prices."""
    rng = random.Random(payload.seed)

    with session() as conn:
        products = [
            dict(r) for r in conn.execute(
                "SELECT p.sku, p.category, p.list_price, p.current_price,"
                " g.true_elasticity, g.base_demand, g.ref_price"
                " FROM products p JOIN ground_truth g ON g.sku = p.sku"
            )
        ]
        if not products:
            return AdvanceResult(
                days_generated=0, skus=0, rows_written=0, first_date=None,
                last_date=None,
                note="No seeded catalog. Run: python -m commerce.seed",
            )

        last_row = conn.execute("SELECT MAX(sale_date) AS d FROM sales").fetchone()
        cursor = (
            date.fromisoformat(last_row["d"][:10])
            if last_row and last_row["d"]
            else date.today() - timedelta(days=1)
        )

        rows: list[tuple] = []
        days: list[date] = []
        for offset in range(1, payload.days + 1):
            day = cursor + timedelta(days=offset)
            days.append(day)
            for p in products:
                price = float(p["current_price"])
                ref = float(p["ref_price"]) or float(p["list_price"])
                if price <= 0 or ref <= 0:
                    continue
                demand = (
                    float(p["base_demand"])
                    * (price / ref) ** float(p["true_elasticity"])
                    * _seasonal_factor(day, p["category"])
                    * _weekday_factor(day)
                    * math.exp(rng.gauss(0.0, DEMAND_NOISE_SD))
                )
                units = max(0, int(round(demand)))
                rows.append(
                    (p["sku"], day.isoformat(), units, round(price, 2),
                     round(units * price, 2), 0)
                )

        # Idempotent per date: never regenerate a day that already transacted.
        existing = {
            r["sale_date"] for r in conn.execute(
                "SELECT DISTINCT sale_date FROM sales WHERE sale_date >= ?",
                (days[0].isoformat(),),
            )
        }
        fresh = [r for r in rows if r[1] not in existing]
        if fresh:
            conn.executemany(
                "INSERT INTO sales (sku, sale_date, units, unit_price, revenue,"
                " on_promo) VALUES (?,?,?,?,?,?)",
                fresh,
            )
            _refresh_inventory(conn, days[0].isoformat())

    generated = sorted({r[1] for r in fresh})
    return AdvanceResult(
        days_generated=len(generated),
        skus=len(products),
        rows_written=len(fresh),
        first_date=generated[0] if generated else None,
        last_date=generated[-1] if generated else None,
        note=(
            f"Market advanced {len(generated)} day(s) at current price-book prices."
            if generated
            else "Those dates already had transactions; nothing regenerated."
        ),
    )


def _refresh_inventory(conn, since: str) -> None:
    """Draw stock down by what sold and re-derive weekly velocity.

    Without this, cover days would drift out of step with the sales record and
    the inventory-pressure signal would quietly become fiction.
    """
    sold = conn.execute(
        "SELECT sku, SUM(units) AS units, COUNT(DISTINCT sale_date) AS days"
        " FROM sales WHERE sale_date >= ? GROUP BY sku",
        (since,),
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for row in sold:
        days = max(int(row["days"]), 1)
        velocity = round(float(row["units"]) / days * 7.0, 2)
        conn.execute(
            "UPDATE inventory SET on_hand = MAX(0, on_hand - ?),"
            " weekly_velocity = ?, updated_at = ? WHERE sku = ?",
            (int(row["units"]), velocity, now, row["sku"]),
        )


@router.get("/realized/{sku}")
def realized_series(
    sku: str, days: int = Query(28, ge=1, le=365)
) -> dict:
    """Recent daily units and revenue for one SKU — the readback surface."""
    with session() as conn:
        rows = [
            dict(r) for r in conn.execute(
                "SELECT sale_date, units, unit_price, revenue FROM sales"
                " WHERE sku = ? ORDER BY sale_date DESC LIMIT ?",
                (sku, days),
            )
        ]
    return {"sku": sku, "days": len(rows), "series": list(reversed(rows))}
