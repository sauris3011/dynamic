"""Deciding what a chat question is about.

Two routers with one output type. The model-backed one resolves the things
keywords cannot — "and what about the other one?", "would that still clear
compliance?" — and the keyword one is not a stub: it is what runs whenever the
gateway is down, and it is deliberately good enough that the assistant stays
useful without a model at all.

Classification is the *only* influence a model has on a chat answer. What gets
measured, projected, or looked up afterwards is fixed code.
"""

from __future__ import annotations

import re

from pricing.chat import catalog as catalog_mod
from pricing.core.logging import get_logger
from pricing.llm import grounded
from pricing.llm.schemas import ChatRoute

logger = get_logger("pricing.chat.routing")

ROUTER_SYSTEM = (
    "You classify questions asked by a retail pricing analyst inside a pricing "
    "platform. Return only the classification — never an answer, never a price.\n"
    "Intents:\n"
    "- product: facts about a specific product or category as it stands today "
    "(price, cost, margin, MAP, stock).\n"
    "- history: what already happened — past sales, units, revenue, past price "
    "changes, trends over a period.\n"
    "- what_if: a hypothetical price change and its projected effect.\n"
    "- analysis: questions about pricing runs this platform has produced — "
    "recommendations, bands, escalations, compliance verdicts, approvals, "
    "accuracy of past forecasts.\n"
    "- platform: how the platform itself works, what it can do, policy, "
    "operating modes, autonomy bands, methodology.\n"
    "- unsupported: anything outside retail pricing for this retailer.\n"
    "Extract any SKU codes, product names, category, percentage move (negative "
    "for a cut), absolute target price, horizon in days, and lookback in days. "
    "Restate the question so it stands alone, resolving pronouns from the "
    "conversation."
)

_WHAT_IF = re.compile(
    r"\bwhat if\b|\bif (?:we|i)\b|\bsuppose\b|\bscenario\b|\bsimulat|\bproject(?:ed|ion)?\b"
    r"|\bwould happen\b|\bimpact of\b|\beffect of\b",
    re.IGNORECASE,
)
_MOVE = re.compile(
    r"\b(raise|raising|increase|increasing|up|hike|cut|cutting|drop|dropping|lower|"
    r"lowering|reduce|reducing|discount|down)\b",
    re.IGNORECASE,
)
_DOWNWARD = re.compile(
    r"\b(cut|cutting|drop|dropping|lower|lowering|reduce|reducing|discount|down|off)\b",
    re.IGNORECASE,
)
_HISTORY = re.compile(
    r"\b(last|past|previous|history|historical|trend|trending|sold|selling|sales|"
    r"revenue|units|so far|year to date|ytd|over the)\b",
    re.IGNORECASE,
)
_ANALYSIS = re.compile(
    r"\b(run|runs|recommend\w*|escalat\w*|band|bands|queue|approve\w*|rejected|"
    r"override|compliance|violation|forecast error|accuracy|baseline|latest "
    r"analysis|pipeline output|pending)\b",
    re.IGNORECASE,
)
_PLATFORM = re.compile(
    r"\b(how (?:do|does|did) (?:you|the|this)|what (?:can|do) you|explain how|"
    r"what is a|what are the|methodology|policy|operating mode|autonomy|"
    r"who decides|capabilit\w*|help)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent|pct)", re.IGNORECASE)
_TARGET = re.compile(
    r"\bto\s*(?:\$|£|€)?\s*(\d+(?:\.\d{1,2})?)\b", re.IGNORECASE
)
_WINDOW = re.compile(
    r"\b(?:last|past|previous|next|over)\s+(\d+)\s*(day|days|week|weeks|month|months|"
    r"quarter|quarters|year|years)\b",
    re.IGNORECASE,
)
_NAMED_WINDOW = re.compile(
    r"\b(?:last|past|previous)\s+(week|fortnight|month|quarter|year)\b", re.IGNORECASE
)
_OBJECTIVE = re.compile(r"\b(revenue|margin|balanced)\b", re.IGNORECASE)
_RECORD_ID = re.compile(r"\b(?:run|rec)-[0-9a-f]{6,16}\b", re.IGNORECASE)

_MULTIPLIER = {
    "day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30,
    "quarter": 91, "quarters": 91, "year": 365, "years": 365,
    "fortnight": 14,
}


def _window_days(text: str) -> int:
    match = _WINDOW.search(text)
    if match:
        return min(int(match.group(1)) * _MULTIPLIER.get(match.group(2).lower(), 1), 1095)
    named = _NAMED_WINDOW.search(text)
    if named:
        return _MULTIPLIER.get(named.group(1).lower(), 30)
    return 0


def keyword_route(question: str, categories: list[str]) -> ChatRoute:
    """The fallback router — and the only router when the gateway is down."""
    text = question or ""
    skus = catalog_mod.find_sku_tokens(text)
    category = catalog_mod.match_category(text, categories)

    percent = _PERCENT.search(text)
    delta = float(percent.group(1)) if percent else 0.0
    if delta > 0 and _DOWNWARD.search(text):
        delta = -delta

    target = _TARGET.search(text)
    # "to 4.50" is a price; "to 5%" already matched as a percentage.
    target_price = 0.0
    if target and not percent:
        target_price = float(target.group(1))

    window = _window_days(text)
    objective = _OBJECTIVE.search(text)

    # Order matters. "What do the bands mean?" and "what's in the escalate band?"
    # share a keyword and are different questions, so a method question with no
    # record attached to it is treated as one — otherwise every explanation of
    # how the platform works comes back as a report on the last run.
    about_a_record = bool(skus) or bool(_RECORD_ID.search(text))
    if _WHAT_IF.search(text) or (percent and _MOVE.search(text)) or target_price:
        intent = "what_if"
    elif _PLATFORM.search(text) and not about_a_record:
        intent = "platform"
    elif _ANALYSIS.search(text):
        intent = "analysis"
    elif _HISTORY.search(text):
        intent = "history"
    elif skus or category:
        intent = "product"
    else:
        intent = "platform"

    return ChatRoute(
        intent=intent,
        skus=skus,
        product_query=text[:120],
        category=category,
        delta_pct=delta,
        target_price=target_price,
        horizon_days=window if intent == "what_if" else 0,
        days_back=window if intent != "what_if" else 0,
        objective=(objective.group(1).lower() if objective else ""),  # type: ignore[arg-type]
        restated=text[:300],
    )


def _transcript(history: list[dict], limit: int = 6) -> str:
    recent = [h for h in history if h.get("text")][-limit:]
    return "\n".join(
        f"{'Analyst' if h.get('role') == 'analyst' else 'Assistant'}: "
        f"{str(h['text'])[:400]}"
        for h in recent
    )


def route(
    question: str,
    history: list[dict] | None = None,
    context: dict | None = None,
    categories: list[str] | None = None,
) -> tuple[ChatRoute, str]:
    """Classify a question. Returns (route, router_used)."""
    fallback = keyword_route(question, categories or [])
    if not grounded.available():
        return fallback, "keyword"

    where = ""
    if context:
        parts = [f"{k}={v}" for k, v in context.items() if v]
        where = f"\nThe analyst is currently looking at: {', '.join(parts)}." if parts else ""

    result = grounded.call(
        role="router",
        system=ROUTER_SYSTEM,
        user=(
            f"Known categories: {', '.join(categories or []) or 'unknown'}."
            f"{where}\n\nConversation so far:\n{_transcript(history or [])}\n\n"
            f"Classify this question:\n{question}"
        ),
        schema=ChatRoute,
        temperature=0.0,
    )
    if not (result.ok and result.data):
        logger.info("chat.router_fallback", error=result.error[:200])
        return fallback, "keyword"

    routed: ChatRoute = result.data
    # The model classifies; the deterministic extractor is trusted for anything
    # it found literally in the text. A restated SKU code that never appeared is
    # the classic hallucination here, and it would send the whole answer to the
    # wrong product.
    if fallback.skus:
        routed.skus = fallback.skus
    else:
        routed.skus = [s.upper() for s in routed.skus if s.upper() in question.upper()]
    if fallback.delta_pct and not routed.delta_pct:
        routed.delta_pct = fallback.delta_pct
    if fallback.category and not routed.category:
        routed.category = fallback.category
    if not routed.restated:
        routed.restated = question[:300]
    return routed, "model"
