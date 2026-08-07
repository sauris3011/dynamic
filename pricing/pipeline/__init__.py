"""The five-agent pricing pipeline (PRD 4.2).

    1. Data & Context      -> concurrent ingestion + quality gate
    2. Quantitative        -> elasticity (causal) then Monte Carlo (uncertainty)
    3. Strategy & Reasoning-> constrained optimization + grounded rationale
    4. Validation & Compliance -> rules, stability, band assignment (holds veto)
    5. Execution           -> idempotent push, audit, outcome readback

Agents 1-4 may call an LLM for interpretation and narration. Agent 5 never does.
The pipeline runs to completion without any LLM at all — narration degrades to
deterministic summaries — because a pricing decision must not depend on a
gateway being reachable.
"""
