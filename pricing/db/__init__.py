"""Embedded SQLite persistence, split across two files (D9, NFR-005).

`app.db`       — run state, recommendations, audit, feedback outcomes.
`llm_cache.db` — prompt/response cache only.

Separate files rather than separate tables: cache writes are frequent and bursty,
and keeping them out of the application database avoids write contention on the
data an auditor cares about.
"""
