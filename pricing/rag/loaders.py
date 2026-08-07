"""Document loading for RAG uploads (FR-041).

Produces `langchain_core.documents.Document` objects, so loading composes
directly with the splitters in `splitter.py` and with any LangChain retriever
later. The Document abstraction is the useful part of LangChain here.

**Why not `langchain_community.document_loaders`.** That package is the obvious
home for these loaders, and it is being sunset — importing it emits a
deprecation warning pointing at standalone replacements that do not all exist
yet. Taking a dependency on an unmaintained package to save roughly eighty lines
is a bad trade, especially behind a corporate proxy where every extra transitive
dependency is another download that can fail. The loaders below use `pypdf` and
`docx2txt` directly and return the same type.

**Metadata is the point.** A PDF is loaded page by page so a citation can say
"policy.pdf p.4" rather than "policy.pdf" — a reviewer has to be able to check
the claim, and a whole-document citation is not checkable.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from langchain_core.documents import Document

from pricing.core.logging import get_logger

logger = get_logger("pricing.rag.loaders")

# Extensions we can turn into text. Anything else is refused with a clear
# message rather than ingested as mojibake.
SUPPORTED = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".pdf", ".docx"}

TEXT_LIKE = {".txt", ".md", ".markdown", ".json", ".log"}


class UnsupportedDocument(ValueError):
    """The file type cannot be loaded. Carries a message for the operator."""


def supported_extensions() -> list[str]:
    return sorted(SUPPORTED)


def load(raw: bytes, filename: str) -> list[Document]:
    """Turn uploaded bytes into Documents. Raises UnsupportedDocument."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise UnsupportedDocument(
            f"'{filename}' has an unsupported type. Supported: "
            f"{', '.join(supported_extensions())}."
        )

    if suffix == ".pdf":
        documents = _load_pdf(raw, filename)
    elif suffix == ".docx":
        documents = _load_docx(raw, filename)
    elif suffix == ".csv":
        documents = _load_csv(raw, filename)
    else:
        documents = _load_text(raw, filename)

    kept = [d for d in documents if d.page_content.strip()]
    if not kept:
        raise UnsupportedDocument(
            f"'{filename}' produced no readable text. A scanned PDF holds "
            "images rather than text and needs OCR, which is out of scope here."
        )
    logger.info("loaders.loaded", source=filename, kind=suffix, parts=len(kept))
    return kept


def _decode(raw: bytes, filename: str) -> str:
    """UTF-8 first, then the common Windows fallback.

    A hard failure on a cp1252 document would be needlessly strict: these are
    policy files exported from Office on the same machines this runs on.
    """
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocument(
        f"'{filename}' is not readable as text in UTF-8 or Windows-1252."
    )


def _load_text(raw: bytes, filename: str) -> list[Document]:
    return [
        Document(
            page_content=_decode(raw, filename),
            metadata={"source": filename, "loader": "text"},
        )
    ]


def _load_pdf(raw: bytes, filename: str) -> list[Document]:
    """One Document per page, so page numbers survive into citations."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise UnsupportedDocument(
            "PDF support needs `pypdf`. Install it, or upload the text instead."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedDocument(f"'{filename}' is not a readable PDF: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as exc:  # noqa: BLE001
            raise UnsupportedDocument(
                f"'{filename}' is password-protected."
            ) from exc

    documents = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("loaders.pdf_page_failed", source=filename,
                           page=number, error=str(exc))
            continue
        documents.append(
            Document(
                page_content=text,
                metadata={"source": filename, "page": number,
                          "pages": len(reader.pages), "loader": "pypdf"},
            )
        )
    return documents


def _load_docx(raw: bytes, filename: str) -> list[Document]:
    try:
        import docx2txt
    except ImportError as exc:
        raise UnsupportedDocument(
            "DOCX support needs `docx2txt`. Install it, or upload the text."
        ) from exc

    import tempfile

    # docx2txt takes a path, not bytes. The temp file is removed immediately.
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as handle:
        handle.write(raw)
        path = handle.name
    try:
        text = docx2txt.process(path) or ""
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedDocument(
            f"'{filename}' could not be read as a Word document: {exc}"
        ) from exc
    finally:
        Path(path).unlink(missing_ok=True)

    return [Document(page_content=text,
                     metadata={"source": filename, "loader": "docx2txt"})]


def _load_csv(raw: bytes, filename: str) -> list[Document]:
    """One Document per row, rendered as `column: value` lines.

    Rows are kept separate rather than concatenated because a retrieved CSV
    fragment spanning half of two unrelated records is worse than useless — it
    reads as a single coherent record that never existed.
    """
    text = _decode(raw, filename)
    reader = csv.DictReader(io.StringIO(text))
    documents = []
    for index, row in enumerate(reader, start=1):
        rendered = "\n".join(
            f"{key}: {value}" for key, value in row.items()
            if key and value not in (None, "")
        )
        if rendered:
            documents.append(
                Document(
                    page_content=rendered,
                    metadata={"source": filename, "row": index, "loader": "csv"},
                )
            )
    if not documents:
        # Not a keyed CSV — fall back to treating it as plain text.
        return _load_text(raw, filename)
    return documents
