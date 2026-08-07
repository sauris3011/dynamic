"""Embedding strategy selection, strongest first.

**Why there is a chain at all.** ChromaDB's default embedding function downloads
an ONNX MiniLM model on first use. In this environment that download fails with
CERTIFICATE_VERIFY_FAILED, because the corporate proxy intercepts TLS and the
downloader does not consult our CA configuration. PRD assumption A-03
anticipated exactly this; it is confirmed rather than hypothetical.

Three strategies, tried in order:

1. **Gateway embeddings (preferred).** The model alias comes from
   `MODEL_EMBEDDING` and is never hardcoded (D4). This is the strongest option
   available here: the gateway already holds the credentials and already
   terminates TLS the way the corporate proxy expects, so it works in the exact
   environment where the local download does not.
2. **Local MiniLM.** Real semantic embeddings with no network at query time.
   Used when no gateway alias is configured but the model can be loaded — on a
   machine with unrestricted egress, or once CA_BUNDLE_PATH is set.
3. **Hashed character n-grams.** Zero download, zero network, deterministic.
   This is *lexical* retrieval, not semantic: it matches shared word and
   character patterns, so "margin floor" retrieves the margin policy but a
   paraphrase using entirely different vocabulary may not.

Each fallback is weaker than the one above it, and the code says so. Keeping the
grounding pipeline functional in a locked-down environment beats a RAG panel
that cannot ingest anything — but `build()` reports which function is live, and
the UI displays it, so nobody mistakes lexical matching for semantic search.

**Dimensions are not interchangeable.** A Chroma collection is fixed to the
width of the vectors first written into it, so changing strategy invalidates
existing collections. `build()` therefore returns the dimensionality and
`store.py` compares it against what the collections were built with, rather than
letting the mismatch surface as an opaque error on someone's first query.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from pricing.config import get_settings
from pricing.core.logging import get_logger

logger = get_logger("pricing.rag.embeddings")

DIMENSIONS = 384          # matches MiniLM, so collections stay interchangeable
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    """Word tokens plus character 4-grams.

    Character n-grams give partial robustness to morphology and typos that pure
    word matching lacks — "pricing" and "prices" share several.
    """
    lowered = text.lower()
    words = _TOKEN_RE.findall(lowered)
    grams = [lowered[i:i + 4] for i in range(0, max(0, len(lowered) - 3), 2)]
    return words + grams


def _hash_bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    index = int.from_bytes(digest[:4], "big") % DIMENSIONS
    # Signed hashing reduces collision bias — colliding tokens partially cancel
    # rather than always reinforcing.
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def hashed_embedding(text: str) -> list[float]:
    """Deterministic L2-normalised hashed n-gram vector. No network, no model."""
    vector = [0.0] * DIMENSIONS
    tokens = _tokens(text)
    if not tokens:
        return vector

    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1

    for token, count in counts.items():
        index, sign = _hash_bucket(token)
        # Sub-linear term weighting, as in TF-IDF: a word appearing 20 times is
        # not 20x more informative than one appearing once.
        vector[index] += sign * (1.0 + math.log(count))

    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


class HashingEmbeddingFunction:
    """Chroma-compatible embedding function requiring no download.

    Chroma's interface has shifted across versions: older releases call the
    object directly, newer ones call `embed_documents` on ingest and
    `embed_query` on retrieval. All three are implemented so the fallback works
    regardless of which chromadb version is installed — a mismatch here fails at
    query time only, which is exactly when it is least convenient to discover.
    """

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self._embed(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self._embed(input)

    def embed_query(self, input: list[str] | str) -> list[list[float]]:  # noqa: A002
        # Chroma passes a list here, but accept a bare string defensively.
        if isinstance(input, str):
            return self._embed([input])
        return self._embed(input)

    @staticmethod
    def _embed(texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(text) for text in texts]

    @staticmethod
    def name() -> str:
        return "hashed-ngram-384"

    def is_legacy(self) -> bool:
        return False


_active: str = "uninitialised"
_degraded_reason: str = ""


def build() -> tuple[Any, str, int]:
    """Return (embedding_function, description, dimensions).

    Each strategy is attempted in turn and any failure — an unset alias, a
    blocked download, a TLS error, a gateway 400 — falls through to the next.
    The warmup inside each builder is what forces the failure to surface here,
    at startup, instead of inside a user's first upload.
    """
    global _active, _degraded_reason
    settings = get_settings()
    reasons: list[str] = []

    # --- 1. Gateway (preferred) ------------------------------------------
    if settings.gateway_embeddings_enabled:
        try:
            from pricing.rag import gateway_embeddings

            fn, description, dimensions = gateway_embeddings.build()
            _active, _degraded_reason = "gateway", ""
            return fn, description, dimensions
        except Exception as exc:  # noqa: BLE001
            reasons.append(f"gateway: {type(exc).__name__}: {str(exc)[:160]}")
            logger.warning(
                "embeddings.gateway_unavailable",
                model=settings.model_embedding,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
    else:
        reasons.append("gateway: MODEL_EMBEDDING is not set")

    # --- 2. Local MiniLM --------------------------------------------------
    try:
        from chromadb.utils import embedding_functions

        fn = embedding_functions.ONNXMiniLM_L6_V2()
        probe = fn(["warmup"])
        _active, _degraded_reason = "minilm", "; ".join(reasons)
        logger.info("embeddings.minilm_ready")
        return fn, "all-MiniLM-L6-v2 (semantic, local ONNX)", len(probe[0])
    except Exception as exc:  # noqa: BLE001
        reasons.append(f"minilm: {type(exc).__name__}: {str(exc)[:160]}")
        logger.warning(
            "embeddings.minilm_unavailable",
            error=f"{type(exc).__name__}: {str(exc)[:200]}",
            fallback="hashed-ngram-384",
        )

    # --- 3. Hashed n-grams (always available) -----------------------------
    _active = "hashed"
    _degraded_reason = "; ".join(reasons)
    return (
        HashingEmbeddingFunction(),
        f"hashed n-gram {DIMENSIONS}d (LEXICAL fallback — no semantic model available)",
        DIMENSIONS,
    )


def active() -> str:
    return _active


def degraded_reason() -> str:
    """Why a stronger strategy was not used. Empty when the best one is live."""
    return _degraded_reason
