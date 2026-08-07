"""Price read and batch write endpoints (FR-051, FR-052, FR-053, FR-054).

The batch write is the single most safety-critical endpoint in the system. Two
properties matter more than throughput:

* **Idempotency.** A repeated `batch_key` applies nothing and replays the
  original result verbatim. A retry after a network timeout must never double-
  apply a price change.
* **Independent re-validation.** Every item is checked against this service's own
  rules (`commerce.validation`) regardless of what the sender already checked.
  Partial success is a normal outcome, not an error.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from commerce.db import session
from commerce.models import (
    PriceBatchRequest,
    PriceBatchResult,
    PriceChange,
    PriceItemResult,
    PriceRecord,
)
from commerce.validation import validate_price_change

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("", response_model=list[PriceRecord])
def list_prices(
    category: str | None = Query(None),
    limit: int = Query(10000, ge=1, le=100000),
) -> list[PriceRecord]:
    clause = "WHERE category = ?" if category else ""
    params = [category] if category else []
    params.append(limit)
    with session() as conn:
        rows = conn.execute(
            f"SELECT sku, current_price, unit_cost, map_price, list_price "
            f"FROM products {clause} ORDER BY sku LIMIT ?",
            params,
        ).fetchall()
    return [PriceRecord(**dict(r)) for r in rows]


@router.get("/history/{sku}", response_model=list[PriceChange])
def price_history(sku: str, limit: int = Query(200, ge=1, le=5000)) -> list[PriceChange]:
    with session() as conn:
        rows = conn.execute(
            "SELECT sku, old_price, new_price, changed_at, batch_key, source, reason "
            "FROM price_history WHERE sku = ? ORDER BY changed_at DESC, id DESC LIMIT ?",
            (sku, limit),
        ).fetchall()
    return [PriceChange(**dict(r)) for r in rows]


@router.post("/batch", response_model=PriceBatchResult)
def apply_price_batch(payload: PriceBatchRequest) -> PriceBatchResult:
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()

    with session() as conn:
        # --- Idempotency gate (FR-052) -------------------------------------
        prior = conn.execute(
            "SELECT result_json FROM price_batches WHERE batch_key = ?",
            (payload.batch_key,),
        ).fetchone()
        if prior is not None:
            stored = PriceBatchResult(**json.loads(prior["result_json"]))
            stored.idempotent_replay = True
            return stored

        results: list[PriceItemResult] = []
        applied = rejected = unchanged = 0

        for item in payload.items:
            rejection, row = validate_price_change(conn, item.sku, item.new_price, today)

            if rejection is not None:
                rejected += 1
                results.append(
                    PriceItemResult(
                        sku=item.sku,
                        status="rejected",
                        old_price=row["current_price"] if row is not None else None,
                        new_price=item.new_price,
                        rejection_code=rejection.code,
                        rejection_reason=rejection.reason,
                    )
                )
                continue

            old_price = float(row["current_price"])
            new_price = round(float(item.new_price), 2)

            if abs(old_price - new_price) < 0.005:
                unchanged += 1
                results.append(
                    PriceItemResult(
                        sku=item.sku,
                        status="unchanged",
                        old_price=old_price,
                        new_price=new_price,
                    )
                )
                continue

            conn.execute(
                "UPDATE products SET current_price = ? WHERE sku = ?",
                (new_price, item.sku),
            )
            conn.execute(
                "INSERT INTO price_history (sku, old_price, new_price, changed_at,"
                " batch_key, source, reason) VALUES (?,?,?,?,?,?,?)",
                (
                    item.sku, old_price, new_price, now.isoformat(),
                    payload.batch_key, payload.source, item.reason,
                ),
            )
            applied += 1
            results.append(
                PriceItemResult(
                    sku=item.sku,
                    status="applied",
                    old_price=old_price,
                    new_price=new_price,
                )
            )

        result = PriceBatchResult(
            batch_key=payload.batch_key,
            idempotent_replay=False,
            item_count=len(payload.items),
            applied_count=applied,
            rejected_count=rejected,
            unchanged_count=unchanged,
            results=results,
        )

        conn.execute(
            "INSERT INTO price_batches (batch_key, received_at, item_count,"
            " applied_count, rejected_count, result_json) VALUES (?,?,?,?,?,?)",
            (
                payload.batch_key, now.isoformat(), len(payload.items),
                applied, rejected, result.model_dump_json(),
            ),
        )

    return result


@router.get("/batch/{batch_key}", response_model=PriceBatchResult)
def get_batch(batch_key: str) -> PriceBatchResult:
    with session() as conn:
        row = conn.execute(
            "SELECT result_json FROM price_batches WHERE batch_key = ?", (batch_key,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No batch '{batch_key}'")
    return PriceBatchResult(**json.loads(row["result_json"]))
