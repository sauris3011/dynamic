"""Two-tier LLM cache: exact-hash and semantic (FR-073).

Exact tier is a SHA-256 of the fully composed prompt — cheap, and catches the
common case of an identical run being repeated. The semantic tier catches
near-misses (a prompt differing only in whitespace or a reordered SKU list) by
comparing embeddings.

Semantic hits are only accepted above a similarity threshold, and the threshold
is deliberately high. A wrong cache hit is worse than a cache miss: it silently
returns an answer to a question nobody asked, and the error is invisible in the
output.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from pricing.config import get_settings

SEMANTIC_THRESHOLD = 0.97

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    prompt_hash   TEXT PRIMARY KEY,
    model         TEXT NOT NULL,
    role          TEXT,
    prompt_text   TEXT NOT NULL,
    response_json TEXT NOT NULL,
    embedding     BLOB,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    hit_count     INTEGER NOT NULL DEFAULT 0,
    last_hit_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_cache_model ON llm_cache(model);

-- Running counters so the UI can show hit/miss ratios without scanning
-- the whole cache table (FR-066).
CREATE TABLE IF NOT EXISTS cache_stats (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    exact_hits    INTEGER NOT NULL DEFAULT 0,
    semantic_hits INTEGER NOT NULL DEFAULT 0,
    misses        INTEGER NOT NULL DEFAULT 0,
    tokens_saved  INTEGER NOT NULL DEFAULT 0
);
"""


def _db_path():
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s.cache_db_path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO cache_stats (id) VALUES (1)")


def prompt_hash(model: str, prompt: str) -> str:
    """Model is part of the key — the same prompt to a different model is a
    different question."""
    return hashlib.sha256(f"{model}\x00{prompt}".encode("utf-8")).hexdigest()


def _pack(vec: list[float]) -> bytes:
    return json.dumps([round(float(x), 6) for x in vec]).encode("utf-8")


def _unpack(blob: bytes | None) -> list[float] | None:
    if not blob:
        return None
    try:
        return json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def lookup(
    model: str, prompt: str, embedding: list[float] | None = None
) -> tuple[dict | None, str]:
    """Return (response, hit_kind) where hit_kind is exact|semantic|miss."""
    key = prompt_hash(model, prompt)
    with session() as conn:
        row = conn.execute(
            "SELECT response_json, tokens_in, tokens_out FROM llm_cache "
            "WHERE prompt_hash = ?",
            (key,),
        ).fetchone()
        if row is not None:
            conn.execute(
                "UPDATE llm_cache SET hit_count = hit_count + 1, last_hit_at = ? "
                "WHERE prompt_hash = ?",
                (datetime.now(timezone.utc).isoformat(), key),
            )
            conn.execute(
                "UPDATE cache_stats SET exact_hits = exact_hits + 1, "
                "tokens_saved = tokens_saved + ? WHERE id = 1",
                (row["tokens_in"] + row["tokens_out"],),
            )
            return json.loads(row["response_json"]), "exact"

        if embedding:
            best_row, best_sim = None, 0.0
            for cand in conn.execute(
                "SELECT prompt_hash, response_json, embedding, tokens_in, tokens_out "
                "FROM llm_cache WHERE model = ? AND embedding IS NOT NULL",
                (model,),
            ):
                vec = _unpack(cand["embedding"])
                if vec is None:
                    continue
                sim = _cosine(embedding, vec)
                if sim > best_sim:
                    best_row, best_sim = cand, sim
            if best_row is not None and best_sim >= SEMANTIC_THRESHOLD:
                conn.execute(
                    "UPDATE cache_stats SET semantic_hits = semantic_hits + 1, "
                    "tokens_saved = tokens_saved + ? WHERE id = 1",
                    (best_row["tokens_in"] + best_row["tokens_out"],),
                )
                return json.loads(best_row["response_json"]), "semantic"

        conn.execute("UPDATE cache_stats SET misses = misses + 1 WHERE id = 1")
    return None, "miss"


def store(
    model: str,
    prompt: str,
    response: dict,
    role: str | None = None,
    embedding: list[float] | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    with session() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache (prompt_hash, model, role, prompt_text,"
            " response_json, embedding, tokens_in, tokens_out, created_at, hit_count)"
            " VALUES (?,?,?,?,?,?,?,?,?,0)",
            (
                prompt_hash(model, prompt), model, role, prompt,
                json.dumps(response, default=str),
                _pack(embedding) if embedding else None,
                tokens_in, tokens_out,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def stats() -> dict:
    with session() as conn:
        row = conn.execute("SELECT * FROM cache_stats WHERE id = 1").fetchone()
        entries = conn.execute("SELECT COUNT(*) AS n FROM llm_cache").fetchone()["n"]
    if row is None:
        return {"exact_hits": 0, "semantic_hits": 0, "misses": 0,
                "tokens_saved": 0, "entries": 0, "hit_rate": 0.0}
    hits = row["exact_hits"] + row["semantic_hits"]
    total = hits + row["misses"]
    return {
        "exact_hits": row["exact_hits"],
        "semantic_hits": row["semantic_hits"],
        "misses": row["misses"],
        "tokens_saved": row["tokens_saved"],
        "entries": entries,
        "hit_rate": round(hits / total, 4) if total else 0.0,
    }


def clear() -> None:
    with session() as conn:
        conn.execute("DELETE FROM llm_cache")
        conn.execute(
            "UPDATE cache_stats SET exact_hits = 0, semantic_hits = 0, "
            "misses = 0, tokens_saved = 0 WHERE id = 1"
        )
