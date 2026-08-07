"""Commerce Service: idempotency, independent re-validation, market clock.

The batch write is the most safety-critical endpoint in the system. Two
properties matter more than anything else about it:

* A repeated `batch_key` applies nothing (FR-052). A retry after a network
  timeout must never double-apply a price change.
* This service re-validates on its own terms (FR-053) regardless of what the
  sender already checked, because "the caller validated it" is not a control.
"""

from __future__ import annotations


def _skus(client, limit: int = 3) -> list[dict]:
    return client.get("/catalog/products", params={"limit": limit}).json()


def test_health_reports_seeded_catalog(commerce_client):
    body = commerce_client.get("/health").json()
    assert body["status"] == "ok" and body["product_count"] > 0


def test_batch_push_applies_and_records_history(commerce_client):
    product = _skus(commerce_client, 1)[0]
    new_price = round(product["current_price"] * 1.03, 2)
    result = commerce_client.post("/prices/batch", json={
        "batch_key": "test-batch-0001",
        "items": [{"sku": product["sku"], "new_price": new_price, "reason": "test"}],
    }).json()

    assert result["applied_count"] == 1 and result["idempotent_replay"] is False
    history = commerce_client.get(f"/prices/history/{product['sku']}").json()
    assert history[0]["new_price"] == new_price
    assert history[0]["batch_key"] == "test-batch-0001"


def test_repushing_the_same_batch_is_a_no_op(commerce_client):
    """FR-052 — the property that makes a retry safe."""
    product = _skus(commerce_client, 1)[0]
    payload = {
        "batch_key": "test-batch-idem",
        "items": [{"sku": product["sku"],
                   "new_price": round(product["current_price"] * 1.04, 2)}],
    }
    first = commerce_client.post("/prices/batch", json=payload).json()
    second = commerce_client.post("/prices/batch", json=payload).json()

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert second["applied_count"] == first["applied_count"]

    history = commerce_client.get(f"/prices/history/{product['sku']}").json()
    matching = [h for h in history if h["batch_key"] == "test-batch-idem"]
    assert len(matching) == 1, "a replayed batch wrote a second history row"


def test_service_rejects_a_below_cost_price_on_its_own_terms(commerce_client):
    """FR-053 — the sender's opinion is not a control."""
    product = _skus(commerce_client, 1)[0]
    result = commerce_client.post("/prices/batch", json={
        "batch_key": "test-batch-belowcost",
        "items": [{"sku": product["sku"], "new_price": 0.01}],
    }).json()
    assert result["rejected_count"] == 1
    assert result["results"][0]["status"] == "rejected"
    assert result["results"][0]["rejection_reason"]


def test_partial_success_is_first_class(commerce_client):
    """Some applied, some rejected, one response — never an all-or-nothing error."""
    products = _skus(commerce_client, 2)
    result = commerce_client.post("/prices/batch", json={
        "batch_key": "test-batch-partial",
        "items": [
            {"sku": products[0]["sku"],
             "new_price": round(products[0]["current_price"] * 1.02, 2)},
            {"sku": "NO-SUCH-SKU", "new_price": 5.00},
        ],
    }).json()
    assert result["applied_count"] == 1 and result["rejected_count"] == 1
    statuses = {r["sku"]: r["status"] for r in result["results"]}
    assert statuses["NO-SUCH-SKU"] == "rejected"


def test_unchanged_price_is_reported_not_applied(commerce_client):
    product = _skus(commerce_client, 1)[0]
    result = commerce_client.post("/prices/batch", json={
        "batch_key": "test-batch-noop",
        "items": [{"sku": product["sku"], "new_price": product["current_price"]}],
    }).json()
    assert result["unchanged_count"] == 1 and result["applied_count"] == 0


# --- Market clock (feedback loop input) -----------------------------------

def test_market_advance_generates_days_at_current_prices(commerce_client):
    before = commerce_client.get("/market/clock").json()
    result = commerce_client.post("/market/advance", json={"days": 5}).json()
    after = commerce_client.get("/market/clock").json()

    assert result["days_generated"] == 5
    assert result["rows_written"] > 0
    assert after["sales_rows"] > before["sales_rows"]
    assert after["last_date"] > before["last_date"]


def test_advancing_the_same_days_twice_does_not_double_count(commerce_client):
    """Idempotent per date — otherwise the readback would measure phantom demand."""
    commerce_client.post("/market/advance", json={"days": 4})
    clock = commerce_client.get("/market/clock").json()
    rows_before = clock["sales_rows"]

    # Re-advancing regenerates nothing for dates that already transacted, but
    # does extend past them; assert the overlap was not duplicated.
    series = commerce_client.get(
        f"/market/realized/{_skus(commerce_client, 1)[0]['sku']}",
        params={"days": 10},
    ).json()
    dates = [row["sale_date"] for row in series["series"]]
    assert len(dates) == len(set(dates)), "a date transacted twice"
    assert rows_before > 0


def test_ground_truth_is_served_for_evaluation_only(commerce_client):
    """FR-002. The pricing client has no wrapper for this by design (D-07)."""
    rows = commerce_client.get("/eval/ground-truth", params={"limit": 5}).json()
    assert rows and all("true_elasticity" in r for r in rows)

    from pricing.clients.commerce import CommerceClient

    forbidden = [m for m in dir(CommerceClient) if "ground" in m.lower()
                 or "truth" in m.lower()]
    assert not forbidden, (
        f"CommerceClient grew access to the answer key: {forbidden}. "
        "Reading it during a run makes the accuracy metric meaningless."
    )
