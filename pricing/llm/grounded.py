"""The only LLM entry point in this codebase (D3, D6, FR-043, FR-072).

Everything a model call must do — grounding injection, structured-output
validation, caching, token accounting, retry, redaction — happens here, once.
Nothing else constructs a chat model. That is the point: "always ground the
call" and "always validate the JSON" are guarantees you can verify by grepping
for a second entry point, not policies that depend on everyone remembering.

Degradation is deliberate and total: if the gateway is unreachable, calls return
`None` and callers fall back to deterministic text. A pricing decision must
never depend on a language model being available — the numbers come from
`pricing.analytics`, and the model only explains them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.core import telemetry
from pricing.core.tls import verify_option
from pricing.db import cache_db
from pricing.llm import costs, registry
from pricing.rag import store as rag_store

logger = get_logger("pricing.llm.grounded")

T = TypeVar("T", bound=BaseModel)

# Retrieval breadth per role (PRD 4.4). Narration needs little context; the
# strategist needs the most. Uniform full-width injection would burn tokens on
# calls that cannot use them.
ROLE_RETRIEVAL_K = {"router": 0, "narrator": 1, "analyst": 2, "strategist": 3}

# Figures in <task> are authoritative. Documents are for policy/market background.
GROUNDING_HEADER = (
    "You are given verified context from the retailer's own documents.\n"
    "- The figures and findings stated in the task have already been computed "
    "by the pricing system. Treat them as established fact and use them "
    "directly; do not ask for evidence of them and do not caveat them as "
    "unverifiable.\n"
    "- Use the context below for policy, market and product background. Cite "
    "the source ids you rely on for those points.\n"
    "- If the context does not cover something you would like to say about "
    "policy or market context, leave it out rather than inventing support."
)


@dataclass
class LLMResult:
    """Outcome of one grounded call."""

    data: Any = None
    text: str = ""
    citations: list[dict] = field(default_factory=list)
    model: str = ""
    role: str = ""
    cache_hit: str = "miss"
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cost_source: str = ""      # gateway | fallback | unknown
    latency_ms: int = 0
    repaired: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and (self.data is not None or bool(self.text))

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


class GatewayUnavailable(RuntimeError):
    pass


_model_cache: dict[str, Any] = {}


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[:-17].rstrip("/")
    if u and not u.endswith("/v1") and ("/v1/" not in u):
        u = f"{u}/v1"
    return u


def _build_model(alias: str, temperature: float | None):
    key = f"{alias}:{temperature}"
    if key in _model_cache:
        return _model_cache[key]

    from langchain.chat_models import init_chat_model

    s = get_settings()
    import httpx

    verify = verify_option(s)
    http_client = httpx.Client(verify=verify, timeout=90.0)

    kwargs: dict[str, Any] = {
        "model_provider": "openai",
        "base_url": _normalize_base_url(s.llm_gateway_url),
        "api_key": s.llm_gateway_api_key or "not-needed",
        "http_client": http_client,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature

    model = init_chat_model(alias, **kwargs)
    _model_cache[key] = model
    return model


# A model that refuses the temperature it was given says so in the 400 body.
# Matched on the message rather than a status code because the gateway wraps
# provider errors, so the code alone cannot distinguish this from a bad prompt.
_TEMPERATURE_REJECTED = re.compile(
    r"temperature|unsupportedparams", re.IGNORECASE
)


def _is_temperature_rejection(error: Exception) -> bool:
    text = str(error)
    return bool(_TEMPERATURE_REJECTED.search(text)) and "support" in text.lower()


def _compose_prompt(
    system: str, user: str, chunks: list[rag_store.RetrievedChunk]
) -> str:
    """Build the full prompt. This exact string is the cache key, so grounding
    changes correctly invalidate cached answers."""
    parts = [f"<system>\n{system}\n</system>"]
    if chunks:
        block = "\n\n".join(
            f'<source id="{c.citation_id}">\n{c.text}\n</source>' for c in chunks
        )
        parts.append(f"<grounding_context>\n{GROUNDING_HEADER}\n\n{block}\n</grounding_context>")
    parts.append(f"<task>\n{user}\n</task>")
    return "\n\n".join(parts)


@retry(
    retry=retry_if_exception_type((TimeoutError, ConnectionError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.6, min=0.6, max=6),
    reraise=True,
)
def _invoke(model, prompt: str, config: dict[str, Any] | None = None):
    return model.invoke(prompt, config=config) if config else model.invoke(prompt)


def _accumulate_usage(result: LLMResult, message: Any) -> None:
    """Add one response's token usage to `result`.

    Accumulates rather than assigns: a repair-retry costs two round trips and
    the operator is billed for both, so reporting only the successful one would
    understate spend precisely when it is highest.

    Two shapes are read because providers disagree. LangChain normalises to
    `usage_metadata`, but some gateway responses only carry the raw OpenAI
    `token_usage` block in `response_metadata`.
    """
    if message is None:
        return
    usage = getattr(message, "usage_metadata", None) or {}
    if not usage:
        meta = getattr(message, "response_metadata", None) or {}
        usage = meta.get("token_usage") or meta.get("usage") or {}
    if not isinstance(usage, dict):
        return
    result.tokens_in += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    result.tokens_out += int(
        usage.get("output_tokens") or usage.get("completion_tokens") or 0
    )


def _extract_json_text(text: str) -> str:
    """Strip DeepSeek <think> tags and markdown code blocks."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return match.group(1)
    match_obj = re.search(r"\{.*\}", text, re.DOTALL)
    return match_obj.group(0) if match_obj else text


def _structured_model(model, schema):
    try:
        return model.with_structured_output(
            schema, method="json_schema", include_raw=True
        ), True
    except TypeError:
        return model.with_structured_output(schema, method="json_schema"), False


def _invoke_structured(
    structured, prompt: str, result: LLMResult, config: dict[str, Any] | None = None,
):
    try:
        output = structured.invoke(prompt, config=config) if config else structured.invoke(prompt)
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        return None, exc

    if isinstance(output, dict):
        _accumulate_usage(result, output.get("raw"))
        return output.get("parsed"), output.get("parsing_error")

    return output, None


def _execute(
    model, prompt: str, schema, result: LLMResult, config: dict[str, Any] | None = None,
) -> None:
    if schema is not None:
        try:
            structured, _ = _structured_model(model, schema)
            parsed, error = _invoke_structured(structured, prompt, result, config)

            if error is not None or parsed is None:
                # Exactly one repair-retry (PRD 4.3)
                logger.warning("llm.repair_retry", error=str(error)[:300])
                repair = (
                    f"{prompt}\n\n<validation_error>\nYour previous response failed "
                    f"schema validation:\n{error}\nReturn ONLY valid JSON "
                    f"matching the schema.\n</validation_error>"
                )
                parsed, error = _invoke_structured(structured, repair, result, config)
                result.repaired = True
                if error is not None:
                    raise error if isinstance(error, Exception) else ValueError(str(error))
                if parsed is None:
                    raise ValueError("Model returned no parsable object after one repair attempt.")
        except (TypeError, AttributeError):
            # Fallback for local models (e.g. DeepSeek-R1 / LM Studio) without native json_schema support
            response = _invoke(model, prompt, config)
            raw_text = getattr(response, "content", str(response))
            _accumulate_usage(result, response)
            cleaned = _extract_json_text(raw_text)
            parsed = schema.model_validate_json(cleaned)

        result.data = parsed
        result.text = parsed.model_dump_json() if hasattr(parsed, "model_dump_json") else str(parsed)
        return

    response = _invoke(model, prompt, config)
    result.text = getattr(response, "content", str(response))
    _accumulate_usage(result, response)


def call(
    *,
    role: str,
    system: str,
    user: str,
    schema: type[T] | None = None,
    grounding_query: str | None = None,
    collections: list[str] | None = None,
    temperature: float | None = None,
    use_cache: bool = True,
    run_id: str | None = None,
) -> LLMResult:
    """Make a grounded, validated model call.

    `schema` turns this into a structured-output call: the model is forced to
    the schema and the result is validated before it can enter the pipeline
    (PRD 4.3). One repair-retry feeds validation errors back; after that the
    call fails cleanly rather than returning an unvalidated dict.

    `temperature=None` means "use LLM_TEMPERATURE from configuration".
    """
    started = time.time()
    alias = registry.resolve(role)

    k = ROLE_RETRIEVAL_K.get(role, 2)
    chunks = rag_store.retrieve(grounding_query or user, collections, k) if k else []
    citations = [c.to_citation() for c in chunks]
    prompt = _compose_prompt(system, user, chunks)

    if use_cache:
        cached, hit = cache_db.lookup(alias, prompt)
        if cached is not None:
            telemetry.record_llm_call(
                role=role, model=alias, run_id=run_id, cache_hit=hit,
                tokens_in=0, tokens_out=0,
                latency_ms=int((time.time() - started) * 1000),
                repaired=False, citations=len(citations),
            )
            return LLMResult(
                data=schema(**cached["data"]) if schema and cached.get("data") else None,
                text=cached.get("text", ""),
                citations=cached.get("citations", citations),
                model=alias, role=role, cache_hit=hit,
                latency_ms=int((time.time() - started) * 1000),
            )

    probe = registry.probe_gateway()
    if not probe.reachable:
        return LLMResult(
            model=alias, role=role, error=f"Gateway unavailable — {probe.summary()}",
            citations=citations, latency_ms=int((time.time() - started) * 1000),
        )

    # Configured value first; omitting the parameter is the fallback, because a
    # model that rejects an explicit temperature still accepts its own default.
    resolved = get_settings().llm_temperature if temperature is None else temperature

    result = LLMResult(model=alias, role=role, citations=citations)
    tracing_config = telemetry.langchain_config(
        run_id=run_id, user_id="system", tags=["grounded", role],
    )
    for attempt in (resolved, None):
        result = LLMResult(model=alias, role=role, citations=citations)
        try:
            model = _build_model(alias, attempt)
        except Exception as exc:  # noqa: BLE001
            result.error = f"Model init failed: {type(exc).__name__}: {exc}"
            result.latency_ms = int((time.time() - started) * 1000)
            logger.warning("llm.model_init_failed", role=role, model=alias,
                           error=result.error[:300])
            return result

        try:
            _execute(model, prompt, schema, result, tracing_config)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt is not None and _is_temperature_rejection(exc):
                logger.warning(
                    "llm.temperature_rejected",
                    role=role, model=alias, temperature=attempt,
                    error=str(exc)[:200],
                    detail=(
                        "This model refuses that temperature. Retrying without "
                        "the parameter. Set LLM_TEMPERATURE to a value it "
                        "accepts to avoid the wasted round trip."
                    ),
                )
                continue
            result.error = f"{type(exc).__name__}: {exc}"
            logger.warning("llm.call_failed", role=role, model=alias,
                           error=result.error[:300])
            result.latency_ms = int((time.time() - started) * 1000)
            return result

    result.latency_ms = int((time.time() - started) * 1000)
    rate = costs.rate_for(alias)
    result.cost_usd = round(rate.cost(result.tokens_in, result.tokens_out), 6)
    result.cost_source = rate.source

    if use_cache and result.ok:
        payload = {
            "text": result.text,
            "citations": citations,
            "data": result.data.model_dump() if isinstance(result.data, BaseModel) else None,
        }
        cache_db.store(
            alias, prompt, payload, role=role,
            tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        )

    logger.info(
        "llm.call", role=role, model=alias, cache=result.cache_hit,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        cost_usd=result.cost_usd, cost_source=result.cost_source,
        latency_ms=result.latency_ms, repaired=result.repaired,
        citations=len(citations),
    )
    telemetry.record_llm_call(
        role=role, model=alias, run_id=run_id, cache_hit=result.cache_hit,
        tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        latency_ms=result.latency_ms, repaired=result.repaired,
        citations=len(citations), error=result.error,
        cost_usd=result.cost_usd, cost_source=result.cost_source,
    )
    return result


def available() -> bool:
    """Is the gateway usable right now? Callers use this to decide whether to
    attempt narration at all."""
    try:
        return registry.probe_gateway().usable
    except Exception:
        return False
