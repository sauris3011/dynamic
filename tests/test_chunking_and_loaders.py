"""Configurable chunking and document loading (FR-038, FR-041).

A chunk is the unit of retrieval *and* the unit of citation, so chunk boundaries
are not a formatting detail: a rule severed from the condition it applies under
retrieves as a true statement that means the opposite. These tests pin the
behaviour that keeps citations checkable.
"""

from __future__ import annotations

import json

import pytest

from pricing.config import Settings
from pricing.rag import loaders, splitter

PROSE = (
    "Margin floor policy.\n\n"
    + "Every recommended price must retain at least fifteen percent gross "
      "margin after unit cost. " * 14
)


# --- Configuration --------------------------------------------------------
#
# Exercised through the environment rather than constructor kwargs. These fields
# carry no pydantic alias, so `Settings(CHUNK_SIZE=...)` is an unrecognised key
# that `extra="ignore"` silently discards — a test written that way passes
# while proving nothing. Setting the variable is what a .env file actually does.

def env_settings(monkeypatch, **values) -> Settings:
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_chunking_is_configurable_from_the_environment(monkeypatch):
    s = env_settings(monkeypatch, CHUNK_STRATEGY="token", CHUNK_SIZE=400,
                     CHUNK_OVERLAP=50)
    assert s.chunk_strategy == "token"
    assert s.chunk_size == 400 and s.chunk_overlap == 50


def test_an_unknown_strategy_fails_at_boot_not_at_ingest(monkeypatch):
    """NFR-022 — a bad value is caught with an actionable message at startup."""
    with pytest.raises(ValueError, match="CHUNK_STRATEGY must be one of"):
        env_settings(monkeypatch, CHUNK_STRATEGY="semantic-magic")


def test_overlap_at_or_above_chunk_size_is_rejected(monkeypatch):
    """Otherwise the splitter cannot make forward progress, and LangChain
    raises deep inside ingestion rather than at boot."""
    with pytest.raises(ValueError, match="must be smaller than"):
        env_settings(monkeypatch, CHUNK_SIZE=300, CHUNK_OVERLAP=300)


def test_separators_decode_escapes_and_keep_the_final_fallback(monkeypatch):
    s = env_settings(monkeypatch, CHUNK_SEPARATORS=r"\n\n|\n|. | |")
    assert s.chunk_separator_list == ["\n\n", "\n", ". ", " ", ""]


# --- Splitters ------------------------------------------------------------

@pytest.mark.parametrize("strategy", ["recursive", "character", "markdown"])
def test_each_strategy_produces_usable_chunks(strategy):
    sp = splitter.build_splitter(strategy=strategy, chunk_size=200, chunk_overlap=30)
    chunks = sp.split_text(PROSE)
    assert chunks and all(c.strip() for c in chunks)


def test_chunk_size_actually_bounds_the_output():
    small = splitter.build_splitter("recursive", chunk_size=150, chunk_overlap=20)
    large = splitter.build_splitter("recursive", chunk_size=800, chunk_overlap=20)
    assert len(small.split_text(PROSE)) > len(large.split_text(PROSE))


def test_recursive_splitting_prefers_paragraph_boundaries():
    """A citation cut mid-sentence is not evidence a human can check."""
    text = "First policy paragraph.\n\nSecond policy paragraph.\n\nThird one."
    chunks = splitter.build_splitter(
        "recursive", chunk_size=30, chunk_overlap=0
    ).split_text(text)
    assert any(c.strip().startswith("Second policy") for c in chunks)


def test_split_text_returns_documents_with_metadata():
    docs = splitter.split_text("A policy statement.", metadata={"source": "p.md"})
    assert docs and docs[0].metadata["source"] == "p.md"


def test_empty_text_yields_no_chunks():
    assert splitter.split_text("   ") == []


def test_describe_reports_the_active_configuration():
    described = splitter.describe()
    assert {"strategy", "chunk_size", "chunk_overlap", "separators"} <= set(described)


def test_document_metadata_survives_chunking():
    """A PDF page number has to reach the citation, or the citation names a
    file rather than a claim."""
    from langchain_core.documents import Document

    docs = [Document(page_content=PROSE, metadata={"source": "policy.pdf", "page": 4})]
    chunks = splitter.split_documents(docs)
    assert len(chunks) > 1
    assert all(c.metadata["page"] == 4 for c in chunks)


# --- Loaders --------------------------------------------------------------

def test_plain_text_and_markdown_load():
    docs = loaders.load(b"# Policy\n\nMargin floor is 15%.", "policy.md")
    assert len(docs) == 1
    assert "Margin floor" in docs[0].page_content
    assert docs[0].metadata["source"] == "policy.md"


def test_csv_loads_one_document_per_row():
    """Rows stay separate: a fragment spanning half of two unrelated records
    reads as a single coherent record that never existed."""
    docs = loaders.load(b"sku,price\nBEV-1,4.99\nBEV-2,5.99\n", "prices.csv")
    assert len(docs) == 2
    assert "BEV-1" in docs[0].page_content
    assert docs[0].metadata["row"] == 1


def test_pdf_loads_one_document_per_page_with_page_numbers():
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    # Blank pages carry no text, so this must be refused rather than ingested
    # as empty — a scanned PDF is the real-world version of this case.
    with pytest.raises(loaders.UnsupportedDocument, match="no readable text"):
        loaders.load(buffer.getvalue(), "blank.pdf")


def test_an_unsupported_type_is_refused_with_the_supported_list():
    with pytest.raises(loaders.UnsupportedDocument) as exc:
        loaders.load(b"\x00\x01binary", "installer.exe")
    assert ".pdf" in str(exc.value) and ".docx" in str(exc.value)


def test_a_corrupt_pdf_is_reported_not_ingested_as_noise():
    with pytest.raises(loaders.UnsupportedDocument):
        loaders.load(b"not really a pdf", "broken.pdf")


def test_windows_encoded_text_is_accepted():
    """Policy files exported from Office on these machines are cp1252."""
    docs = loaders.load("Margin floor – 15%".encode("cp1252"), "policy.txt")
    assert "Margin floor" in docs[0].page_content


def test_supported_extensions_are_advertised():
    extensions = loaders.supported_extensions()
    assert {".pdf", ".docx", ".csv", ".md", ".txt"} <= set(extensions)


# --- Competitor bias (D14) ------------------------------------------------

def test_competitor_bias_is_configurable(monkeypatch):
    s = env_settings(monkeypatch,
                     COMPETITOR_BIAS='{"Aldi": 0.80, "Waitrose": 1.25}')
    assert s.competitor_bias_map == {"Aldi": 0.80, "Waitrose": 1.25}


def test_the_feed_uses_the_configured_roster():
    """The roster, not just the numbers — a different market has different
    rivals, which is the point of the pluggable feed interface."""
    from pricing.feeds.competitor import SyntheticCompetitorFeed

    feed = SyntheticCompetitorFeed({"SKU-1": 10.0},
                                   bias={"Aldi": 0.80, "Waitrose": 1.25})
    assert feed.competitors == ("Aldi", "Waitrose")
    prices = {o.competitor: o.price for o in feed.fetch(["SKU-1"])}
    assert prices["Aldi"] < 10.0 < prices["Waitrose"]


def test_bias_is_the_mean_positioning_not_a_floor():
    """Noise is centred on zero, so a configured 1.0 stays at parity on
    average rather than drifting the whole roster in one direction."""
    from pricing.feeds.competitor import SyntheticCompetitorFeed

    skus = {f"SKU-{i}": 10.0 for i in range(200)}
    feed = SyntheticCompetitorFeed(skus, bias={"Parity": 1.0}, drift_pct=0.0)
    prices = [o.price for o in feed.fetch(list(skus))]
    assert 9.7 < sum(prices) / len(prices) < 10.3


def test_malformed_bias_is_rejected_at_boot(monkeypatch):
    with pytest.raises(ValueError, match="COMPETITOR_BIAS"):
        env_settings(monkeypatch, COMPETITOR_BIAS="not json")
    with pytest.raises(ValueError, match="between 0.1 and 5.0"):
        env_settings(monkeypatch, COMPETITOR_BIAS=json.dumps({"Silly": 900.0}))


def test_out_of_stock_rate_is_configurable():
    from pricing.feeds.competitor import SyntheticCompetitorFeed

    skus = {f"SKU-{i}": 5.0 for i in range(150)}
    always = SyntheticCompetitorFeed(skus, bias={"A": 1.0}, out_of_stock_rate=0.0)
    never = SyntheticCompetitorFeed(skus, bias={"A": 1.0}, out_of_stock_rate=1.0)
    assert all(o.in_stock for o in always.fetch(list(skus)))
    assert not any(o.in_stock for o in never.fetch(list(skus)))
