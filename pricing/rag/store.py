"""ChromaDB vector store — embedded, in-process, no server (D6, NFR-004).

Chroma's default embedding function is an ONNX MiniLM that runs locally, so
retrieval costs nothing at the gateway and works with no network at all. This is
a deliberate substitution for `sentence-transformers` from decision D5: same
model family, ~80MB instead of a multi-gigabyte torch install, which matters
behind a corporate proxy. Recorded in docs/DEVIATIONS.md.

Failure policy: if Chroma cannot start, retrieval degrades to empty rather than
raising. A pricing run must not fail because a document store is unavailable —
but the recommendation then carries no citations, which the band logic already
treats as lower confidence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.rag import integrity

logger = get_logger("pricing.rag.store")

COLLECTIONS = {
    "pricing_policy": "MAP agreements, pricing policy, brand guidelines",
    "market_intel": "Market trend reports and competitor intelligence",
    "product_kb": "SKU descriptions and product attributes",
    "user_uploads": "Documents added at runtime through the UI",
}

# Chunking lives in rag/splitter.py and is configured from .env
# (CHUNK_STRATEGY, CHUNK_SIZE, CHUNK_OVERLAP, CHUNK_SEPARATORS).


@dataclass
class RetrievedChunk:
    text: str
    source: str
    collection: str
    chunk_index: int
    distance: float
    # Set by the loaders where the format has structure: a PDF page, a CSV row.
    locator: str = ""

    @property
    def citation_id(self) -> str:
        """A citation a reviewer can actually check.

        "pricing_policy:policy.pdf p.4" can be verified by opening page four.
        "pricing_policy:policy.pdf#7" names an internal chunk index that means
        nothing outside this process — which is why the loaders carry page and
        row numbers through to here.
        """
        if self.locator:
            return f"{self.collection}:{self.source} {self.locator}"
        return f"{self.collection}:{self.source}#{self.chunk_index}"

    def to_citation(self) -> dict:
        return {
            "id": self.citation_id, "source": self.source,
            "collection": self.collection, "excerpt": self.text[:220],
            "locator": self.locator or None,
        }


@lru_cache(maxsize=1)
def _client() -> Any | None:
    try:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        settings = get_settings()
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(
            path=str(settings.chroma_path),
            settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag.unavailable", error=f"{type(exc).__name__}: {exc}")
        return None


def available() -> bool:
    return _client() is not None


@lru_cache(maxsize=1)
def _embedding_fn() -> tuple[Any, str, int]:
    """Resolved once per process: gateway, then local MiniLM, then hashed
    n-grams (see pricing/rag/embeddings.py). Returns (fn, description, dims)."""
    from pricing.rag import embeddings

    return embeddings.build()


SIGNATURE_FILE = integrity.SIGNATURE_FILE


def _signature_path():
    return integrity.signature_path()


def _current_signature() -> str:
    return integrity.current_signature(_embedding_fn)


def embedding_mismatch() -> dict | None:
    """Were the stored collections built by a different embedding function?

    Delegates to rag/integrity.py, which owns the detection rules.
    """
    return integrity.check(_client(), COLLECTIONS, _embedding_fn)


def _record_signature() -> None:
    integrity.record_signature(_embedding_fn)


def _collection(name: str):
    client = _client()
    if client is None:
        return None
    fn, _, _ = _embedding_fn()
    try:
        return client.get_or_create_collection(
            name=name,
            metadata={"description": COLLECTIONS.get(name, name)},
            embedding_function=fn,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag.collection_failed", collection=name, error=str(exc))
        return None


def reset_collections() -> dict:
    """Drop and recreate every collection under the current embedding function.

    Required when the embedding model changes: vectors from two different models
    are not comparable even at equal width, so keeping the old ones would mean
    retrieving against a space the query no longer lives in.

    Only derived data is destroyed. The source documents are the operator's own
    files and are re-uploadable; nothing in `app.db` or `commerce.db` is touched.
    """
    client = _client()
    if client is None:
        return {"reset": [], "error": "Vector store unavailable."}

    dropped: list[str] = []
    for name in COLLECTIONS:
        try:
            client.delete_collection(name)
            dropped.append(name)
        except Exception:  # noqa: BLE001 - absent collection is not an error
            continue

    _record_signature()
    _, description, dimensions = _embedding_fn()
    logger.warning("rag.collections_reset", dropped=dropped, model=description)
    return {
        "reset": dropped,
        "embedding_model": description,
        "dimensions": dimensions,
        "detail": (
            f"{len(dropped)} collection(s) dropped and re-created for "
            f"{description}. Re-upload documents to restore grounding."
        ),
    }


def chunk_text(text: str) -> list[str]:
    """Chunk a string with the configured splitter (FR-038).

    Thin wrapper over `rag/splitter.py`, kept because callers and tests want
    strings rather than Documents.
    """
    from pricing.rag import splitter

    return [d.page_content for d in splitter.split_text(text)]


def ingest(
    collection: str, source: str, text: str, metadata: dict | None = None
) -> dict:
    """Chunk, embed, and store raw text (FR-038, FR-041)."""
    from langchain_core.documents import Document

    return ingest_documents(
        collection,
        source,
        [Document(page_content=text, metadata=metadata or {})],
    )


def ingest_documents(
    collection: str, source: str, documents: list, metadata: dict | None = None
) -> dict:
    """Chunk, embed, and store pre-loaded Documents (FR-038, FR-041).

    Loader metadata — a PDF page number, a CSV row — is carried onto every chunk
    so a citation can name the page rather than only the file. A citation a
    reviewer cannot check is not evidence.
    """
    from pricing.rag import splitter

    coll = _collection(collection)
    if coll is None:
        return {"ingested": 0, "error": "Vector store unavailable."}

    chunks = splitter.split_documents(documents)
    if not chunks:
        return {"ingested": 0, "error": "Document is empty."}

    digest = hashlib.sha256(source.encode()).hexdigest()[:10]
    ids = [f"{digest}-{i}" for i in range(len(chunks))]
    texts = [c.page_content for c in chunks]
    metadatas = []
    for index, chunk in enumerate(chunks):
        entry = {
            "source": source,
            "collection": collection,
            "chunk_index": index,
            **(metadata or {}),
        }
        # Chroma metadata values must be scalars; anything else is dropped
        # rather than failing the whole ingest.
        for key, value in (chunk.metadata or {}).items():
            if key not in entry and isinstance(value, (str, int, float, bool)):
                entry[key] = value
        metadatas.append(entry)

    try:
        coll.upsert(ids=ids, documents=texts, metadatas=metadatas)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag.ingest_failed", source=source, error=str(exc))
        mismatch = embedding_mismatch()
        return {
            "ingested": 0,
            "error": (
                f"{exc}. {mismatch['detail']}" if mismatch else str(exc)
            ),
        }

    # Written after a successful write, so the signature always describes what
    # is actually in the collections rather than what was merely attempted.
    _record_signature()
    logger.info("rag.ingested", collection=collection, source=source,
                chunks=len(chunks), strategy=splitter.describe()["strategy"])
    return {
        "ingested": len(chunks),
        "collection": collection,
        "source": source,
        "chunking": splitter.describe(),
    }


def _locator(meta: dict) -> str:
    """Human-checkable position within the source document, if the loader knew
    one. Page for a PDF, row for a CSV; empty for undifferentiated text."""
    if meta.get("page"):
        return f"p.{meta['page']}"
    if meta.get("row"):
        return f"row {meta['row']}"
    return ""


def retrieve(query: str, collections: list[str] | None = None, k: int = 3) -> list[RetrievedChunk]:
    """Retrieve grounding context.

    `k` is per-collection and intentionally small: PRD 4.4 tunes retrieval
    breadth per role rather than injecting at full width on every call, which is
    the documented softening of the "every call" requirement in favour of the
    token-efficiency goal stated in the same source document.
    """
    if not query.strip() or k <= 0:
        return []
    targets = collections or list(COLLECTIONS)
    out: list[RetrievedChunk] = []

    for name in targets:
        coll = _collection(name)
        if coll is None:
            continue
        try:
            if coll.count() == 0:
                continue
            res = coll.query(query_texts=[query], n_results=min(k, coll.count()))
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag.query_failed", collection=name, error=str(exc))
            continue

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for doc, meta, dist in zip(docs, metas, dists):
            out.append(
                RetrievedChunk(
                    text=doc,
                    source=str((meta or {}).get("source", "unknown")),
                    collection=name,
                    chunk_index=int((meta or {}).get("chunk_index", 0)),
                    distance=float(dist),
                    locator=_locator(meta or {}),
                )
            )

    out.sort(key=lambda c: c.distance)
    return out[: k * 2]


def stats() -> dict:
    """Per-collection counts for the grounding panel (FR-039)."""
    if not available():
        return {"available": False, "collections": {}, "total_chunks": 0,
                "embedding_model": "unavailable"}
    _, model_description, dimensions = _embedding_fn()
    result, total = {}, 0
    for name, description in COLLECTIONS.items():
        coll = _collection(name)
        count = 0
        if coll is not None:
            try:
                count = coll.count()
            except Exception:
                count = 0
        result[name] = {"chunks": count, "description": description}
        total += count
    from pricing.rag import embeddings, loaders, splitter

    return {
        "available": True,
        "collections": result,
        "total_chunks": total,
        "embedding_model": model_description,
        "embedding_strategy": embeddings.active(),
        "dimensions": dimensions,
        "semantic": embeddings.active() in ("gateway", "minilm"),
        "degraded_reason": embeddings.degraded_reason(),
        "mismatch": embedding_mismatch(),
        "chunking": splitter.describe(),
        "supported_uploads": loaders.supported_extensions(),
    }
