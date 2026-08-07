"""How the platform itself works, and what it is currently configured to do.

Two kinds of fact, deliberately mixed. The first is fixed: the pipeline shape,
the veto, the band definitions — architecture, not state. The second is read
live: the operating mode in force, the thresholds an operator has edited, what
is in the document store. An answer that explained bands correctly while getting
this instance's confidence threshold wrong would be worse than useless, because
it would be checkable and wrong.

Uploaded policy documents reach the answer through retrieval, not from here.
"""

from __future__ import annotations

from pricing.chat.facts import FactPack
from pricing.config import get_settings
from pricing.db.app_db import get_setting, session
from pricing.llm.schemas import ChatRoute
from pricing.rag import store as rag_store

CAPABILITIES = [
    "I answer four kinds of question: what a product's position is today, how it "
    "has traded historically, what a hypothetical price move would be worth, and "
    "what this platform's own runs have concluded.",
    "The pricing pipeline is five agents in sequence: data context, quantitative "
    "analysis (elasticity plus Monte Carlo), strategy, compliance validation, and "
    "persistence. Prices come from the optimizer; language models explain them and "
    "never choose them.",
    "Elasticity is estimated per SKU from sales history by log-log regression with "
    "a confidence interval. Where the interval is too wide to be usable the SKU is "
    "simulated against a wide category prior and flagged as indicative.",
    "Every candidate price is simulated thousands of times against sampled demand, "
    "cost drift and competitor response, producing a distribution — not a point "
    "estimate — with a fixed seed, so identical inputs reproduce identical output.",
    "A deterministic rule engine holds a veto: MAP floors, margin floors, price-"
    "ladder consistency within a family, and maximum move size. A blocked price "
    "cannot be approved, pushed, or argued out of by any model or any caller.",
    "Every recommendation lands in one of three autonomy bands. auto-approve may "
    "move without a human, but only when the operating mode allows it; review "
    "needs an operator; escalate needs a decision and states why.",
    "The feedback loop measures pushed prices against what actually sold, then "
    "refines the elasticity belief for that SKU by a capped step, so the system "
    "learns slowly rather than lurching after one surprising week.",
    "I never change a price. Approving, overriding, rejecting and pushing are "
    "operator actions with an audit trail; I only explain and project.",
]


def _configuration_lines() -> list[str]:
    s = get_settings()
    with session() as conn:
        stored = {
            key: get_setting(conn, key)
            for key in ("operating_mode", "min_confidence", "max_delta_pct",
                        "max_variance", "margin_buffer_pct")
        }
    mode = stored["operating_mode"] or s.operating_mode.value
    lines = [
        f"Operating mode right now: {mode}. supervised pushes nothing "
        "automatically; assisted auto-approves the auto-approve band; autonomous "
        "additionally pushes reviewed items after a hold window.",
        f"Band thresholds in force: minimum confidence "
        f"{float(stored['min_confidence'] or s.band_min_confidence):.2f}, maximum "
        f"move {float(stored['max_delta_pct'] or s.band_max_delta_pct):.1f}%, "
        f"maximum revenue variance "
        f"{float(stored['max_variance'] or s.band_max_variance):.2f}, margin buffer "
        f"{float(stored['margin_buffer_pct'] or s.band_margin_buffer_pct):.1f}%.",
        f"Monte Carlo settings: {s.mc_iterations:,} iterations, seed {s.mc_seed}. "
        "Stability: oscillation window of "
        f"{s.oscillation_window} observations, damping factor {s.damping_factor}.",
        f"Models by role — router {s.model_router}, narrator {s.model_narrator}, "
        f"analyst {s.model_analyst}, strategist {s.model_strategist}, all reached "
        "through the configured gateway. No model id is hardcoded.",
    ]
    return lines


def _grounding_lines() -> list[str]:
    try:
        stats = rag_store.stats()
    except Exception:  # noqa: BLE001 - a missing store is a degraded answer, not an error
        return ["The document store is unavailable, so answers carry no citations."]
    if not stats.get("available"):
        return ["The document store is unavailable, so answers carry no citations."]
    populated = {
        name: detail["chunks"]
        for name, detail in stats["collections"].items() if detail["chunks"]
    }
    if not populated:
        return [
            "No documents have been ingested yet, so I cannot cite policy. Upload "
            "pricing policy, MAP agreements or market reports in the grounding panel "
            "and they will be retrieved on every answer."
        ]
    return [
        "Grounding documents available: "
        + ", ".join(f"{name} ({count} chunks)" for name, count in populated.items())
        + f", embedded with {stats['embedding_model']}."
    ]


def build(route: ChatRoute, question: str) -> FactPack:
    """What the platform is and how it is set up, plus retrieved policy."""
    lines = list(CAPABILITIES)
    lines.extend(_configuration_lines())
    lines.extend(_grounding_lines())
    if route.intent == "unsupported":
        lines.append(
            "That question is outside what this platform holds. I can only speak to "
            "this retailer's catalog, its trading history, price scenarios, and the "
            "runs this platform has produced."
        )
    return FactPack(
        headline="How this platform works, and how it is configured right now.",
        lines=lines,
        data={},
        sources=["Platform configuration", "Architecture"],
        grounding_query=question or "pricing policy methodology governance",
        collections=["pricing_policy", "market_intel", "user_uploads"],
    )
