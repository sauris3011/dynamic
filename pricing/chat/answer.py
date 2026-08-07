"""Turning a fact pack into a reply.

The model's job here is narrow and stated as such in the prompt: phrase these
figures for a pricing manager. It is not asked to analyse, and it is given
nothing to analyse with — the facts arrive already computed.

Because that instruction is not self-enforcing, the answer is checked afterwards
for figures that appear in the prose but nowhere in the evidence. Anything
unsupported is reported alongside the answer rather than quietly shipped, and
the UI shows the evidence next to the reply so the check is a backstop rather
than the only defence.

With no gateway, `render` returns the fact pack itself. Less fluent, equally
true — which is the right way round.
"""

from __future__ import annotations

import re

from pricing.chat.facts import FactPack
from pricing.core.logging import get_logger
from pricing.llm import grounded
from pricing.llm.schemas import ChatAnswer

logger = get_logger("pricing.chat.answer")

ANSWER_SYSTEM = (
    "You are the analyst assistant inside a retail pricing platform. You are "
    "speaking to a pricing manager: commercially sharp, not technical.\n"
    "Rules you must follow:\n"
    "- Every figure you state must already appear in the FACTS block. Never "
    "compute, estimate, extrapolate or round into a new number.\n"
    "- If the FACTS do not answer the question, say exactly what is missing and "
    "what the analyst could do to get it. Do not fill the gap with plausibility.\n"
    "- Projections are distributions. Quote the interval or the probability "
    "alongside any expected value; never present a forecast as a certainty.\n"
    "- Never recommend pushing a price, and never imply you have changed one. "
    "Compliance verdicts and autonomy bands are decided by the engine and are "
    "not open to your opinion.\n"
    "- Be brief: a short paragraph, then the points that matter. No preamble, no "
    "restating the question."
)

# Figures worth policing: anything with a decimal point, a percentage, or a
# magnitude large enough to be a claim rather than a count. Bare small integers
# ("three bands", "5 SKUs") are excluded deliberately — flagging those produces
# noise that would train everyone to ignore the check.
_FIGURE = re.compile(r"-?\d[\d,]*(?:\.\d+)?%?")


def _value(token: str) -> float | None:
    try:
        return float(token.rstrip("%").replace(",", ""))
    except ValueError:
        return None


def _material(token: str, value: float) -> bool:
    """Is this a claim rather than a count? Percentages, decimals and anything
    three digits or longer are claims; "three bands" and "5 SKUs" are not."""
    return token.endswith("%") or "." in token or abs(value) >= 100


def unsupported_figures(answer: str, pack: FactPack, question: str) -> list[str]:
    """Figures in the prose that appear in neither the evidence nor the question.

    Tolerance is deliberately loose — a fact of 1,234.56 quoted as 1,235 is the
    same fact — so what survives is invention, not rounding.
    """
    evidence = " ".join([pack.headline, *pack.lines, pack.shortfall, question])
    known = [v for v in (_value(t) for t in _FIGURE.findall(evidence)) if v is not None]
    flagged: list[str] = []
    for token in _FIGURE.findall(answer or ""):
        value = _value(token)
        if value is None or not _material(token, value):
            continue
        tolerance = max(0.5, abs(value) * 0.01)
        if not any(abs(value - candidate) <= tolerance for candidate in known):
            flagged.append(token)
    return flagged[:6]


def _prompt(question: str, pack: FactPack, history: list[dict]) -> str:
    recent = [h for h in (history or []) if h.get("text")][-4:]
    transcript = "\n".join(
        f"{'Analyst' if h.get('role') == 'analyst' else 'You'}: {str(h['text'])[:300]}"
        for h in recent
    )
    facts = "\n".join(f"- {line}" for line in pack.lines) or "(no facts available)"
    parts = [f"QUESTION: {question}"]
    if transcript:
        parts.append(f"CONVERSATION SO FAR:\n{transcript}")
    parts.append(f"FACTS (computed by this platform, authoritative):\n{facts}")
    if pack.shortfall:
        parts.append(
            f"GAP: {pack.shortfall}\nTell the analyst this plainly rather than "
            "answering around it."
        )
    return "\n\n".join(parts)


def render(question: str, pack: FactPack, history: list[dict] | None = None) -> dict:
    """Compose the reply. Falls back to the evidence itself when no model runs."""
    if not pack.usable and pack.shortfall:
        return {
            "answer": pack.shortfall,
            "key_points": [], "caveats": [], "citations": [],
            "narrated": False, "model": "", "cache_hit": "", "tokens": 0,
            "cost_usd": 0.0, "latency_ms": 0, "unsupported_figures": [],
        }

    if not grounded.available():
        return {
            "answer": pack.render(),
            "key_points": [], "caveats": [
                "The language gateway is unavailable, so this is the computed "
                "evidence verbatim rather than a written answer. The figures are "
                "unaffected."
            ],
            "citations": [], "narrated": False, "model": "", "cache_hit": "",
            "tokens": 0, "cost_usd": 0.0, "latency_ms": 0, "unsupported_figures": [],
        }

    result = grounded.call(
        role="analyst",
        system=ANSWER_SYSTEM,
        user=_prompt(question, pack, history or []),
        schema=ChatAnswer,
        grounding_query=pack.grounding_query or question,
        collections=pack.collections or None,
    )
    if not (result.ok and result.data):
        logger.info("chat.answer_fallback", error=result.error[:200])
        return {
            "answer": pack.render(),
            "key_points": [],
            "caveats": [f"Written answer unavailable ({result.error or 'no response'}); "
                        "showing the computed evidence instead."],
            "citations": result.citations, "narrated": False, "model": result.model,
            "cache_hit": result.cache_hit, "tokens": result.tokens_total,
            "cost_usd": result.cost_usd, "latency_ms": result.latency_ms,
            "unsupported_figures": [],
        }

    data: ChatAnswer = result.data
    valid = {c["id"] for c in result.citations}
    flagged = unsupported_figures(data.answer, pack, question)
    if flagged:
        logger.warning("chat.unsupported_figures", figures=flagged, question=question[:120])
    return {
        "answer": data.answer,
        "key_points": data.key_points,
        "caveats": data.caveats,
        # A model-invented source id is not evidence (FR-023).
        "citations": [c for c in result.citations if c["id"] in valid],
        "cited_ids": [c for c in data.citations if c in valid],
        "narrated": True,
        "model": result.model,
        "cache_hit": result.cache_hit,
        "tokens": result.tokens_total,
        "cost_usd": result.cost_usd,
        "latency_ms": result.latency_ms,
        "unsupported_figures": flagged,
    }
