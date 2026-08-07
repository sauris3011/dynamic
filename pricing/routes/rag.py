"""Universal RAG grounding panel (FR-038 .. FR-044, FR-068)."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from pricing.core.logging import get_logger
from pricing.db.app_db import audit, session
from pricing.llm import registry
from pricing.rag import loaders, store

logger = get_logger("pricing.routes.rag")
router = APIRouter(prefix="/api/rag", tags=["rag"])

MAX_UPLOAD_BYTES = 4 * 1024 * 1024


class TextIngestRequest(BaseModel):
    collection: str = Field(pattern="^(pricing_policy|market_intel|product_kb|user_uploads)$")
    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=10)
    actor: str = Field("operator", max_length=80)


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    collections: list[str] | None = None
    k: int = Field(3, ge=1, le=10)


@router.get("/stats")
def rag_stats() -> dict:
    """Embedding statistics for the grounding panel (FR-039)."""
    stats = store.stats()
    probe = registry.probe_gateway()
    stats["gateway"] = {
        "reachable": probe.reachable,
        "usable": probe.usable,
        "summary": probe.summary(),
        "configured": probe.configured,
        "missing": probe.missing,
    }
    return stats


@router.post("/ingest/text")
def ingest_text(payload: TextIngestRequest) -> dict:
    """Add a document by pasting text (FR-041)."""
    if not store.available():
        raise HTTPException(503, "Vector store unavailable — is chromadb installed?")
    result = store.ingest(payload.collection, payload.source, payload.text)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    with session() as conn:
        audit(conn, actor=payload.actor, event_type="rag_ingest",
              entity_type="collection", entity_id=payload.collection,
              source=payload.source, chunks=result["ingested"])
    return result


@router.post("/ingest/file")
async def ingest_file(
    file: UploadFile = File(...),
    collection: str = Form("user_uploads"),
    actor: str = Form("operator"),
) -> dict:
    """Upload and embed a document at runtime, no restart (FR-041).

    PDF and DOCX are loaded structurally rather than decoded as text, so a
    citation can name the page a claim came from.
    """
    if not store.available():
        raise HTTPException(503, "Vector store unavailable — is chromadb installed?")
    if collection not in store.COLLECTIONS:
        raise HTTPException(422, f"Unknown collection '{collection}'")

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_BYTES // 1024 // 1024}MB.")

    filename = file.filename or "upload.txt"
    try:
        documents = loaders.load(raw, filename)
    except loaders.UnsupportedDocument as exc:
        raise HTTPException(415, str(exc)) from exc

    result = store.ingest_documents(collection, filename, documents)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    with session() as conn:
        audit(conn, actor=actor, event_type="rag_upload",
              entity_type="collection", entity_id=collection,
              source=filename, chunks=result["ingested"],
              parts=len(documents))
    return {**result, "parts_loaded": len(documents)}


@router.delete("/collections")
def reset_collections(actor: str = "operator") -> dict:
    """Drop and re-create every collection under the current embedding function.

    Needed when MODEL_EMBEDDING changes: vectors from two different models are
    not comparable even at equal width, and at differing widths the collection
    cannot be queried at all. Deliberately explicit rather than automatic — a
    config typo must not be able to destroy an ingested corpus on its own.
    """
    if not store.available():
        raise HTTPException(503, "Vector store unavailable — is chromadb installed?")
    result = store.reset_collections()
    if result.get("error"):
        raise HTTPException(503, result["error"])
    with session() as conn:
        audit(conn, actor=actor, event_type="rag_reset",
              entity_type="collection", entity_id="all",
              dropped=result["reset"], embedding_model=result["embedding_model"])
    logger.warning("rag.reset", actor=actor, dropped=result["reset"])
    return result


@router.post("/retrieve")
def retrieve(payload: RetrieveRequest) -> dict:
    """Inspect what grounding a query would pull. Useful for demonstrating that
    citations are real rather than decorative."""
    chunks = store.retrieve(payload.query, payload.collections, payload.k)
    body = {
        "query": payload.query,
        "results": [
            {**c.to_citation(), "distance": round(c.distance, 4)} for c in chunks
        ],
    }
    if not chunks:
        # Retrieval degrades to empty rather than raising, so an embedding
        # mismatch would otherwise present as "nothing matched" — indistinguishable
        # from an empty corpus, and far more misleading.
        mismatch = store.embedding_mismatch()
        if mismatch:
            body["warning"] = mismatch["detail"]
    return body
