"""Pricing AI Platform — the AI solution layer (PRD 4.1, D13).

Owns agents, RAG, the deterministic analytics stack, compliance rules, autonomy
bands, A2A endpoints, and telemetry. Owns **no business data**: catalog, sales,
inventory, and the live price book all live in the Commerce Service and are
reached over HTTP only.

Named `pricing` rather than `platform` deliberately — a top-level package called
`platform` would shadow Python's stdlib `platform` module and break any library
that imports it.
"""

__version__ = "1.1.0"
