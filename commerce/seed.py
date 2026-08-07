"""Synthetic retail dataset generator (FR-001, FR-002).

The important property here is that sales history is generated *from* a known
elasticity per SKU:

    units = base_demand * (price / ref_price) ** elasticity * seasonal * promo * noise

Taking logs makes that a linear relationship, so a regression on the generated
history should recover the elasticity we started with. That is what turns
"pricing accuracy" from a claim into a measurement (PRD 9.1) — we know the true
optimum, so we can score how close a recommendation lands.

Two things this generator must get right or the whole evaluation is worthless:

1. **Prices must actually vary over history.** With a flat price series,
   elasticity is unidentifiable — the regression has no signal to fit and would
   return noise. Promotions and periodic repricing supply that variation.
2. **Noise must be multiplicative and modest.** Too little and the problem is
   trivial; too much and even a correct estimator looks wrong.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta, timezone

from commerce.db import init_db, session

DEFAULT_SKU_COUNT = 500
DEFAULT_HISTORY_DAYS = 365
DEFAULT_SEED = 20260807

# (category, subcategories, base elasticity, price band)
# Staples are inelastic; discretionary and heavily-substitutable goods are not.
CATEGORIES: list[tuple[str, list[str], float, tuple[float, float]]] = [
    ("Beverages", ["Carbonated", "Juice", "Water"], -2.2, (0.80, 4.50)),
    ("Snacks", ["Crisps", "Confectionery", "Biscuits"], -1.8, (0.90, 5.00)),
    ("Coffee & Tea", ["Ground Coffee", "Pods", "Tea"], -1.5, (2.50, 14.00)),
    ("Household", ["Cleaning", "Paper", "Laundry"], -1.1, (1.50, 12.00)),
    ("Personal Care", ["Haircare", "Skincare", "Oral"], -0.9, (1.80, 11.00)),
]

BRANDS = ["Aurora", "Northfield", "Kestrel", "Vellum", "Harbour", "Ridgeway", "Marlow"]

# Size ladders must be **unit-consistent**. Mixing units inside one ladder (e.g.
# 330ml, 500ml, 1L) breaks the pack-size scaling below, because 1.0 is
# numerically smaller than 330 and the scale exponent collapses toward zero —
# which produces nonsense like a 1L juice priced under a penny.
LADDERS_BY_FORM: dict[str, list[list[tuple[float, str]]]] = {
    "liquid": [
        [(330.0, "ml"), (500.0, "ml"), (1000.0, "ml"), (2000.0, "ml")],
        [(200.0, "ml"), (400.0, "ml"), (1000.0, "ml")],
    ],
    "solid": [
        [(150.0, "g"), (300.0, "g"), (750.0, "g")],
        [(100.0, "g"), (250.0, "g"), (500.0, "g")],
    ],
    "pack": [
        [(6.0, "pack"), (12.0, "pack"), (24.0, "pack")],
        [(4.0, "pack"), (9.0, "pack"), (16.0, "pack")],
    ],
}

# Physical form per subcategory, so a juice never ships in grams.
FORM_BY_SUBCATEGORY: dict[str, str] = {
    "Carbonated": "liquid", "Juice": "liquid", "Water": "liquid",
    "Crisps": "solid", "Confectionery": "solid", "Biscuits": "solid",
    "Ground Coffee": "solid", "Pods": "pack", "Tea": "pack",
    "Cleaning": "liquid", "Paper": "pack", "Laundry": "liquid",
    "Haircare": "liquid", "Skincare": "liquid", "Oral": "pack",
}

# Nothing in a retail catalog is priced below this.
MIN_LIST_PRICE = 0.49


def _format_size(size_value: float, size_unit: str) -> str:
    """Human-readable pack size. Ladders are stored unit-consistent (ml/g), so
    convert the large end up for display only — 1000ml reads as 1L."""
    if size_unit == "ml" and size_value >= 1000:
        return f"{size_value / 1000:g}L"
    if size_unit == "g" and size_value >= 1000:
        return f"{size_value / 1000:g}kg"
    if size_unit == "pack":
        return f"{size_value:g}pk"
    return f"{size_value:g}{size_unit}"


def _seasonal_factor(day: date, category: str) -> float:
    """Annual sinusoid plus a December lift, phased differently per category."""
    doy = day.timetuple().tm_yday
    phase = {"Beverages": 0.0, "Snacks": 1.2, "Coffee & Tea": 3.1}.get(category, 2.0)
    annual = 1.0 + 0.18 * math.sin(2 * math.pi * (doy / 365.25) + phase)
    december = 1.22 if day.month == 12 else 1.0
    return annual * december


def _weekday_factor(day: date) -> float:
    # Retail footfall peaks Friday/Saturday.
    return [0.92, 0.90, 0.95, 1.00, 1.18, 1.25, 1.05][day.weekday()]


def _round_retail(price: float) -> float:
    """Charm pricing — retailers land on .49 / .99 far more often than random.

    Snaps to the *nearest* charm point rather than always rounding down, so the
    adjustment stays under 50p and does not distort the price ladder.
    """
    if price < 1.0:
        return max(MIN_LIST_PRICE, 0.99 if price >= 0.75 else 0.49)
    whole = math.floor(price)
    frac = price - whole
    if frac < 0.25:
        candidate = whole - 1 + 0.99
    elif frac < 0.75:
        candidate = whole + 0.49
    else:
        candidate = whole + 0.99
    return round(max(MIN_LIST_PRICE, candidate), 2)


def _build_catalog(rng: random.Random, sku_count: int) -> list[dict]:
    products: list[dict] = []
    family_seq = 0
    while len(products) < sku_count:
        category, subcats, base_elas, (lo, hi) = rng.choice(CATEGORIES)
        subcategory = rng.choice(subcats)
        brand = rng.choice(BRANDS)
        form = FORM_BY_SUBCATEGORY[subcategory]
        ladder = rng.choice(LADDERS_BY_FORM[form])
        family_seq += 1
        family_id = f"FAM-{family_seq:04d}"
        anchor = rng.uniform(lo, hi)
        base_size = ladder[0][0]
        # Cost ratio is a property of the supplier relationship, so it is drawn
        # once per family. Drawing it per SKU produced families where the 500ml
        # cost less than the 330ml, which then made margin-floor rules behave
        # unintelligibly.
        family_cost_ratio = rng.uniform(0.45, 0.68)

        for idx, (size_value, size_unit) in enumerate(ladder):
            if len(products) >= sku_count:
                break
            # Larger packs cost more in absolute terms but less per unit — this
            # sub-linear exponent is what makes the price-ladder rule meaningful
            # downstream (a 2L must cost more than a 1L, but less than twice).
            scale = (size_value / base_size) ** 0.82 if base_size else 1.0
            list_price = _round_retail(anchor * scale)
            # Small per-SKU jitter around the family ratio keeps variety without
            # inverting the cost ladder.
            cost_ratio = min(0.72, max(0.40, family_cost_ratio * rng.uniform(0.96, 1.04)))
            unit_cost = round(max(0.12, list_price * cost_ratio), 2)
            has_map = rng.random() < 0.35
            map_price = round(list_price * rng.uniform(0.72, 0.85), 2) if has_map else None

            # Per-SKU elasticity dispersed around the category norm. Larger packs
            # skew slightly more elastic (shoppers compare unit prices on them).
            elasticity = base_elas * rng.uniform(0.75, 1.25) - 0.08 * idx
            elasticity = max(-3.4, min(-0.45, elasticity))

            sku = f"{category[:3].upper()}-{family_seq:04d}-{idx + 1}"
            products.append(
                {
                    "sku": sku,
                    "name": f"{brand} {subcategory} {_format_size(size_value, size_unit)}",
                    "category": category,
                    "subcategory": subcategory,
                    "brand": brand,
                    "family_id": family_id,
                    "size_value": size_value,
                    "size_unit": size_unit,
                    "unit_cost": unit_cost,
                    "map_price": map_price,
                    "list_price": list_price,
                    "current_price": list_price,
                    "launched_on": "2023-01-15",
                    "_elasticity": elasticity,
                    "_base_demand": rng.uniform(18, 220) / max(1.0, scale ** 0.5),
                }
            )
    return products[:sku_count]


def _generate_sales(
    rng: random.Random, products: list[dict], history_days: int
) -> tuple[list[tuple], list[tuple], dict[str, float]]:
    """Return (sales_rows, price_history_rows, final_prices).

    Price movement over history comes from two sources: multi-day promotions and
    occasional base-price resets. Without both, elasticity cannot be estimated.
    """
    today = date.today()
    start = today - timedelta(days=history_days)
    sales_rows: list[tuple] = []
    history_rows: list[tuple] = []
    final_prices: dict[str, float] = {}

    for p in products:
        sku = p["sku"]
        ref_price = p["list_price"]
        base_demand = p["_base_demand"]
        elasticity = p["_elasticity"]

        current = ref_price
        promo_until: date | None = None
        promo_price = ref_price

        for offset in range(history_days):
            day = start + timedelta(days=offset)

            if promo_until is not None and day > promo_until:
                promo_until = None
                current = ref_price

            # Promotion cadence drives the price variation that makes elasticity
            # identifiable at all. Too rare and the regression has no signal to
            # fit; validated at ~0.028/day giving a within-SKU price CV near 0.11.
            if promo_until is None and rng.random() < 0.028:
                depth = rng.uniform(0.10, 0.35)
                promo_price = _round_retail(ref_price * (1 - depth))
                floor = p["unit_cost"] * 1.05
                promo_price = max(promo_price, round(floor, 2))
                promo_until = day + timedelta(days=rng.randint(3, 12))
                current = promo_price
                history_rows.append(
                    (sku, ref_price, current, day.isoformat(), None, "seed", "promotion")
                )

            # Occasional base-price reset (cost pass-through, competitive move).
            if promo_until is None and rng.random() < 0.006:
                new_ref = _round_retail(ref_price * rng.uniform(0.94, 1.09))
                if new_ref != ref_price and new_ref > p["unit_cost"] * 1.1:
                    history_rows.append(
                        (sku, ref_price, new_ref, day.isoformat(), None, "seed", "reprice")
                    )
                    ref_price = new_ref
                    current = new_ref

            on_promo = promo_until is not None
            demand = (
                base_demand
                * (current / p["list_price"]) ** elasticity
                * _seasonal_factor(day, p["category"])
                * _weekday_factor(day)
                * math.exp(rng.gauss(0.0, 0.16))     # multiplicative noise
                * (1.14 if on_promo else 1.0)        # display/visibility lift
            )
            units = max(0, int(round(demand)))
            sales_rows.append(
                (sku, day.isoformat(), units, round(current, 2),
                 round(units * current, 2), 1 if on_promo else 0)
            )

        final_prices[sku] = round(current if promo_until is None else ref_price, 2)

    return sales_rows, history_rows, final_prices


def seed(
    sku_count: int = DEFAULT_SKU_COUNT,
    history_days: int = DEFAULT_HISTORY_DAYS,
    seed_value: int = DEFAULT_SEED,
) -> dict[str, int]:
    """Generate and persist the full synthetic dataset. Deterministic per seed."""
    rng = random.Random(seed_value)
    init_db()

    products = _build_catalog(rng, sku_count)
    sales_rows, history_rows, final_prices = _generate_sales(rng, products, history_days)
    now = datetime.now(timezone.utc).isoformat()

    with session() as conn:
        conn.execute("DELETE FROM sales")
        conn.execute("DELETE FROM price_history")
        conn.execute("DELETE FROM price_batches")
        conn.execute("DELETE FROM inventory")
        conn.execute("DELETE FROM ground_truth")
        conn.execute("DELETE FROM products")

        conn.executemany(
            "INSERT INTO products (sku, name, category, subcategory, brand, family_id,"
            " size_value, size_unit, unit_cost, map_price, list_price, current_price,"
            " launched_on) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    p["sku"], p["name"], p["category"], p["subcategory"], p["brand"],
                    p["family_id"], p["size_value"], p["size_unit"], p["unit_cost"],
                    p["map_price"], p["list_price"], final_prices[p["sku"]],
                    p["launched_on"],
                )
                for p in products
            ],
        )

        conn.executemany(
            "INSERT INTO sales (sku, sale_date, units, unit_price, revenue, on_promo)"
            " VALUES (?,?,?,?,?,?)",
            sales_rows,
        )

        conn.executemany(
            "INSERT INTO price_history (sku, old_price, new_price, changed_at,"
            " batch_key, source, reason) VALUES (?,?,?,?,?,?,?)",
            history_rows,
        )

        # Inventory: mostly healthy cover, with deliberate overstock and
        # stockout-risk cases so the pressure signals have something to find.
        inv_rows = []
        for p in products:
            weekly = max(1.0, p["_base_demand"] * 7 / 4)
            roll = rng.random()
            if roll < 0.12:
                cover_weeks = rng.uniform(9, 20)      # overstock
            elif roll < 0.22:
                cover_weeks = rng.uniform(0.2, 0.9)   # stockout risk
            else:
                cover_weeks = rng.uniform(2.5, 6.5)
            inv_rows.append(
                (p["sku"], int(weekly * cover_weeks), int(weekly * rng.uniform(0, 2)),
                 round(weekly, 2), now)
            )
        conn.executemany(
            "INSERT INTO inventory (sku, on_hand, on_order, weekly_velocity, updated_at)"
            " VALUES (?,?,?,?,?)",
            inv_rows,
        )

        conn.executemany(
            "INSERT INTO ground_truth (sku, true_elasticity, base_demand, ref_price)"
            " VALUES (?,?,?,?)",
            [
                (p["sku"], round(p["_elasticity"], 4), round(p["_base_demand"], 3),
                 p["list_price"])
                for p in products
            ],
        )

    return {
        "products": len(products),
        "sales_rows": len(sales_rows),
        "price_changes": len(history_rows),
        "history_days": history_days,
    }


if __name__ == "__main__":
    stats = seed()
    print(f"Seeded commerce.db: {stats}")
