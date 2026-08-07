"""Pricing AI Platform entry point (port 8000).

Run:  python -m uvicorn pricing.main:app --port 8000
"""

from __future__ import annotations

import signal
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pricing import __version__
from pricing.config import get_settings
from pricing.core import telemetry
from pricing.core.logging import configure_logging, get_logger
from pricing.core import tls
from pricing.core.tls import tls_status
from pricing.db import app_db, cache_db
from pricing.routes import (
    a2a,
    chat,
    feedback,
    loop,
    metrics,
    ops,
    products,
    rag,
    recommendations,
    runs,
    simulation,
)

logger = get_logger("pricing.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    # Before anything makes an outbound call. Third-party libraries build their
    # own SSL contexts, so this has to be process-wide to be worth anything.
    if settings.use_os_trust_store:
        tls.install_os_trust_store()

    app_db.init_db()
    cache_db.init_db()

    status = tls_status(settings)
    logger.info(
        "platform.startup",
        version=__version__,
        commerce=settings.commerce_base_url,
        mode=settings.operating_mode.value,
        tls_mode=status["mode"],
        data_dir=str(settings.data_dir.resolve()),
    )
    if not status["secure"]:
        # NFR-019: never let an insecure TLS posture pass quietly.
        logger.warning("platform.insecure_tls", detail=status["detail"])

    yield

    # Graceful shutdown (NFR-023). Stop the loop first so no iteration is
    # mid-flight while connections close.
    try:
        from pricing.services import loop as loop_service

        loop_service.stop(actor="shutdown")
    except Exception:
        pass
    telemetry.flush()          # never raises (NFR-025)
    logger.info("platform.shutdown", version=__version__)


app = FastAPI(
    title="Dynamic Pricing Engine — AI Platform",
    description=(
        "AI solution layer: five-agent pipeline, deterministic analytics, "
        "compliance veto, and banded autonomy. Owns no business data — catalog, "
        "sales, inventory and the price book live in the Commerce Service and "
        "are reached over HTTP only."
    ),
    version=__version__,
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://localhost:{_settings.ui_port}",
        f"http://127.0.0.1:{_settings.ui_port}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(recommendations.router)
app.include_router(loop.router)
app.include_router(rag.router)
app.include_router(simulation.router)
app.include_router(feedback.router)
app.include_router(metrics.router)
app.include_router(ops.router)
app.include_router(products.router)
app.include_router(chat.router)
app.include_router(a2a.router)


@app.get("/health", tags=["ops"])
def health() -> dict:
    return ops.health()


def _handle_signal(signum, _frame):  # pragma: no cover - process-level
    logger.info("platform.signal", signal=signum)
    raise SystemExit(0)


for _sig in (signal.SIGINT, signal.SIGTERM):
    try:
        signal.signal(_sig, _handle_signal)
    except (ValueError, OSError):
        # Not in the main thread (e.g. under the reloader) — uvicorn handles it.
        pass
