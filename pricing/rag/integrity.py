"""Embedding/collection compatibility (D-11).

A Chroma collection is fixed to the width of the first vectors written into it.
Changing MODEL_EMBEDDING from a 384-dimension model to a 3072-dimension one does
not "upgrade" the collections — it breaks them, and the failure surfaces as an
opaque `expecting embedding with dimension of 384, got 3072` on someone's next
upload.

Two distinct problems live here, and the second is the nastier one:

* **Different width** — ingest and retrieval fail outright. Detected by reading
  a stored vector, so it works even for collections written before the
  signature file existed, which is precisely when a mismatch is least expected.
* **Same width, different model** — everything *appears* to work while queries
  run against a vector space they no longer live in. Only the signature file
  can catch this, and without it the symptom is "retrieval got worse" with no
  cause to point at.

Nothing is deleted automatically. Re-embedding is the operator's call, because
the alternative is a config typo silently destroying an ingested corpus.
"""

from __future__ import annotations

from typing import Callable

from pricing.config import get_settings
from pricing.core.logging import get_logger

logger = get_logger("pricing.rag.integrity")

SIGNATURE_FILE = ".embedding_signature"


def signature_path():
    return get_settings().chroma_path / SIGNATURE_FILE


def current_signature(embedding_fn: Callable[[], tuple]) -> str:
    from pricing.rag import embeddings

    _, description, dimensions = embedding_fn()
    return f"{embeddings.active()}:{dimensions}:{description}"


def record_signature(embedding_fn: Callable[[], tuple]) -> None:
    try:
        path = signature_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(current_signature(embedding_fn), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag.signature_write_failed", error=str(exc))


def stored_dimensions(client, collections, embedding_fn) -> int | None:
    """Actual width of the vectors already in the store, or None if empty."""
    if client is None:
        return None
    fn, _, _ = embedding_fn()
    for name in collections:
        try:
            # Fetched with the live embedding function attached, which is safe
            # because reading stored vectors never embeds anything.
            coll = client.get_or_create_collection(name=name, embedding_function=fn)
            if coll.count() == 0:
                continue
            sample = coll.get(limit=1, include=["embeddings"])
            vectors = sample.get("embeddings")
            if vectors is not None and len(vectors) > 0 and vectors[0] is not None:
                return len(vectors[0])
        except Exception:  # noqa: BLE001 - try the next collection
            continue
    return None


def check(client, collections, embedding_fn) -> dict | None:
    """Report an embedding/collection mismatch, or None when consistent."""
    try:
        _, description, current_dims = embedding_fn()
        existing = stored_dimensions(client, collections, embedding_fn)

        if existing is not None and existing != current_dims:
            return {
                "stored": f"{existing}d vectors already in the store",
                "current": description,
                "compatible": False,
                "detail": (
                    f"Collections hold {existing}-dimension vectors but the "
                    f"configured embedding function produces {current_dims}. "
                    "Ingest and retrieval against them will fail. Re-embed with "
                    "DELETE /api/rag/collections, then re-upload."
                ),
            }

        path = signature_path()
        current = current_signature(embedding_fn)
        if existing is None or not path.exists():
            return None
        stored = path.read_text(encoding="utf-8").strip()
        if stored == current:
            return None
        return {
            "stored": stored,
            "current": current,
            "compatible": True,
            "detail": (
                f"Collections were embedded with '{stored}' but the configured "
                f"function is '{current}'. The widths match so queries will run, "
                "but the two vector spaces are not comparable and retrieval "
                "quality will be poor. Re-embed with DELETE "
                "/api/rag/collections, then re-upload."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag.signature_check_failed", error=str(exc))
        return None
