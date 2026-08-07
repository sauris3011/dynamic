"""Shared fixtures (NFR-033).

Every test runs against **temporary databases**, never `./data`. A suite that
mutates the demo dataset is a suite people stop running, and a compliance test
that passes because of leftover state from a previous run is worse than no test.

Nothing here reaches the network. The LLM gateway, Langfuse, and ChromaDB are all
allowed to be absent — the system is designed to degrade rather than fail when
they are, and the tests assert that property rather than working around it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    """An isolated app.db + llm_cache.db under tmp_path."""
    from pricing.config import Settings, get_settings

    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)

    from pricing.db import app_db as module
    from pricing.db import cache_db

    module.init_db()
    cache_db.init_db()
    yield module
    get_settings.cache_clear()
    assert isinstance(settings, Settings)


@pytest.fixture()
def commerce_db(tmp_path, monkeypatch):
    """A seeded Commerce Service database, small enough to be quick."""
    import commerce.db as cdb

    monkeypatch.setattr(cdb, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "commerce.db", raising=False)

    from commerce import seed as seed_module

    seed_module.seed(sku_count=24, history_days=200, seed_value=7)
    return cdb


@pytest.fixture()
def commerce_client(commerce_db):
    """A TestClient over the Commerce Service against the temp database."""
    from fastapi.testclient import TestClient

    from commerce.main import app

    with TestClient(app) as client:
        yield client


def sales_frame(n: int = 180, elasticity: float = -1.8, base: float = 120.0,
                ref_price: float = 4.0, seed: int = 11) -> list[dict]:
    """Synthetic sales rows with a known elasticity, for estimator tests.

    Built here rather than imported from `commerce.seed` so a change to the demo
    generator cannot silently alter what the estimator tests are measuring.
    """
    import math
    import random
    from datetime import date, timedelta

    rng = random.Random(seed)
    start = date(2025, 1, 1)
    rows = []
    price = ref_price
    for i in range(n):
        day = start + timedelta(days=i)
        on_promo = i % 17 in (0, 1, 2, 3)
        price = round(ref_price * (0.78 if on_promo else rng.uniform(0.97, 1.06)), 2)
        units = max(
            1,
            int(round(
                base
                * (price / ref_price) ** elasticity
                * (1.14 if on_promo else 1.0)
                * math.exp(rng.gauss(0.0, 0.10))
            )),
        )
        rows.append({
            "sku": "TEST-1", "sale_date": day.isoformat(), "units": units,
            "unit_price": price, "revenue": round(units * price, 2),
            "on_promo": on_promo,
        })
    return rows
