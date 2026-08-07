"""Commerce Service application entry point (port 8001).

Run:  python -m uvicorn commerce.main:app --port 8001
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from commerce import __version__
from commerce.db import init_db, is_seeded, session
from commerce.models import HealthResponse
from commerce.routes import catalog, evaluation, inventory, market, prices, sales

logger = logging.getLogger("commerce")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seeded = is_seeded()
    logger.info("Commerce Service starting (seeded=%s)", seeded)
    if not seeded:
        logger.warning(
            "commerce.db has no products. Run: python -m commerce.seed"
        )
    yield
    # SQLite connections are per-request and closed by the session() context
    # manager, so there is nothing to drain here. The hook stays as the explicit
    # shutdown seam required by NFR-023.
    logger.info("Commerce Service shutting down cleanly")


app = FastAPI(
    title="Commerce Service",
    description=(
        "Enterprise system of record — catalog, sales, inventory, live price book. "
        "Knows nothing about AI. The pricing platform reaches this data over HTTP only."
    ),
    version=__version__,
    lifespan=lifespan,
)

# The UI never calls this service directly; the pricing platform does. CORS is
# permitted only for local development origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{os.environ.get('UI_PORT', '5173')}",
        f"http://127.0.0.1:{os.environ.get('UI_PORT', '5173')}",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(catalog.router)
app.include_router(sales.router)
app.include_router(inventory.router)
app.include_router(prices.router)
app.include_router(market.router)
app.include_router(evaluation.router)


@app.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness probe. The pricing platform blocks runs until this is ok (FR-076)."""
    try:
        with session() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    except Exception:
        return HealthResponse(
            status="degraded", service="commerce", version=__version__,
            seeded=False, product_count=0,
        )
    return HealthResponse(
        status="ok" if count > 0 else "degraded",
        service="commerce",
        version=__version__,
        seeded=count > 0,
        product_count=count,
    )
