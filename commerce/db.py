"""SQLite schema and connection management for the Commerce Service.

Embedded, in-process, file-based — no database server (NFR-004). WAL mode so
reads do not block the writer (NFR-005).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
DB_PATH = DATA_DIR / "commerce.db"

SCHEMA = """
-- Product catalog. `family_id` groups variants of the same product so the
-- pricing platform can check price-ladder consistency (FR-031).
CREATE TABLE IF NOT EXISTS products (
    sku           TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    subcategory   TEXT NOT NULL,
    brand         TEXT NOT NULL,
    family_id     TEXT NOT NULL,
    size_value    REAL NOT NULL,
    size_unit     TEXT NOT NULL,
    unit_cost     REAL NOT NULL,
    map_price     REAL,              -- minimum advertised price, may be NULL
    list_price    REAL NOT NULL,
    current_price REAL NOT NULL,
    launched_on   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_family   ON products(family_id);

-- Transaction history. One row per SKU per day.
CREATE TABLE IF NOT EXISTS sales (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sku        TEXT NOT NULL REFERENCES products(sku),
    sale_date  TEXT NOT NULL,
    units      INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    revenue    REAL NOT NULL,
    on_promo   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sales_sku_date ON sales(sku, sale_date);

CREATE TABLE IF NOT EXISTS inventory (
    sku             TEXT PRIMARY KEY REFERENCES products(sku),
    on_hand         INTEGER NOT NULL,
    on_order        INTEGER NOT NULL DEFAULT 0,
    weekly_velocity REAL NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Append-only price change log. Every mutation lands here (FR-054).
CREATE TABLE IF NOT EXISTS price_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sku        TEXT NOT NULL REFERENCES products(sku),
    old_price  REAL NOT NULL,
    new_price  REAL NOT NULL,
    changed_at TEXT NOT NULL,
    batch_key  TEXT,
    source     TEXT NOT NULL,       -- 'seed' | 'pricing-platform' | 'manual'
    reason     TEXT
);
CREATE INDEX IF NOT EXISTS idx_price_history_sku ON price_history(sku, changed_at);

-- Idempotency ledger (FR-052, D15). A repeated batch_key is a no-op and the
-- original outcome is replayed verbatim.
CREATE TABLE IF NOT EXISTS price_batches (
    batch_key      TEXT PRIMARY KEY,
    received_at    TEXT NOT NULL,
    item_count     INTEGER NOT NULL,
    applied_count  INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    result_json    TEXT NOT NULL
);

-- EVALUATION ONLY. The ground-truth elasticity used to synthesize sales
-- history (FR-002). This is what makes "pricing accuracy" objectively
-- measurable rather than merely asserted (PRD 9.1).
--
-- The pricing platform MUST NOT read this during a pricing run. It is served
-- exclusively from /eval/ground-truth, which exists so the scoring harness can
-- compare recommendations against the true optimum. Reading it inside the
-- pipeline would be marking your own homework.
CREATE TABLE IF NOT EXISTS ground_truth (
    sku             TEXT PRIMARY KEY REFERENCES products(sku),
    true_elasticity REAL NOT NULL,
    base_demand     REAL NOT NULL,
    ref_price       REAL NOT NULL
);
"""


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    """Open a connection with WAL and foreign keys enabled."""
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Transactional scope. Commits on success, rolls back on exception."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)


def is_seeded() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        with session() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()
            return bool(row and row["n"] > 0)
    except sqlite3.Error:
        return False
