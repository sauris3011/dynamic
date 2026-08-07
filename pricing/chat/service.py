"""One question in, one grounded answer out.

The shape is fixed and the same for every question: classify, gather evidence
deterministically, phrase. The classifier may be a model or a keyword router;
the evidence never is. That ordering is what lets a chat answer be shown next to
the figures it came from — and what stops a conversational interface from
becoming a second, unaccountable route to a pricing opinion.

Questions are recorded in the audit log. A pricing decision that was influenced
by an answer should be traceable to the question that produced it.
"""

from __future__ import annotations

import time

from pricing.chat import (
    analysis_facts,
    answer as answer_mod,
    catalog as catalog_mod,
    history_facts,
    platform_facts,
    product_facts,
    routing,
    suggestions as suggestions_mod,
    whatif_facts,
)
from pricing.chat.facts import FactPack
from pricing.core import telemetry
from pricing.core.logging import get_logger
from pricing.db.app_db import audit, session
from pricing.llm.schemas import ChatRoute

logger = get_logger("pricing.chat.service")

MAX_QUESTION_CHARS = 600


def _gather(route: ChatRoute, question: str, context: dict) -> FactPack:
    catalog = catalog_mod.snapshot()
    if route.intent == "what_if":
        return whatif_facts.build(route, catalog, question)
    if route.intent == "history":
        return history_facts.build(route, catalog)
    if route.intent == "analysis":
        return analysis_facts.build(route, question, context)
    if route.intent == "product":
        pack = product_facts.build(route, catalog)
        # A product question about something the catalog cannot resolve is often
        # really a question about the platform ("what do you know about
        # pricing?"). Answering "no such SKU" to that is a non-sequitur.
        if not pack.usable and not route.skus and not route.category:
            return platform_facts.build(route, question)
        return pack
    return platform_facts.build(route, question)


def ask(
    question: str,
    history: list[dict] | None = None,
    context: dict | None = None,
    actor: str = "operator",
) -> dict:
    """Answer one analyst question from evidence this platform holds."""
    started = time.time()
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    context = {k: v for k, v in (context or {}).items() if v}
    catalog = catalog_mod.snapshot()

    route, router_used = routing.route(question, history, context, catalog.categories)
    pack = _gather(route, question, context)
    composed = answer_mod.render(question, pack, history)

    payload = {
        "question": question,
        "intent": route.intent,
        "router": router_used,
        "headline": pack.headline,
        "answer": composed["answer"],
        "key_points": composed.get("key_points", []),
        "caveats": list(composed.get("caveats", [])),
        "facts": pack.lines,
        "data": pack.data,
        "sources": pack.sources,
        "citations": composed.get("citations", []),
        "shortfall": pack.shortfall,
        "unsupported_figures": composed.get("unsupported_figures", []),
        "suggestions": suggestions_mod.follow_ups(route, catalog, pack.data),
        "narrated": composed.get("narrated", False),
        "model": composed.get("model", ""),
        "cache_hit": composed.get("cache_hit", ""),
        "tokens": composed.get("tokens", 0),
        "cost_usd": composed.get("cost_usd", 0.0),
        "latency_ms": int((time.time() - started) * 1000),
        "scope": {
            "skus": route.skus,
            "category": route.category,
            "horizon_days": route.horizon_days,
            "days_back": route.days_back,
        },
    }
    if not catalog.available and route.intent in ("product", "history", "what_if"):
        payload["caveats"].append(catalog.error or "The catalog is unavailable.")
    if payload["unsupported_figures"]:
        payload["caveats"].append(
            "Figures in this reply that do not appear in the evidence below: "
            + ", ".join(payload["unsupported_figures"])
            + ". Trust the evidence, not the prose."
        )

    _record(payload, actor)
    return payload


def _record(payload: dict, actor: str) -> None:
    logger.info(
        "chat.answered", intent=payload["intent"], router=payload["router"],
        narrated=payload["narrated"], facts=len(payload["facts"]),
        citations=len(payload["citations"]), tokens=payload["tokens"],
        latency_ms=payload["latency_ms"],
        unsupported=len(payload["unsupported_figures"]),
    )
    telemetry.record(
        "chat_question",
        intent=payload["intent"], router=payload["router"],
        narrated=payload["narrated"], tokens=payload["tokens"],
        latency_ms=payload["latency_ms"],
    )
    try:
        with session() as conn:
            audit(
                conn, actor=actor, event_type="chat_question",
                entity_type="chat", entity_id=payload["intent"],
                question=payload["question"][:300],
                skus=payload["scope"]["skus"], category=payload["scope"]["category"],
                narrated=payload["narrated"], router=payload["router"],
            )
    except Exception as exc:  # noqa: BLE001 - an unwritable audit must not lose the answer
        logger.warning("chat.audit_failed", error=str(exc)[:200])


def suggestions(limit: int = 6) -> dict:
    """Opening questions, chosen from what the system currently holds."""
    catalog = catalog_mod.snapshot()
    return {
        "suggestions": suggestions_mod.starters(limit),
        "catalog_available": catalog.available,
        "categories": catalog.categories,
        "detail": catalog.error or "",
    }
