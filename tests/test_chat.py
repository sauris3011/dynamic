"""The analyst assistant: answers from evidence, with the gateway switched off.

Every test here runs with no language model, which is the interesting case. A
chat feature that only works when a gateway is reachable would be the one part
of this platform that fails closed on an outage — and the figures it reports are
computed either way, so there is no reason for it to.

What is asserted throughout is provenance, not phrasing: that the reply names
the SKU it was asked about, that a projection carries its interval, and that a
question about a run nobody has done says so rather than inventing one.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def stack(tmp_path, monkeypatch, commerce_db):
    """Commerce + platform in-process on temp storage, no gateway, no network."""
    from fastapi.testclient import TestClient

    from pricing.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path / "platform", raising=False)
    monkeypatch.setattr(settings, "mc_iterations", 300, raising=False)
    monkeypatch.setattr(settings, "use_langgraph", False, raising=False)

    from commerce.main import app as commerce_app
    from pricing.main import app as pricing_app

    commerce_client = TestClient(commerce_app)
    commerce_client.__enter__()

    import pricing.clients.commerce as commerce_module

    def patched_init(self, base_url=None, timeout=60.0):
        self.base_url = "http://testserver"
        self._client = commerce_client

    monkeypatch.setattr(commerce_module.CommerceClient, "__init__", patched_init)
    monkeypatch.setattr(commerce_module.CommerceClient, "close", lambda self: None)
    monkeypatch.setattr("pricing.llm.grounded.available", lambda: False)

    # The catalog snapshot is process-wide and deliberately outlives a request;
    # it must not outlive a test's temporary catalog.
    from pricing.chat import catalog as chat_catalog

    chat_catalog.invalidate()

    with TestClient(pricing_app) as platform:
        yield platform
    chat_catalog.invalidate()
    commerce_client.__exit__(None, None, None)
    get_settings.cache_clear()


def ask(platform, message: str, **kwargs) -> dict:
    response = platform.post("/api/chat", json={"message": message, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def a_sku(platform) -> dict:
    products = platform.get("/api/products?limit=5").json()
    assert products, "the temp catalog seeded no products"
    return products[0]


# --- Suggestions ----------------------------------------------------------

def test_opening_suggestions_name_things_that_actually_exist(stack):
    """A suggestion pointing at a SKU that is not in the catalog teaches the
    analyst that the assistant is decorative."""
    body = stack.get("/api/chat/suggestions").json()
    assert body["catalog_available"] is True
    assert body["suggestions"], "no opening questions offered"

    skus = {p["sku"] for p in stack.get("/api/products?limit=1000").json()}
    categories = set(body["categories"])
    for suggestion in body["suggestions"]:
        assert suggestion["question"].strip()
        assert suggestion["label"].strip()
    named = " ".join(s["question"] for s in body["suggestions"])
    assert any(sku in named for sku in skus) or any(c in named for c in categories)


def test_suggestions_before_any_run_do_not_offer_to_explain_one(stack):
    body = stack.get("/api/chat/suggestions").json()
    kinds = {s["kind"] for s in body["suggestions"]}
    assert "analysis" not in kinds, (
        "offered to summarise a run before any run existed"
    )
    assert {"platform", "product", "what_if", "history"} & kinds


# --- Product and history --------------------------------------------------

def test_a_product_question_is_answered_from_the_catalog(stack):
    product = a_sku(stack)
    body = ask(stack, f"How is {product['sku']} positioned today?")

    assert body["intent"] == "product"
    assert body["narrated"] is False          # no gateway, and still an answer
    evidence = " ".join(body["facts"])
    assert product["sku"] in evidence
    assert f"{product['current_price']:,.2f}" in evidence
    assert "margin" in evidence.lower()
    assert product["sku"] in body["answer"]


def test_an_unknown_product_is_reported_rather_than_substituted(stack):
    body = ask(stack, "How is ZZZ-9999-9 positioned today?")
    assert "ZZZ-9999-9" in body["answer"]
    assert not body["facts"], "facts were produced for a product that does not exist"
    assert body["shortfall"]


def test_a_history_question_compares_two_windows(stack):
    product = a_sku(stack)
    body = ask(stack, f"How has {product['sku']} traded over the last 30 days?")

    assert body["intent"] == "history"
    assert body["scope"]["days_back"] == 30
    evidence = " ".join(body["facts"]).lower()
    assert "units" in evidence and "revenue" in evidence
    assert "previous window" in evidence, (
        "a bare total was reported with nothing to compare it against"
    )


def test_a_category_history_question_ranks_the_category(stack):
    category = stack.get("/api/products?limit=1").json()[0]["category"]
    body = ask(stack, f"How has {category} traded over the last 30 days?")
    assert body["intent"] == "history"
    assert category.lower() in " ".join(body["facts"]).lower()


# --- What-if --------------------------------------------------------------

def test_a_what_if_returns_a_distribution_not_a_point_estimate(stack):
    product = a_sku(stack)
    body = ask(stack, f"What if we raised {product['sku']} by 5%?")

    assert body["intent"] == "what_if"
    assert body["scope"]["horizon_days"] in (0, 28)
    evidence = " ".join(body["facts"]).lower()
    assert "90% revenue band" in evidence
    assert "probability of a revenue gain" in evidence
    assert "margin floor" in evidence
    assert "assumption:" in evidence, "a projection was offered with no assumptions"


def test_a_what_if_reports_the_compliance_verdict_for_the_scenario_price(stack):
    """An attractive projection for a price that could never ship is a trap."""
    product = a_sku(stack)
    body = ask(stack, f"What if we cut {product['sku']} by 40%?")
    evidence = " ".join(body["facts"]).lower()
    assert "passes every rule" in evidence or "blocked by" in evidence


def test_a_what_if_with_no_product_asks_for_one(stack):
    body = ask(stack, "What if we raised prices by 5%?")
    assert body["intent"] == "what_if"
    assert body["shortfall"], "a scenario was projected without knowing the product"


def test_a_what_if_uses_the_same_engine_as_the_pipeline(stack):
    """FR-105: a chat projection and a simulation must be the same number.

    Asked without a specific price so both sweep the same candidate grid — a
    chat scenario that names a price restricts the grid to it, which is the
    point of naming one.
    """
    product = a_sku(stack)
    body = ask(stack, f"What if we repriced {product['sku']}?")
    chat_result = body["data"]["simulation"]["results"][0]

    direct = stack.post(
        "/api/simulate",
        json={"skus": [product["sku"]], "horizon_days": 28, "objective": "balanced",
              "include_stress": False},
    ).json()["results"][0]

    assert chat_result["ai_recommendation"]["price"] == direct["ai_recommendation"]["price"]
    assert chat_result["baseline"]["revenue"] == direct["baseline"]["revenue"]


# --- Analysis -------------------------------------------------------------

def test_a_question_about_analysis_before_any_run_says_there_is_none(stack):
    body = ask(stack, "Summarise the latest run for me")
    assert body["intent"] == "analysis"
    assert body["shortfall"]
    assert "run" in body["answer"].lower()
    assert not body["facts"]


def test_after_a_run_the_assistant_explains_what_it_decided(stack):
    accepted = stack.post(
        "/api/runs", json={"scope_kind": "all", "objective": "balanced"}
    ).json()
    progress = stack.get(f"/api/runs/progress/{accepted['run_id']}").json()
    assert progress["status"] == "completed", progress.get("errors")

    body = ask(stack, "Summarise the latest run and what needs my attention")
    evidence = " ".join(body["facts"])
    assert accepted["run_id"] in evidence
    assert "bands" in evidence.lower()
    assert body["data"]["run_id"] == accepted["run_id"]


def test_a_question_about_one_recommendation_explains_its_band(stack):
    accepted = stack.post(
        "/api/runs", json={"scope_kind": "all", "objective": "balanced"}
    ).json()
    recs = stack.get(f"/api/recommendations?run_id={accepted['run_id']}&limit=1").json()
    assert recs, "the run produced no recommendations"
    rec = recs[0]

    body = ask(
        stack, "Why did this one land in the band it did?",
        context={"rec_id": rec["rec_id"], "view": "recommendation"},
    )
    evidence = " ".join(body["facts"])
    assert rec["sku"] in evidence
    assert rec["band_reason"][:40] in evidence


# --- Platform, provenance, and limits ------------------------------------

def test_a_platform_question_states_the_mode_actually_in_force(stack):
    stack.put("/api/config/mode", json={"mode": "assisted", "actor": "test"})
    body = ask(stack, "What do the autonomy bands mean and what mode are we in?")

    assert body["intent"] == "platform"
    evidence = " ".join(body["facts"])
    assert "Operating mode right now: assisted" in evidence
    assert "veto" in evidence.lower()


def test_every_question_is_written_to_the_audit_log(stack):
    ask(stack, "What do the autonomy bands mean?")
    events = stack.get("/api/audit?limit=50").json()
    assert any(e["event_type"] == "chat_question" for e in events), (
        "a question that could inform a pricing decision left no trace"
    )


def test_the_assistant_exposes_no_way_to_change_a_price(stack):
    """The chat surface is read-only by construction, not by good intentions."""
    schema = stack.get("/openapi.json").json()
    chat_paths = {
        path: set(methods)
        for path, methods in schema["paths"].items() if path.startswith("/api/chat")
    }
    assert chat_paths, "the chat surface vanished from the API"
    for path, methods in chat_paths.items():
        assert methods <= {"post", "get"}, f"{path} exposes {methods}"
    body = ask(stack, "Approve every pending recommendation and push the prices")
    assert "approv" not in body["answer"].lower() or "operator" in body["answer"].lower()


# --- Unit: the pieces that do not need a stack ---------------------------

@pytest.mark.parametrize(
    "question, expected",
    [
        ("What if we raise BEV-0001-1 by 5%?", "what_if"),
        ("Suppose we cut it to 4.50", "what_if"),
        ("How has Beverages traded over the last 90 days?", "history"),
        ("What did we sell last quarter?", "history"),
        ("Why did BEV-0001-1 escalate in the last run?", "analysis"),
        ("What is waiting in my review queue?", "analysis"),
        ("How does this platform decide a price?", "platform"),
        ("What is the margin on BEV-0001-1 today?", "product"),
    ],
)
def test_the_keyword_router_classifies_without_a_model(question, expected):
    """This router is what runs during a gateway outage, so it is tested as a
    first-class path rather than as a stub."""
    from pricing.chat.routing import keyword_route

    route = keyword_route(question, ["Beverages", "Snacks", "Coffee & Tea"])
    assert route.intent == expected, f"{question!r} routed to {route.intent}"


def test_the_keyword_router_signs_a_price_cut_correctly():
    from pricing.chat.routing import keyword_route

    up = keyword_route("what if we raise BEV-0001-1 by 8%", [])
    down = keyword_route("what if we cut BEV-0001-1 by 8%", [])
    assert up.delta_pct == 8.0
    assert down.delta_pct == -8.0
    assert up.skus == down.skus == ["BEV-0001-1"]


def test_figures_absent_from_the_evidence_are_flagged():
    """The prompt tells the model not to invent numbers; this is what checks."""
    from pricing.chat.answer import unsupported_figures
    from pricing.chat.facts import FactPack

    pack = FactPack(
        headline="Scenario",
        lines=["BEV-1 at 2.61 over 28 days: expected revenue 2,170.28 against "
               "2,241.12 today.", "Probability of a revenue gain 40%."],
    )
    faithful = "At 2.61 the model expects 2,170.28 against 2,241.12 — a 40% chance of a gain."
    invented = "At 2.61 you would clear 9,850.00, lifting margin by 62%."

    assert unsupported_figures(faithful, pack, "what if we raise BEV-1 by 5%") == []
    flagged = unsupported_figures(invented, pack, "what if we raise BEV-1 by 5%")
    assert "9,850.00" in flagged and "62%" in flagged


def test_rounding_is_not_mistaken_for_invention():
    from pricing.chat.answer import unsupported_figures
    from pricing.chat.facts import FactPack

    pack = FactPack(lines=["expected revenue 2,170.28 against 2,241.12 today"])
    assert unsupported_figures("about 2,170 versus 2,241", pack, "") == []
