"""Embeddings via LangChain against the LiteLLM gateway (D3, D4, D5, FR-038).

Uses `langchain_openai.OpenAIEmbeddings` pointed at the gateway, mirroring how
chat models are constructed with `init_chat_model` (D3). One LLM client library,
one place TLS and credentials are configured, and the provider stays swappable
by configuration rather than by code (NFR-032).

The model alias comes from `MODEL_EMBEDDING` and is never hardcoded (D4).

**Why this is not routed through `GroundedLLM`.** FR-043 requires that no *chat*
call bypasses the grounding wrapper, because grounding injection and structured
output validation must be unconditional. Neither concept applies to an embedding
— there is no prompt to ground and no schema to validate — and routing one
through the wrapper would recurse, since the wrapper calls retrieval and
retrieval calls embeddings. This is the substrate the wrapper is built on, so it
sits below it.

Failure policy matches the rest of the RAG layer: a gateway that cannot embed
degrades to the local strategies rather than raising, because a pricing run must
never fail over a document store. What it must not do is degrade *silently* —
`build()` reports which function is live and the UI displays it.
"""

from __future__ import annotations

from typing import Any

from pricing.config import get_settings
from pricing.core.logging import get_logger
from pricing.core.tls import verify_option

logger = get_logger("pricing.rag.gateway_embeddings")


class EmbeddingGatewayError(RuntimeError):
    """The gateway could not produce embeddings. Callers fall back."""


class GatewayEmbeddingFunction:
    """Adapts a LangChain `Embeddings` object to Chroma's interface.

    Chroma's embedding-function interface has shifted across versions: older
    releases call the object directly, newer ones call `embed_documents` on
    ingest and `embed_query` on retrieval. LangChain names the same two methods
    but `embed_query` takes a single string and returns one vector, while Chroma
    passes a list and expects a list. The two conventions collide precisely on
    that method, so the adapter exists to keep them apart — a mismatch here
    fails at query time only, which is the least convenient moment to find it.
    """

    def __init__(self, model: str, embeddings: Any) -> None:
        self.model = model
        self._embeddings = embeddings
        self._dimensions: int | None = None

    # --- Chroma calling conventions ---------------------------------------

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self.embed(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        return self.embed(input)

    def embed_query(self, input: list[str] | str) -> Any:  # noqa: A002
        # Chroma hands a list and wants a list back; LangChain hands a string
        # and wants a single vector. Honour whichever shape arrived.
        if isinstance(input, str):
            return self.embed([input])[0]
        return self.embed(input)

    def name(self) -> str:
        return f"gateway:{self.model}"

    def is_legacy(self) -> bool:
        return False

    @property
    def dimensions(self) -> int | None:
        return self._dimensions

    # --- Implementation ---------------------------------------------------

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, preserving input order.

        Batching, retry and ordering are LangChain's responsibility here; the
        client library already implements them, and reimplementing them was a
        needless second source of truth.
        """
        if not texts:
            return []
        # Embedding APIs reject empty strings. Substituting a space keeps the
        # response aligned with the input list rather than shifting every
        # subsequent vector onto the wrong document.
        cleaned = [(t.strip() or " ") for t in texts]
        try:
            vectors = self._embeddings.embed_documents(cleaned)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingGatewayError(
                f"Embedding call failed: {type(exc).__name__}: {exc}"
            ) from exc

        if len(vectors) != len(cleaned):
            raise EmbeddingGatewayError(
                f"Gateway returned {len(vectors)} embeddings for "
                f"{len(cleaned)} inputs."
            )
        if not vectors or not vectors[0]:
            raise EmbeddingGatewayError("Gateway returned a zero-length embedding.")
        if self._dimensions is None:
            self._dimensions = len(vectors[0])
        return [list(map(float, v)) for v in vectors]


def _build_langchain_embeddings(alias: str) -> Any:
    """Construct `OpenAIEmbeddings` against the gateway.

    `check_embedding_ctx_length=False` matters: LangChain otherwise tokenises
    with tiktoken against a *known OpenAI* model name to pre-chunk long inputs.
    A gateway alias like `azure/genailab-maas-text-embedding-3-large` is not a
    name tiktoken knows, so that path either errors or silently downloads an
    encoding — neither of which is wanted behind a proxy. Text arrives here
    already chunked by `rag/splitter.py`, so the pre-chunking is redundant.
    """
    from langchain_openai import OpenAIEmbeddings

    import httpx

    s = get_settings()
    verify = verify_option(s)

    return OpenAIEmbeddings(
        model=alias,
        base_url=s.llm_gateway_url.rstrip("/") + "/v1",
        api_key=s.llm_gateway_api_key or "not-needed",
        chunk_size=s.embedding_batch_size,
        check_embedding_ctx_length=False,
        http_client=httpx.Client(verify=verify, timeout=90.0),
        http_async_client=httpx.AsyncClient(verify=verify, timeout=90.0),
    )


def build(model: str | None = None) -> tuple[Any, str, int]:
    """Construct and verify a gateway embedding function.

    Raises `EmbeddingGatewayError` if the gateway cannot embed, so the caller
    can fall back. The warmup call is what makes that decision at startup rather
    than inside a user's first upload.
    """
    settings = get_settings()
    alias = (model or settings.model_embedding).strip()
    if not alias:
        raise EmbeddingGatewayError("MODEL_EMBEDDING is not configured.")

    try:
        embeddings = _build_langchain_embeddings(alias)
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingGatewayError(
            f"Could not construct the embedding client: {type(exc).__name__}: {exc}"
        ) from exc

    fn = GatewayEmbeddingFunction(alias, embeddings)
    probe = fn.embed(["warmup"])
    dimensions = len(probe[0])
    logger.info("embeddings.gateway_ready", model=alias, dimensions=dimensions)
    return fn, f"{alias} (semantic, via gateway · {dimensions}d)", dimensions
