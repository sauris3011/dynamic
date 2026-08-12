"""Chat input guardrails & moderation (FR-073, NFR-021).

Uses `better_profanity` and rule-based checks to detect abusive, profane, or
inappropriate input before calling the LLM gateway.
"""

from __future__ import annotations

import re
from better_profanity import profanity

# Initialize default wordlist
profanity.load_censor_words()

_INSULT_PATTERNS = re.compile(
    r"\b(you (?:are|r) (?:a )?(?:shit|bitch|idiot|fool|stupid|bastard|dummy|useless))\b",
    re.IGNORECASE,
)

PROFANE_REFUSAL = (
    "I am a retail pricing assistant designed to support commercial pricing operations. "
    "Please keep questions professional and focused on products, history, price scenarios, "
    "or pricing run analysis."
)

UNSUPPORTED_SCOPE_REFUSAL = (
    "I can only assist with retail pricing operations — including product details, "
    "trading history, scenario projections, compliance rules, and pricing run analysis. "
    "Questions outside retail pricing are not supported."
)


def check_moderation(text: str) -> tuple[bool, str | None]:
    """Check user text for profanity, insults, or abuse.

    Returns (is_flagged, refusal_message).
    """
    if not text or not text.strip():
        return False, None

    cleaned = text.strip()
    if _INSULT_PATTERNS.search(cleaned) or profanity.contains_profanity(cleaned):
        return True, PROFANE_REFUSAL

    return False, None
