"""HTTP client for the Commerce Service (PRD 4.1, FR-003).

This module is the *entire* surface through which the pricing platform reaches
catalog, sales, inventory, and price data. There is no shared database and no
import from the `commerce` package — if either ever appears, the service
boundary the architecture depends on has been quietly breached.

Deliberately omitted: any wrapper for `/eval/ground-truth`. That endpoint holds
the elasticity answer key, and the pricing pipeline must never read it (see
commerce/routes/evaluation.py). The scoring harness calls it directly.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.core.tls import verify_option

logger = get_logger("pricing.clients.commerce")

RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


class CommerceUnavailable(RuntimeError):
    """Raised when the Commerce Service cannot be reached or is unhealthy.

    Surfaced to the caller rather than swallowed: a pricing run on stale or
    partial data is worse than no run at all (NFR-026).
    """


class CommerceClient:
    def __init__(self, base_url: str | None = None, timeout: float = 60.0) -> None:
        s = get_settings()
        self.base_url = (base_url or s.commerce_base_url).rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            verify=verify_option(s),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CommerceClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        resp = self._client.get(path, params=clean)
        resp.raise_for_status()
        return resp.json()

    # --- Health -----------------------------------------------------------
    def health(self) -> dict:
        try:
            return self._get("/health")
        except Exception as exc:
            raise CommerceUnavailable(
                f"Commerce Service unreachable at {self.base_url}: {exc}"
            ) from exc

    def require_healthy(self) -> dict:
        """Gate for FR-076 — no run starts until commerce is up and seeded."""
        health = self.health()
        if health.get("status") != "ok":
            raise CommerceUnavailable(
                f"Commerce Service at {self.base_url} reports status "
                f"'{health.get('status')}' (products={health.get('product_count')}). "
                "Run: python -m commerce.seed"
            )
        return health

    # --- Reads ------------------------------------------------------------
    def products(
        self, category: str | None = None, family_id: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        return self._get(
            "/catalog/products", category=category, family_id=family_id, limit=limit
        )

    def product(self, sku: str) -> dict:
        return self._get(f"/catalog/products/{sku}")

    def categories(self) -> list[str]:
        return self._get("/catalog/categories")

    def sales(
        self, sku: str | None = None, category: str | None = None,
        since: str | None = None, limit: int = 400000,
    ) -> list[dict]:
        return self._get("/sales", sku=sku, category=category, since=since, limit=limit)

    def inventory(self, category: str | None = None) -> list[dict]:
        return self._get("/inventory", category=category)

    def inventory_for_sku(self, sku: str) -> dict:
        return self._get(f"/inventory/{sku}")

    def prices(self, category: str | None = None) -> list[dict]:
        return self._get("/prices", category=category)

    def price_history(self, sku: str, limit: int = 200) -> list[dict]:
        return self._get(f"/prices/history/{sku}", limit=limit)

    def market_clock(self) -> dict:
        """Where the transaction record ends — the readback horizon."""
        return self._get("/market/clock")

    def advance_market(self, days: int = 7) -> dict:
        """Ask the retailer's systems to transact forward at current prices.

        The platform never simulates the market itself: it asks the system of
        record to move, then reads back ordinary sales rows. Ground-truth
        elasticity stays on the far side of the boundary.
        """
        return self._post("/market/advance", {"days": int(days)})

    # --- Write ------------------------------------------------------------
    def push_prices(
        self, batch_key: str, items: list[dict], source: str = "pricing-platform"
    ) -> dict:
        """Idempotent batch push (FR-052).

        Not retried on HTTP errors. The batch_key makes a retry *safe*, but a
        4xx means the request itself was malformed and resending it unchanged
        will fail identically. Transport-level failures are retried by _post.
        """
        payload = {"batch_key": batch_key, "source": source, "items": items}
        resp = self._post("/prices/batch", payload)
        logger.info(
            "commerce.push",
            batch_key=batch_key,
            applied=resp.get("applied_count"),
            rejected=resp.get("rejected_count"),
            replay=resp.get("idempotent_replay"),
        )
        return resp

    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def _post(self, path: str, payload: dict) -> Any:
        resp = self._client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()
