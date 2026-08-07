"""Chunking, via LangChain text splitters and configurable from .env (FR-038).

**Why chunking is a real decision, not a formatting detail.** A chunk is the
unit of retrieval *and* the unit of citation. Too large and the model is handed
three unrelated policies and cites whichever it likes; too small and a rule gets
severed from the condition it applies under, so the retrieved fragment is true
but useless. The right size depends on the corpus, which is why it belongs in
configuration rather than in a constant somebody has to recompile to change.

`RecursiveCharacterTextSplitter` is the default because it tries separators in
priority order — paragraph, then line, then sentence, then word — and only
falls back to cutting mid-word when nothing else fits. A citation cut mid-
sentence is not evidence a human can check.

Strategies (CHUNK_STRATEGY):
  recursive  — priority-ordered separators. The sensible default.
  character  — single separator. Predictable, blunt.
  token      — sized by model tokens, not characters. Use when the embedding
               model's context window is the binding constraint.
  markdown   — splits on heading structure, keeping a section with its heading.
"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document

from pricing.config import get_settings
from pricing.core.logging import get_logger

logger = get_logger("pricing.rag.splitter")


def build_splitter(
    strategy: str | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> Any:
    """Construct the configured splitter. Arguments override config for tests."""
    s = get_settings()
    strategy = (strategy or s.chunk_strategy).lower()
    size = chunk_size if chunk_size is not None else s.chunk_size
    overlap = chunk_overlap if chunk_overlap is not None else s.chunk_overlap

    if strategy == "token":
        # Token splitting needs a tiktoken encoding, which is downloaded on
        # first use. Behind a proxy that download can fail — and a chunking
        # strategy is not worth failing an ingest over, so fall back to the
        # recursive splitter and say why.
        try:
            from langchain_text_splitters import TokenTextSplitter

            splitter = TokenTextSplitter(chunk_size=size, chunk_overlap=overlap)
            splitter.split_text("warmup")
            return splitter
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "splitter.token_unavailable",
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
                fallback="recursive",
                detail="tiktoken could not load its encoding; check TLS/egress.",
            )
            strategy = "recursive"

    if strategy == "character":
        from langchain_text_splitters import CharacterTextSplitter

        separators = s.chunk_separator_list
        return CharacterTextSplitter(
            separator=separators[0] if separators else "\n\n",
            chunk_size=size,
            chunk_overlap=overlap,
        )

    if strategy == "markdown":
        from langchain_text_splitters import MarkdownTextSplitter

        return MarkdownTextSplitter(chunk_size=size, chunk_overlap=overlap)

    from langchain_text_splitters import RecursiveCharacterTextSplitter

    return RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=s.chunk_separator_list,
        keep_separator=True,
    )


def split_documents(documents: list[Document]) -> list[Document]:
    """Chunk pre-loaded documents, preserving their metadata.

    Metadata carries the page number for a PDF and the row for a CSV, which is
    what makes a citation checkable — "policy.pdf p.4" rather than "policy.pdf".
    """
    if not documents:
        return []
    splitter = build_splitter()
    try:
        chunks = splitter.split_documents(documents)
    except Exception as exc:  # noqa: BLE001
        logger.warning("splitter.failed", error=f"{type(exc).__name__}: {exc}")
        return documents
    return [c for c in chunks if c.page_content.strip()]


def split_text(text: str, metadata: dict | None = None) -> list[Document]:
    """Chunk a raw string into Documents."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    return split_documents([Document(page_content=cleaned, metadata=metadata or {})])


def describe() -> dict:
    """Current chunking configuration, for the RAG panel."""
    s = get_settings()
    return {
        "strategy": s.chunk_strategy,
        "chunk_size": s.chunk_size,
        "chunk_overlap": s.chunk_overlap,
        "separators": [
            sep.replace("\n", "\\n").replace("\t", "\\t") or "(empty)"
            for sep in s.chunk_separator_list
        ],
    }
