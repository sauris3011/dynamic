"""Persist a completed run to app.db, and read it back for the API.

Kept separate from the orchestrator so the pipeline stays a pure transformation
over RunState — that makes it testable without a database and keeps the run
inspectable before anything is written.
"""

from __future__ import annotations

import json
import uuid

from pricing.db.app_db import audit, now_iso, session
from pricing.pipeline.state import RunState, SkuAnalysis


def _rec_id() -> str:
    return f"rec-{uuid.uuid4().hex[:12]}"


def save_run(state: RunState) -> None:
    """Write the run header, every recommendation, and all rule evaluations."""
    with session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, started_at, completed_at, status,"
            " trigger, scope_kind, scope_value, objective, mode, sku_count,"
            " duration_ms, error, quality_json, tokens_in, tokens_out, cost_usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                state.run_id, state.started_at, state.completed_at, state.status,
                state.trigger, state.scope.kind, state.scope.value,
                state.objective.value, state.mode.value, len(state.priced),
                int(state.stage_timings.get("total", 0) * 1000),
                "; ".join(state.errors) if state.errors else None,
                json.dumps(state.quality.to_dict()) if state.quality else None,
                state.tokens_in, state.tokens_out, state.cost_usd,
            ),
        )

        for a in state.priced:
            if a.optimization is None or a.band is None:
                continue
            rec_id = _rec_id()
            _insert_recommendation(conn, rec_id, state, a)
            if a.compliance:
                conn.executemany(
                    "INSERT INTO compliance_evals (rec_id, rule_code, passed,"
                    " actual_value, threshold, detail) VALUES (?,?,?,?,?,?)",
                    [
                        (rec_id, e.code, 1 if e.passed else 0,
                         e.actual_value, e.threshold, e.detail)
                        for e in a.compliance.evaluations
                    ],
                )
            # Price observation feeds oscillation detection on later runs.
            conn.execute(
                "INSERT INTO price_observations (sku, price, observed_at, run_id)"
                " VALUES (?,?,?,?)",
                (a.sku, a.current_price, now_iso(), state.run_id),
            )

        audit(
            conn, actor="system", event_type="run_completed",
            entity_type="run", entity_id=state.run_id,
            status=state.status, sku_count=len(state.priced),
            bands=state.band_counts(), mode=state.mode.value,
            duration_ms=int(state.stage_timings.get("total", 0) * 1000),
        )


def _insert_recommendation(conn, rec_id: str, state: RunState, a: SkuAnalysis) -> None:
    opt, est, out = a.optimization, a.elasticity, a.optimization.outcome
    product = a.context.product
    conn.execute(
        "INSERT INTO recommendations (rec_id, run_id, sku, product_name, category,"
        " current_price, recommended_price, delta_abs, delta_pct, unit_cost,"
        " expected_revenue_delta, expected_margin_delta, revenue_ci_low,"
        " revenue_ci_high, prob_below_margin, variance, confidence, elasticity,"
        " elasticity_ci_low, elasticity_ci_high, elasticity_samples, baseline_price,"
        " band, band_reason, compliance_status, damped, oscillating, rationale,"
        " citations_json, narrated, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rec_id, state.run_id, a.sku, product.get("name"), product.get("category"),
            a.current_price, opt.recommended_price,
            round(opt.recommended_price - a.current_price, 2), round(a.delta_pct, 2),
            product.get("unit_cost", 0.0),
            opt.expected_revenue_delta, opt.expected_margin_delta,
            opt.revenue_ci_low, opt.revenue_ci_high,
            out.prob_below_margin_floor if out else None,
            out.revenue_cv if out else None,
            a.confidence,
            est.elasticity if est and est.usable else None,
            est.ci_low if est and est.usable else None,
            est.ci_high if est and est.usable else None,
            est.sample_size if est else 0,
            a.baseline_price,
            a.band.band.value, a.band.reason,
            "pass" if (a.compliance and a.compliance.passed) else "violation",
            1 if opt.damped else 0,
            1 if (a.stability and a.stability.oscillating) else 0,
            a.rationale, json.dumps(a.citations), 1 if a.narrated else 0,
            "pending", now_iso(),
        ),
    )


# --- Read helpers used by the API ---------------------------------------

def list_runs(limit: int = 50) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: str) -> dict | None:
    with session() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row else None


def list_recommendations(
    run_id: str | None = None, band: str | None = None,
    status: str | None = None, category: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    clauses, params = [], []
    for field_name, value in (
        ("run_id", run_id), ("band", band), ("status", status), ("category", category)
    ):
        if value:
            clauses.append(f"{field_name} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with session() as conn:
        rows = conn.execute(
            f"SELECT * FROM recommendations {where} "
            f"ORDER BY ABS(expected_revenue_delta) DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def get_recommendation(rec_id: str) -> dict | None:
    with session() as conn:
        row = conn.execute(
            "SELECT * FROM recommendations WHERE rec_id = ?", (rec_id,)
        ).fetchone()
        if row is None:
            return None
        rec = dict(row)
        rec["compliance_evals"] = [
            dict(e) for e in conn.execute(
                "SELECT rule_code, passed, actual_value, threshold, detail "
                "FROM compliance_evals WHERE rec_id = ?", (rec_id,)
            )
        ]
        rec["approvals"] = [
            dict(e) for e in conn.execute(
                "SELECT actor, action, reason, mode, price, created_at "
                "FROM approvals WHERE rec_id = ? ORDER BY created_at", (rec_id,)
            )
        ]
    return rec


def price_history_for(sku: str, limit: int = 12) -> list[float]:
    """Prices this platform has observed, newest first — input to oscillation
    detection (FR-090)."""
    with session() as conn:
        rows = conn.execute(
            "SELECT price FROM price_observations WHERE sku = ? "
            "ORDER BY observed_at DESC LIMIT ?", (sku, limit),
        ).fetchall()
    return [r["price"] for r in rows]


def price_history_map(limit_per_sku: int = 12) -> dict[str, list[float]]:
    """Observed price series for every SKU, newest first.

    Loaded in one query rather than per SKU: oscillation detection runs over the
    whole run scope, and 500 individual round-trips to SQLite would cost more
    than the detection itself.
    """
    out: dict[str, list[float]] = {}
    with session() as conn:
        rows = conn.execute(
            "SELECT sku, price FROM price_observations"
            " ORDER BY sku, observed_at DESC"
        ).fetchall()
    for row in rows:
        series = out.setdefault(row["sku"], [])
        if len(series) < limit_per_sku:
            series.append(row["price"])
    return out
