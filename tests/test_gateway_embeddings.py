"""Gateway embedding client (D3, D4, D5, FR-038, A-03).

Runs against a stubbed LangChain `Embeddings` object rather than the real
gateway: the suite must pass with no network, and an embedding test that
silently skips when egress is blocked tests nothing at all.

The adapter under test exists because Chroma and LangChain disagree about one
method. Both name it `embed_query`, but Chroma passes a list and wants a list of
vectors back, while LangChain passes a string and returns a single vector. Get
that wrong and retrieval fails at query time only — the least convenient moment
to discover it — so both shapes are asserted directly.
"""

from __future__ import annotations

import pytest

from pricing.rag import embeddings
from pricing.rag.gateway_embeddings import (
    EmbeddingGatewayError,
    GatewayEmbeddingFunction,
)


class StubEmbeddings:
    """Stands in for `langchain_openai.OpenAIEmbeddings`.

    Vector content is derived from the input text, so a caller can prove which
    vector came back for which input.
    """

    def __init__(self, dimensions: int = 8, drop: int = 0, fail: bool = False):
        self.dimensions = dimensions
        self.drop = drop
        self.fail = fail
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("gateway exploded")
        vectors = [
            [float(len(t))] + [0.0] * (self.dimensions - 1) for t in texts
        ]
        return vectors[: len(vectors) - self.drop] if self.drop else vectors

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def function(**kwargs) -> GatewayEmbeddingFunction:
    return GatewayEmbeddingFunction("vendor/embed-v1", StubEmbeddings(**kwargs))


# --- Model resolution -----------------------------------------------------

def test_model_id_comes_from_config_not_source():
    """D4 — model IDs are never hardcoded."""
    from pricing.config import Settings

    settings = Settings(MODEL_EMBEDDING="vendor/some-embedding-v9")
    assert settings.model_for_role("embeddings") == "vendor/some-embedding-v9"
    assert settings.gateway_embeddings_enabled


def test_unset_model_disables_the_gateway_path():
    from pricing.config import Settings

    assert not Settings(MODEL_EMBEDDING="").gateway_embeddings_enabled
    assert not Settings(MODEL_EMBEDDING="   ").gateway_embeddings_enabled


def test_embedding_alias_is_not_probed_with_the_chat_roles():
    """A missing embedding model must not disable narration.

    `probe_gateway().usable` gates every LLM call. If the embedding alias were
    in ROLES, a gateway without it would silently switch off all narration over
    an unrelated capability.
    """
    from pricing.llm import registry

    assert "embeddings" not in registry.ROLES


# --- Correctness of the returned vectors ----------------------------------

def test_vectors_come_back_in_input_order():
    fn = function()
    assert [v[0] for v in fn.embed(["a", "bb", "ccc"])] == [1.0, 2.0, 3.0]


def test_a_short_response_is_an_error_not_a_silent_truncation():
    """Dropping a vector would attach every later one to the wrong document."""
    fn = function(drop=1)
    with pytest.raises(EmbeddingGatewayError, match="2 embeddings for 3 inputs"):
        fn.embed(["a", "b", "c"])


def test_a_failing_gateway_raises_a_typed_error():
    with pytest.raises(EmbeddingGatewayError, match="gateway exploded"):
        function(fail=True).embed(["a"])


def test_dimensions_are_recorded_from_the_first_call():
    fn = function(dimensions=16)
    assert fn.dimensions is None
    fn.embed(["a"])
    assert fn.dimensions == 16


# --- Chroma / LangChain interface collision -------------------------------

def test_implements_every_chroma_calling_convention():
    fn = function()
    assert len(fn(["a"])) == 1
    assert len(fn.embed_documents(["a", "b"])) == 2
    assert fn.name() == "gateway:vendor/embed-v1"


def test_embed_query_honours_whichever_convention_called_it():
    """Chroma passes a list and wants a list; LangChain passes a string and
    wants one vector. Returning the wrong shape breaks retrieval only."""
    fn = function()

    chroma_style = fn.embed_query(["a"])
    assert isinstance(chroma_style, list) and isinstance(chroma_style[0], list)

    langchain_style = fn.embed_query("a")
    assert isinstance(langchain_style, list)
    assert isinstance(langchain_style[0], float)


def test_empty_input_is_a_no_op():
    assert function().embed([]) == []


def test_blank_text_still_yields_an_aligned_vector():
    """Embedding APIs reject empty strings. Dropping the input would shift every
    subsequent vector onto the wrong document."""
    stub = StubEmbeddings()
    fn = GatewayEmbeddingFunction("vendor/embed-v1", stub)

    assert len(fn.embed(["", "text"])) == 2
    assert stub.calls[0][0] == " ", "blank input was not substituted"


# --- Strategy selection ---------------------------------------------------

def test_unset_alias_falls_through_to_a_local_strategy(monkeypatch):
    """No gateway model configured is a normal state, not a failure."""
    from pricing.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "model_embedding", "", raising=False)

    fn, description, dimensions = embeddings.build()
    assert embeddings.active() in ("minilm", "hashed")
    assert dimensions > 0 and description
    assert len(fn(["probe"])[0]) == dimensions
    get_settings.cache_clear()


def test_a_failing_gateway_degrades_rather_than_raising(monkeypatch):
    """A pricing run must never fail because a document store is unavailable."""
    from pricing.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "model_embedding", "vendor/nope",
                        raising=False)
    monkeypatch.setattr(
        "pricing.rag.gateway_embeddings.build",
        lambda *a, **k: (_ for _ in ()).throw(EmbeddingGatewayError("unreachable")),
    )

    fn, description, dimensions = embeddings.build()
    assert embeddings.active() in ("minilm", "hashed")
    assert "unreachable" in embeddings.degraded_reason()
    assert len(fn(["probe"])[0]) == dimensions
    get_settings.cache_clear()


def test_the_hashed_fallback_is_deterministic_and_normalised():
    """It must never itself fail — it is the floor of the chain."""
    from pricing.rag.embeddings import hashed_embedding

    first = hashed_embedding("minimum advertised price")
    second = hashed_embedding("minimum advertised price")
    assert first == second
    assert abs(sum(v * v for v in first) ** 0.5 - 1.0) < 1e-9
