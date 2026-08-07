"""LLM access layer.

`grounded.GroundedLLM` is the **only** way to call a model in this codebase
(D6, FR-043). Nothing else constructs a chat model or hits the gateway. That is
what makes mandatory grounding and structured-output validation structural
guarantees rather than conventions someone can forget on a busy afternoon.
"""
