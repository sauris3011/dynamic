"""Application state: runs, recommendations, compliance, audit, feedback.

Deliberately contains **no business data** — no catalog, no sales, no inventory.
Those belong to the Commerce Service and are reached over HTTP (PRD 4.1). If a
table here ever starts to look like a product table, the service boundary has
been breached.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from pricing.config import get_settings

SCHEMA = """
-- One row per pricing run (W1).
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    completed_at TEXT,
    status       TEXT NOT NULL,          -- running|completed|failed|halted
    trigger      TEXT NOT NULL,          -- manual|loop|a2a
    scope_kind   TEXT NOT NULL,          -- all|category|skus
    scope_value  TEXT,
    objective    TEXT NOT NULL,          -- revenue|margin|balanced
    mode         TEXT NOT NULL,          -- operating mode in force (FR-104)
    sku_count    INTEGER NOT NULL DEFAULT 0,
    duration_ms  INTEGER,
    error        TEXT,
    quality_json TEXT,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    cost_usd     REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

-- One row per SKU per run (FR-022).
CREATE TABLE IF NOT EXISTS recommendations (
    rec_id                 TEXT PRIMARY KEY,
    run_id                 TEXT NOT NULL REFERENCES runs(run_id),
    sku                    TEXT NOT NULL,
    product_name           TEXT,
    category               TEXT,
    current_price          REAL NOT NULL,
    recommended_price      REAL NOT NULL,
    delta_abs              REAL NOT NULL,
    delta_pct              REAL NOT NULL,
    unit_cost              REAL NOT NULL,
    expected_revenue_delta REAL,
    expected_margin_delta  REAL,
    revenue_ci_low         REAL,
    revenue_ci_high        REAL,
    prob_below_margin      REAL,
    variance               REAL,
    confidence             REAL NOT NULL,
    elasticity             REAL,
    elasticity_ci_low      REAL,
    elasticity_ci_high     REAL,
    elasticity_samples     INTEGER,
    baseline_price         REAL,          -- rule-based comparator (FR-057)
    band                   TEXT NOT NULL, -- auto_approve|review|escalate
    band_reason            TEXT NOT NULL,
    compliance_status      TEXT NOT NULL, -- pass|violation
    damped                 INTEGER NOT NULL DEFAULT 0,
    oscillating            INTEGER NOT NULL DEFAULT 0,
    rationale              TEXT,
    citations_json         TEXT NOT NULL DEFAULT '[]',
    status                 TEXT NOT NULL, -- pending|approved|rejected|overridden|pushed|failed
    final_price            REAL,
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rec_run  ON recommendations(run_id);
CREATE INDEX IF NOT EXISTS idx_rec_sku  ON recommendations(sku, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rec_band ON recommendations(band, status);

-- Every rule evaluation, pass or fail (FR-035).
CREATE TABLE IF NOT EXISTS compliance_evals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id       TEXT NOT NULL REFERENCES recommendations(rec_id),
    rule_code    TEXT NOT NULL,
    passed       INTEGER NOT NULL,
    actual_value REAL,
    threshold    REAL,
    detail       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ce_rec ON compliance_evals(rec_id);

-- Human and system decisions (FR-024, FR-097).
CREATE TABLE IF NOT EXISTS approvals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rec_id     TEXT NOT NULL REFERENCES recommendations(rec_id),
    actor      TEXT NOT NULL,           -- 'system' for automatic approvals
    action     TEXT NOT NULL,           -- approve|reject|override|revert
    reason     TEXT,
    mode       TEXT NOT NULL,
    price      REAL,
    created_at TEXT NOT NULL
);

-- Append-only. Never UPDATE or DELETE from this table (FR-059).
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);

CREATE TABLE IF NOT EXISTS push_batches (
    batch_key   TEXT PRIMARY KEY,
    run_id      TEXT,
    created_at  TEXT NOT NULL,
    item_count  INTEGER NOT NULL,
    applied     INTEGER NOT NULL,
    rejected    INTEGER NOT NULL,
    result_json TEXT NOT NULL
);

-- Price the platform has seen for a SKU over time. Drives oscillation
-- detection (FR-090) without needing to re-query commerce each run.
CREATE TABLE IF NOT EXISTS price_observations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sku         TEXT NOT NULL,
    price       REAL NOT NULL,
    observed_at TEXT NOT NULL,
    run_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_po_sku ON price_observations(sku, observed_at DESC);

-- Closed feedback loop (FR-107 .. FR-110).
CREATE TABLE IF NOT EXISTS outcomes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                TEXT NOT NULL,
    run_id             TEXT,
    rec_id             TEXT,
    forecast_revenue   REAL,
    realized_revenue   REAL,
    forecast_error_pct REAL,
    measured_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_out_sku ON outcomes(sku, measured_at DESC);

-- Current elasticity belief per SKU, refined by observed outcomes (FR-107).
-- `refinement_count` and `total_adjustment` make the learning inspectable and
-- bounded (FR-111) — a loop that silently drifts is worse than no loop.
CREATE TABLE IF NOT EXISTS elasticity_estimates (
    sku              TEXT PRIMARY KEY,
    elasticity       REAL NOT NULL,
    ci_low           REAL NOT NULL,
    ci_high          REAL NOT NULL,
    sample_size      INTEGER NOT NULL,
    r_squared        REAL,
    refinement_count INTEGER NOT NULL DEFAULT 0,
    total_adjustment REAL NOT NULL DEFAULT 0.0,
    updated_at       TEXT NOT NULL
);

-- Runtime-mutable settings (mode, band thresholds) so the UI can change them
-- without a restart (FR-067, FR-100).
CREATE TABLE IF NOT EXISTS settings_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Saved what-if scenarios (FR-048, FR-049). Stores the full result, not just
-- the inputs: a scenario compared months later must show what was actually
-- projected at the time, not what the current model would say now.
CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT 'operator',
    horizon_days INTEGER NOT NULL DEFAULT 28,
    request_json TEXT NOT NULL,
    result_json  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scenarios_created ON scenarios(created_at DESC);

CREATE TABLE IF NOT EXISTS loop_state (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    running          INTEGER NOT NULL DEFAULT 0,
    interval_seconds INTEGER NOT NULL DEFAULT 300,
    scope_json       TEXT NOT NULL DEFAULT '{}',
    started_at       TEXT,
    stopped_at       TEXT,
    iterations       INTEGER NOT NULL DEFAULT 0,
    last_run_id      TEXT,
    last_error       TEXT
);
"""


def _db_path():
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s.app_db_path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
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
        conn.execute(
            "INSERT OR IGNORE INTO loop_state (id, running) VALUES (1, 0)"
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit(
    conn: sqlite3.Connection,
    actor: str,
    event_type: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    **detail: Any,
) -> None:
    """Append an audit record. Never updates or deletes (FR-059)."""
    conn.execute(
        "INSERT INTO audit_log (ts, actor, event_type, entity_type, entity_id,"
        " detail_json) VALUES (?,?,?,?,?,?)",
        (now_iso(), actor, event_type, entity_type, entity_id, json.dumps(detail, default=str)),
    )


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings_kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings_kv (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (key, value, now_iso()),
    )
