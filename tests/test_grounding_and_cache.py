"""Grounding, structured-output repair, caching, and redaction.

The repair-retry test is the one worth reading twice. PRD 4.3 requires a single
retry that feeds the validation error back, then a typed failure — not an
unbounded loop and not a silently unvalidated dict. Both failure modes are
plausible mistakes, so both are asserted against.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from pricing.llm import grounded
from pricing.llm.schemas import PricingRationale


class Answer(BaseModel):
    verdict: str = Field(min_length=3)


class FlakyStructured:
    """Fails schema validation once, then succeeds — the repair path."""

    def __init__(self, failures: int = 1):
        self.calls = 0
        self.failures = failures

    def invoke(self, prompt: str):
        self.calls += 1
        if self.calls <= self.failures:
            raise ValidationError.from_exception_data("Answer", [])
        assert "validation_error" in prompt, (
            "the repair attempt did not feed the error back to the model"
        )
        return Answer(verdict="fine")


class FakeModel:
    def __init__(self, structured):
        self._structured = structured

    def with_structured_output(self, schema, method="json_schema"):
        return self._structured

    def invoke(self, prompt: str):
        class Response:
            content = "plain text answer"
            usage_metadata = {"input_tokens": 11, "output_tokens": 7}

        return Response()


@pytest.fixture()
def wired(monkeypatch, app_db):
    """Gateway reachable, retrieval empty, cache on a temp database."""
    from pricing.llm import registry

    monkeypatch.setattr(
        grounded.registry, "probe_gateway",
        lambda: registry.GatewayProbe(reachable=True, available_models=["m"]),
    )
    monkeypatch.setattr(grounded.rag_store, "retrieve", lambda *a, **k: [])
    return monkeypatch


def test_structured_output_repairs_once_and_succeeds(wired):
    """PRD 4.3 — one repair-retry with the validation error fed back."""
    flaky = FlakyStructured(failures=1)
    wired.setattr(grounded, "_build_model", lambda alias, temp: FakeModel(flaky))

    result = grounded.call(role="analyst", system="s", user="u", schema=Answer,
                           use_cache=False)
    assert result.ok and result.repaired
    assert flaky.calls == 2, "expected exactly one repair attempt"


def test_repair_is_not_an_unbounded_retry_loop(wired):
    """A model that keeps failing must produce a typed error, not spin."""
    flaky = FlakyStructured(failures=99)
    wired.setattr(grounded, "_build_model", lambda alias, temp: FakeModel(flaky))

    result = grounded.call(role="analyst", system="s", user="u", schema=Answer,
                           use_cache=False)
    assert not result.ok and result.error
    assert flaky.calls == 2, "the repair path retried more than once"


def test_an_unreachable_gateway_degrades_instead_of_raising(monkeypatch, app_db):
    """A pricing decision must never depend on a model being available."""
    from pricing.llm import registry

    monkeypatch.setattr(
        grounded.registry, "probe_gateway",
        lambda: registry.GatewayProbe(reachable=False, error="connection refused"),
    )
    monkeypatch.setattr(grounded.rag_store, "retrieve", lambda *a, **k: [])

    result = grounded.call(role="strategist", system="s", user="u", use_cache=False)
    assert not result.ok
    assert "unavailable" in result.error.lower()


def test_grounding_context_and_citations_reach_the_prompt(wired):
    """FR-042 — retrieved context is attributed with usable source ids."""
    from pricing.rag.store import RetrievedChunk

    chunk = RetrievedChunk(
        text="MAP agreements bind from 2026-01-14.", source="policy.pdf",
        collection="pricing_policy", chunk_index=2, distance=0.1,
    )
    wired.setattr(grounded.rag_store, "retrieve", lambda *a, **k: [chunk])
    captured = {}

    class Capturing(FakeModel):
        def invoke(self, prompt):
            captured["prompt"] = prompt
            return super().invoke(prompt)

    wired.setattr(grounded, "_build_model", lambda a, t: Capturing(None))
    result = grounded.call(role="strategist", system="s", user="margin policy",
                           use_cache=False)

    assert "<grounding_context>" in captured["prompt"]
    assert "pricing_policy:policy.pdf#2" in captured["prompt"]
    assert result.citations[0]["id"] == "pricing_policy:policy.pdf#2"


# --- Temperature (model-family compatibility) -----------------------------

def test_temperature_comes_from_configuration():
    """D4/NFR-032 — model behaviour is configured, not hardcoded.

    The gpt-5 family rejects any explicit temperature other than 1.0 with a
    400, so the default has to be the value every model accepts.
    """
    from pricing.config import Settings

    assert Settings().llm_temperature == 1.0
    assert Settings(LLM_TEMPERATURE=0.2).llm_temperature == 0.2


def test_the_configured_temperature_reaches_the_model(wired):
    captured = {}

    def build(alias, temperature):
        captured["temperature"] = temperature
        return FakeModel(None)

    wired.setattr(grounded, "_build_model", build)
    grounded.call(role="analyst", system="s", user="u", use_cache=False)
    assert captured["temperature"] == 1.0


def test_an_explicit_temperature_overrides_configuration(wired):
    captured = {}
    wired.setattr(grounded, "_build_model",
                  lambda a, t: (captured.setdefault("t", t), FakeModel(None))[1])

    grounded.call(role="analyst", system="s", user="u", temperature=0.4,
                  use_cache=False)
    assert captured["t"] == 0.4


def test_a_model_refusing_the_temperature_is_retried_without_it(wired):
    """Losing the narration to a parameter the model dislikes would be an
    absurd trade: omitting temperature is accepted by every model, so the
    call retries that way rather than failing."""
    attempts: list[float | None] = []

    class Picky(FakeModel):
        def invoke(self, prompt):
            if attempts[-1] is not None:
                raise RuntimeError(
                    "BadRequestError: 400 - litellm.UnsupportedParamsError: "
                    "gpt-5 models don't support temperature=1.0. Only "
                    "temperature=1 is supported."
                )
            return super().invoke(prompt)

    def build(alias, temperature):
        attempts.append(temperature)
        return Picky(None)

    wired.setattr(grounded, "_build_model", build)
    result = grounded.call(role="analyst", system="s", user="u", use_cache=False)

    assert result.ok, result.error
    assert attempts == [1.0, None], "expected one retry with temperature omitted"


def test_an_unrelated_failure_is_not_retried(wired):
    """The retry must be narrow. Re-sending a prompt the model rejected for a
    different reason wastes a round trip and muddies the error."""
    attempts: list[float | None] = []

    class Broken(FakeModel):
        def invoke(self, prompt):
            raise RuntimeError("BadRequestError: 400 - context length exceeded")

    def build(alias, temperature):
        attempts.append(temperature)
        return Broken(None)

    wired.setattr(grounded, "_build_model", build)
    result = grounded.call(role="analyst", system="s", user="u", use_cache=False)

    assert not result.ok
    assert "context length" in result.error
    assert attempts == [1.0], "an unrelated 400 should not trigger the retry"


def test_omitting_temperature_is_distinct_from_sending_a_value(monkeypatch):
    """`temperature=None` must drop the parameter entirely, not send null —
    the whole point of the fallback is that nothing is sent."""
    captured = {}

    def fake_init(alias, **kwargs):
        captured.update(kwargs)
        return FakeModel(None)

    monkeypatch.setitem(grounded._model_cache, "unused", None)
    grounded._model_cache.clear()
    monkeypatch.setattr("langchain.chat_models.init_chat_model", fake_init)

    grounded._build_model("vendor/m", None)
    assert "temperature" not in captured

    grounded._model_cache.clear()
    grounded._build_model("vendor/m", 0.7)
    assert captured["temperature"] == 0.7
    grounded._model_cache.clear()


def test_retrieval_breadth_is_tuned_per_role():
    """PRD 4.4 — narration needs little context; the strategist needs the most."""
    k = grounded.ROLE_RETRIEVAL_K
    assert k["router"] == 0 < k["narrator"] < k["analyst"] < k["strategist"]


def test_a_recommendation_schema_demands_a_real_rationale():
    """FR-023 — an ungrounded explanation reads authoritative while resting on
    nothing, so it is rejected at validation rather than displayed."""
    with pytest.raises(ValidationError):
        PricingRationale(rationale="too short")
    assert PricingRationale(rationale="x" * 60).citations == []


# --- Cache (FR-073) -------------------------------------------------------

def test_exact_cache_hit_returns_without_calling_the_model(wired):
    calls = {"n": 0}

    class Counting(FakeModel):
        def invoke(self, prompt):
            calls["n"] += 1
            return super().invoke(prompt)

    wired.setattr(grounded, "_build_model", lambda a, t: Counting(None))

    first = grounded.call(role="analyst", system="s", user="identical question")
    second = grounded.call(role="analyst", system="s", user="identical question")

    assert first.cache_hit == "miss" and second.cache_hit == "exact"
    assert calls["n"] == 1, "a cache hit still reached the model"


def test_changing_the_grounding_invalidates_the_cache_key(wired):
    """The composed prompt is the key, so new evidence cannot return a stale
    answer — a wrong cache hit is worse than a miss."""
    from pricing.db import cache_db

    a = grounded._compose_prompt("s", "u", [])
    from pricing.rag.store import RetrievedChunk

    chunk = RetrievedChunk("new policy", "p.pdf", "pricing_policy", 0, 0.1)
    b = grounded._compose_prompt("s", "u", [chunk])
    assert cache_db.prompt_hash("m", a) != cache_db.prompt_hash("m", b)


def test_cache_statistics_are_exposed(wired, app_db):
    from pricing.db import cache_db

    wired.setattr(grounded, "_build_model", lambda a, t: FakeModel(None))
    grounded.call(role="analyst", system="s", user="stats question")
    grounded.call(role="analyst", system="s", user="stats question")

    stats = cache_db.stats()
    assert stats["exact_hits"] >= 1 and stats["misses"] >= 1
    assert 0.0 < stats["hit_rate"] <= 1.0


# --- Redaction (NFR-012) --------------------------------------------------

def test_secrets_and_pii_are_redacted_from_logs():
    from pricing.core.logging import redaction_processor

    event = redaction_processor(None, None, {
        "api_key": "sk-abcdef0123456789abcdef",
        "note": "contact priya@retailer.example for the key",
        "headers": {"Authorization": "Bearer abcdef0123456789"},
        "price": 4.99,
    })
    assert event["api_key"] == "[REDACTED]"
    assert "priya@retailer.example" not in event["note"]
    assert event["headers"]["Authorization"] == "[REDACTED]"
    assert event["price"] == 4.99, "redaction damaged a legitimate field"


def test_telemetry_inherits_the_same_redaction(tmp_path, monkeypatch):
    """A secret must not reach a trace by a route the logger would have blocked."""
    from pricing.config import get_settings
    from pricing.core import telemetry

    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path, raising=False)
    telemetry.record("test_event", api_key="sk-0123456789abcdef0123", price=4.99)

    written = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert "sk-0123456789abcdef" not in written
    assert "[REDACTED]" in written and "4.99" in written
    get_settings.cache_clear()
