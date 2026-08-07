"""The unit of evidence a chat answer is built from.

Every intent handler returns one of these and nothing else. That is what makes
the assistant auditable: the reply shown to the analyst is a rephrasing of
`lines`, which were computed, and the UI can show them side by side. If a figure
is in the answer but not in the fact pack, it was invented — and there is a test
for exactly that shape of mistake.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FactPack:
    headline: str = ""
    lines: list[str] = field(default_factory=list)
    # Structured payload for the UI to render (a table, a band split). Never
    # the source of the prose — that is `lines`.
    data: dict = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    # Set when the question was understood but could not be answered from the
    # evidence available. The answer layer says so plainly instead of guessing.
    shortfall: str = ""
    # Retrieval hint for the answering call: what policy context would help.
    grounding_query: str = ""
    collections: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.lines)

    def render(self) -> str:
        """Plain text form — the deterministic answer when no model is available."""
        parts = [self.headline] if self.headline else []
        parts.extend(f"- {line}" for line in self.lines)
        if self.shortfall:
            parts.append(self.shortfall)
        return "\n".join(parts)


def money(value: float | None) -> str:
    return "unknown" if value is None else f"{value:,.2f}"


def pct(value: float | None, places: int = 1) -> str:
    return "unknown" if value is None else f"{value:+.{places}f}%"


def margin_pct(price: float, unit_cost: float) -> float | None:
    if not price:
        return None
    return (price - unit_cost) / price * 100.0
