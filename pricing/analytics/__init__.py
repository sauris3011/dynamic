"""Deterministic analytics — the causal, uncertainty, and decision layers.

**No LLM calls anywhere in this package.** Price optimization is mathematics;
routing it through a language model would make it slower, costlier, and
non-reproducible. The agents call into here as tools and interpret the results.

Layer order is a requirement, not a preference (PRD 4.7):

    elasticity.py  -> causal:      price -> demand response
    montecarlo.py  -> uncertainty: distributions around that response
    optimizer.py   -> decision:    constrained choice over those distributions
"""
