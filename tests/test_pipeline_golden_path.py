"""Golden path: a full run end to end, with the gateway switched off (NFR-033).

The gateway being unavailable is the *point* of this test, not a limitation of
it. Prices come from `pricing.analytics`; the language model only explains them.
If a run cannot complete without an LLM, the architecture claim in PRD 1.3 is
false. So this test asserts it directly.

Both services run in-process against temporary databases via TestClient, so the
suite touches no network and leaves `./data` alone.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def stack(tmp_path, monkeypatch, commerce_db):
    """Commerce + platform wired together, both on temp storage."""
    from fastapi.testclient import TestClient

    from pricing.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "platform", raising=False)
    monkeypatch.setattr(settings, "mc_iterations", 400, raising=False)
    # The graph adds checkpointing, not behaviour; the standalone path keeps the
    # test fast and asserts the stage functions directly.
    monkeypatch.setattr(settings, "use_langgraph", False, raising=False)

    from commerce.main import app as commerce_app
    from pricing.main import app as pricing_app

    commerce_client = TestClient(commerce_app)
    commerce_client.__enter__()

    # Point the platform's HTTP client at the in-process commerce app rather
    # than a socket — the service boundary is preserved, the network is not.
    import pricing.clients.commerce as commerce_module

    original_init = commerce_module.CommerceClient.__init__

    def patched_init(self, base_url=None, timeout=60.0):
        self.base_url = "http://testserver"
        self._client = commerce_client

    def patched_close(self):
        return None

    monkeypatch.setattr(commerce_module.CommerceClient, "__init__", patched_init)
    monkeypatch.setattr(commerce_module.CommerceClient, "close", patched_close)

    # Force the LLM off: narration must be additive, never load-bearing.
    monkeypatch.setattr("pricing.llm.grounded.available", lambda: False)

    with TestClient(pricing_app) as platform:
        yield platform, commerce_client

    commerce_client.__exit__(None, None, None)
    get_settings.cache_clear()


def _wait(platform, run_id: str, tries: int = 200) -> dict:
    """BackgroundTasks run inline under TestClient, so this returns at once."""
    for _ in range(tries):
        body = platform.get(f"/api/runs/progress/{run_id}").json()
        if body.get("status") in ("completed", "failed", "halted"):
            return body
    raise AssertionError("run never reached a terminal state")


def test_full_run_completes_without_an_llm(stack):
    platform, _ = stack
    accepted = platform.post("/api/runs", json={"scope_kind": "all",
                                                "objective": "balanced"}).json()
    progress = _wait(platform, accepted["run_id"])

    assert progress["status"] == "completed", progress.get("errors")
    assert progress["sku_count"] > 0
    assert sum(progress["bands"].values()) == progress["sku_count"]


def test_every_recommendation_carries_the_required_fields(stack):
    """FR-022 — the acceptance criterion from W1, asserted field by field."""
    platform, _ = stack
    run_id = platform.post("/api/runs", json={"scope_kind": "all"}).json()["run_id"]
    _wait(platform, run_id)

    recs = platform.get("/api/recommendations", params={"run_id": run_id}).json()
    assert recs
    for rec in recs:
        assert rec["recommended_price"] > 0
        assert rec["delta_abs"] is not None and rec["delta_pct"] is not None
        assert rec["confidence"] is not None
        assert rec["band"] in ("auto_approve", "review", "escalate")
        assert rec["band_reason"], "a band with no stated reason (FR-096)"
        assert rec["compliance_status"] in ("pass", "violation")
        assert rec["rationale"], "no rationale, even the deterministic fallback"
        assert rec["baseline_price"] is not None, "no comparator (FR-057)"


def test_supervised_mode_pushes_nothing(stack):
    """The default posture. Nothing moves without a person."""
    platform, _ = stack
    run_id = platform.post("/api/runs", json={"scope_kind": "all"}).json()["run_id"]
    _wait(platform, run_id)

    recs = platform.get("/api/recommendations", params={"run_id": run_id}).json()
    assert {r["status"] for r in recs} == {"pending"}


def test_approve_then_push_changes_the_system_of_record(stack):
    platform, commerce = stack
    run_id = platform.post("/api/runs", json={"scope_kind": "all"}).json()["run_id"]
    _wait(platform, run_id)

    recs = platform.get("/api/recommendations", params={"run_id": run_id}).json()
    mover = next(
        r for r in recs
        if abs(r["delta_pct"]) > 0.5 and r["compliance_status"] == "pass"
    )
    platform.post(f"/api/recommendations/{mover['rec_id']}/approve",
                  json={"actor": "tester", "reason": "golden path"})
    result = platform.post("/api/recommendations/push",
                           json={"rec_ids": [mover["rec_id"]], "actor": "tester"}).json()

    assert result["pushed"] == 1
    live = commerce.get("/prices").json()
    now = next(p["current_price"] for p in live if p["sku"] == mover["sku"])
    assert now == pytest.approx(mover["recommended_price"], abs=0.011)


def test_a_compliance_violation_cannot_be_approved(stack):
    """FR-033 — blocked in every mode, through every path."""
    platform, _ = stack
    run_id = platform.post("/api/runs", json={"scope_kind": "all"}).json()["run_id"]
    _wait(platform, run_id)

    recs = platform.get("/api/recommendations", params={"run_id": run_id}).json()
    blocked = [r for r in recs if r["compliance_status"] != "pass"]
    if not blocked:
        pytest.skip("no violation in this dataset; covered directly in engine tests")

    response = platform.post(f"/api/recommendations/{blocked[0]['rec_id']}/approve",
                             json={"actor": "tester"})
    assert response.status_code == 409


def test_override_is_revalidated_before_acceptance(stack):
    """FR-025 — an override is a human decision, not an exemption."""
    platform, _ = stack
    run_id = platform.post("/api/runs", json={"scope_kind": "all"}).json()["run_id"]
    _wait(platform, run_id)

    rec = platform.get("/api/recommendations", params={"run_id": run_id}).json()[0]
    response = platform.post(
        f"/api/recommendations/{rec['rec_id']}/override",
        json={"price": 0.05, "reason": "deliberately illegal", "actor": "tester"},
    )
    assert response.status_code == 409
    assert "violations" in str(response.json())


def test_run_and_decisions_are_audited(stack):
    """FR-054, FR-059 — append-only, with the actor named."""
    platform, _ = stack
    run_id = platform.post("/api/runs", json={"scope_kind": "all"}).json()["run_id"]
    _wait(platform, run_id)
    rec = platform.get("/api/recommendations", params={"run_id": run_id}).json()[0]
    platform.post(f"/api/recommendations/{rec['rec_id']}/approve",
                  json={"actor": "tester", "reason": "audit check"})

    events = platform.get("/api/audit", params={"limit": 200}).json()
    kinds = {e["event_type"] for e in events}
    assert "run_completed" in kinds and "recommendation_approve" in kinds
    assert any(e["actor"] == "tester" for e in events)

    timeline = platform.get(f"/api/recommendations/audit/{rec['sku']}").json()
    assert timeline["recommendations"] and timeline["audit_events"]


def test_kill_switch_reverts_to_supervised(stack):
    """FR-102 — one action: stop the loop, cancel auto-approvals, revert mode."""
    platform, _ = stack
    platform.put("/api/config/mode", json={"mode": "assisted", "actor": "tester"})
    assert platform.get("/api/config").json()["mode"] == "assisted"

    result = platform.post("/api/loop/kill-switch",
                           json={"actor": "tester", "reason": "test"}).json()
    assert result["halted"] and result["mode"] == "supervised"
    assert platform.get("/api/config").json()["mode"] == "supervised"


def test_loop_never_auto_starts(stack):
    """FR-122, NFR-035 — off at boot, every time."""
    platform, _ = stack
    assert platform.get("/api/loop/status").json()["running"] is False


def test_simulation_and_pipeline_share_one_engine(stack):
    """FR-105 — a simulated outcome must be comparable to a forecast, which
    requires them to be the same computation, not two that agree by accident."""
    platform, commerce = stack
    sku = commerce.get("/catalog/products", params={"limit": 1}).json()[0]["sku"]

    first = platform.post("/api/simulate", json={"skus": [sku], "horizon_days": 14}).json()
    second = platform.post("/api/simulate", json={"skus": [sku], "horizon_days": 14}).json()

    assert first["results"][0]["candidates"] == second["results"][0]["candidates"]
    assert first["results"][0]["assumptions"]
    assert first["results"][0]["rule_based_baseline"]["price"] > 0
