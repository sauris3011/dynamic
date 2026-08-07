"""Shared state passed between pipeline stages.

Plain dataclasses rather than a framework type, so the pipeline can run under
LangGraph or standalone without the state definition changing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pricing.analytics.elasticity import ElasticityEstimate
from pricing.analytics.montecarlo import SimulationResult
from pricing.analytics.optimizer import Objective, OptimizationResult
from pricing.analytics.quality import QualityReport
from pricing.analytics.stability import StabilitySignal
from pricing.config import OperatingMode
from pricing.rules.bands import BandAssignment
from pricing.rules.engine import ComplianceVerdict


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunScope:
    kind: str = "all"                 # all | category | skus
    value: str | None = None
    skus: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.kind == "category":
            return f"category '{self.value}'"
        if self.kind == "skus":
            return f"{len(self.skus)} selected SKU(s)"
        return "the full catalog"


@dataclass
class SkuContext:
    """Everything Agent 1 gathered for one SKU."""

    sku: str
    product: dict
    sales: list[dict] = field(default_factory=list)
    inventory: dict | None = None
    competitive: Any = None                # CompetitivePosition
    base_demand: float = 0.0
    cover_days: float | None = None
    price_history: list[float] = field(default_factory=list)
    family_prices: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class SkuAnalysis:
    """The accumulated result for one SKU as it moves through the pipeline."""

    sku: str
    context: SkuContext
    elasticity: ElasticityEstimate | None = None
    simulation: SimulationResult | None = None
    optimization: OptimizationResult | None = None
    stability: StabilitySignal | None = None
    compliance: ComplianceVerdict | None = None
    band: BandAssignment | None = None
    baseline_price: float | None = None
    rationale: str = ""
    citations: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    skipped: str = ""                      # non-empty means excluded, with reason

    @property
    def recommended_price(self) -> float:
        if self.optimization:
            return self.optimization.recommended_price
        return self.context.product.get("current_price", 0.0)

    @property
    def current_price(self) -> float:
        return float(self.context.product.get("current_price", 0.0))

    @property
    def delta_pct(self) -> float:
        cur = self.current_price
        return (self.recommended_price - cur) / cur * 100.0 if cur > 0 else 0.0


@dataclass
class RunState:
    """One pricing run, start to finish."""

    run_id: str = field(default_factory=new_run_id)
    started_at: str = field(default_factory=utcnow)
    completed_at: str | None = None
    status: str = "running"                # running|completed|failed|halted
    trigger: str = "manual"                # manual|loop|a2a
    scope: RunScope = field(default_factory=RunScope)
    objective: Objective = Objective.BALANCED
    mode: OperatingMode = OperatingMode.SUPERVISED

    quality: QualityReport | None = None
    analyses: list[SkuAnalysis] = field(default_factory=list)
    narrative: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stage_timings: dict[str, float] = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0

    @property
    def priced(self) -> list[SkuAnalysis]:
        return [a for a in self.analyses if not a.skipped]

    @property
    def halted(self) -> bool:
        return self.status == "halted"

    def band_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for a in self.priced:
            if a.band:
                counts[a.band.band.value] = counts.get(a.band.band.value, 0) + 1
        return counts
