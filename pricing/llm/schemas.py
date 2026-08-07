"""Pydantic schemas for structured LLM output (PRD 4.3).

Every model response used for application logic is validated against one of
these before it enters the pipeline. Nothing calls `json.loads` on model output
anywhere in this codebase.

`citations` is required on recommendation-class outputs (FR-023): a rationale
with no cited evidence is rejected rather than displayed, because an ungrounded
explanation is worse than none — it reads authoritative while resting on
nothing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DataQualityNarration(BaseModel):
    """Agent 1 — anomaly triage in business language (FR-010)."""

    headline: str = Field(description="One sentence on overall data fitness.")
    concerns: list[str] = Field(
        default_factory=list, max_length=6,
        description="Specific issues a pricing manager should know about.",
    )
    proceed_recommended: bool = Field(
        description="Whether pricing should proceed on this data."
    )


class PricingRationale(BaseModel):
    """Agent 3 — the recommendation explanation (FR-022, FR-023)."""

    rationale: str = Field(
        min_length=40, max_length=1200,
        description="Why this price, in language a pricing manager can act on.",
    )
    key_drivers: list[str] = Field(
        default_factory=list, max_length=4,
        description="The two to four factors that actually decided it.",
    )
    assumptions: list[str] = Field(
        default_factory=list, max_length=4,
        description="What must hold for this to be right.",
    )
    confidence_note: str = Field(
        default="", max_length=300,
        description="What would change this recommendation.",
    )
    citations: list[str] = Field(
        default_factory=list,
        description="Source ids from the grounding context that support the claims.",
    )


class ViolationExplanation(BaseModel):
    """Agent 4 — explains a compliance breach. Explanation only, never a verdict."""

    summary: str = Field(min_length=20, max_length=600)
    business_impact: str = Field(default="", max_length=400)
    suggested_action: str = Field(default="", max_length=400)


class RunNarrative(BaseModel):
    """Run-level summary for the dashboard."""

    headline: str = Field(max_length=200)
    summary: str = Field(min_length=40, max_length=1200)
    notable_skus: list[str] = Field(default_factory=list, max_length=6)
    risk_flags: list[str] = Field(default_factory=list, max_length=5)


class ScenarioNarration(BaseModel):
    """Agent 3 — what-if narration (FR-050)."""

    verdict: Literal["favourable", "neutral", "unfavourable"]
    summary: str = Field(min_length=30, max_length=900)
    downside: str = Field(default="", max_length=400)


class ChatRoute(BaseModel):
    """What an analyst's chat question is *about* — never its answer.

    The model classifies and extracts entities; the figures then come from the
    catalog, the run record, or the Monte Carlo engine. Optional values are
    expressed as sentinels rather than nulls: several models behind the gateway
    handle a nullable JSON-schema field poorly, and "unset" is unambiguous here
    because a zero-percent move or a zero-day window is not a question anyone
    asks.
    """

    intent: Literal[
        "product", "history", "what_if", "analysis", "platform", "unsupported"
    ] = Field(description="Which body of evidence answers this question.")
    skus: list[str] = Field(
        default_factory=list, max_length=8,
        description="SKU codes named in the question, e.g. BEV-0001-1.",
    )
    product_query: str = Field(
        default="", max_length=120,
        description="Product named in words rather than by code, if any.",
    )
    category: str = Field(default="", max_length=60)
    delta_pct: float = Field(
        default=0.0, ge=-90.0, le=90.0,
        description="Signed price move asked about; 0 when none was given.",
    )
    target_price: float = Field(
        default=0.0, ge=0.0, description="Absolute price asked about; 0 if none."
    )
    horizon_days: int = Field(default=0, ge=0, le=365)
    days_back: int = Field(default=0, ge=0, le=1095)
    objective: Literal["revenue", "margin", "balanced", ""] = ""
    restated: str = Field(
        default="", max_length=300,
        description="The question rewritten to stand alone, with pronouns resolved.",
    )


class ChatAnswer(BaseModel):
    """The assistant's reply, phrased from facts it was handed (FR-023).

    `citations` refers to retrieved source ids only. Every figure in `answer`
    must already appear in the facts block — the model is a writer here, not a
    calculator.
    """

    answer: str = Field(min_length=20, max_length=1800)
    key_points: list[str] = Field(default_factory=list, max_length=5)
    caveats: list[str] = Field(default_factory=list, max_length=3)
    citations: list[str] = Field(default_factory=list, max_length=8)
