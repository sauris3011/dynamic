"""Boot-time configuration and validation (NFR-022).

Everything the system needs is declared here as a typed, validated schema. A bad
or missing value fails at startup with a specific message rather than surfacing
as a confusing error deep inside an agent run.

Model IDs are never hardcoded (D4, FR-004): roles map to aliases resolved from
the environment and verified against the gateway at boot.
"""

from __future__ import annotations

import json
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class OperatingMode(str, Enum):
    """Global autonomy posture (FR-101). Supervised is the default."""

    SUPERVISED = "supervised"   # nothing pushes automatically
    ASSISTED = "assisted"       # auto-approve band pushes
    AUTONOMOUS = "autonomous"   # auto-approve + review push after a hold window


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
        # `model_` is a Pydantic-protected prefix; our MODEL_* env vars are
        # mapped explicitly below via alias, so protection can stay off here.
        protected_namespaces=(),
    )

    # --- LLM gateway (backend-only, FR-072) ---
    llm_gateway_url: str = "http://localhost:4000"
    llm_gateway_api_key: str = ""

    # --- Role -> model alias registry (D4) ---
    model_router: str = Field("gemini/gemini-2.5-flash", alias="MODEL_ROUTER")
    model_narrator: str = Field("gemini/gemini-2.5-flash", alias="MODEL_NARRATOR")
    model_analyst: str = Field("gemini/gemini-2.5-flash", alias="MODEL_ANALYST")
    model_strategist: str = Field("gemini/gemini-2.5-pro", alias="MODEL_STRATEGIST")
    # Embeddings resolve through the same gateway as chat when configured.
    # Empty means "no gateway embeddings" and the local strategies apply
    # instead — so an unset value degrades rather than failing at first ingest.
    model_embedding: str = Field("", alias="MODEL_EMBEDDING")
    embedding_batch_size: int = Field(64, ge=1, le=2048)

    # Sampling temperature for every chat call. Configurable because model
    # families disagree about what they accept: the gpt-5 line rejects anything
    # other than 1.0 outright with a 400, while others are happy at 0.2 and
    # produce steadier narration for it. Default 1.0 — the value that works
    # everywhere (D4, NFR-032).
    llm_temperature: float = Field(1.0, ge=0.0, le=2.0, alias="LLM_TEMPERATURE")

    # Applied only when the gateway advertises no rate for a model (FR-065).
    # Quoted per million tokens, the unit vendors publish, so the number in
    # .env can be copied straight from a price list.
    llm_fallback_input_cost_per_mtok: float = Field(0.15, ge=0.0)
    llm_fallback_output_cost_per_mtok: float = Field(0.60, ge=0.0)

    # --- Corporate TLS (NFR-016 .. NFR-020) ---
    ca_bundle_path: str = ""
    allow_insecure_tls: bool = False
    # Verify against the OS certificate store, where a corporate root CA is
    # already installed. On by default: it is the preferred fix for TLS
    # interception and keeps verification fully on (NFR-016).
    use_os_trust_store: bool = True

    # --- RAG chunking (FR-038) ---
    chunk_strategy: str = "recursive"      # recursive | character | token | markdown
    chunk_size: int = Field(900, ge=100, le=8000)
    chunk_overlap: int = Field(120, ge=0, le=2000)
    # Split points in descending priority. Encoded with \n escapes because .env
    # values are single-line; decoded in rag/splitter.py.
    chunk_separators: str = r"\n\n|\n|. | |"

    # --- Services (NFR-003) ---
    commerce_port: int = Field(8001, gt=1024, le=65535)
    pricing_port: int = Field(8000, gt=1024, le=65535)
    ui_port: int = Field(5173, gt=1024, le=65535)
    commerce_base_url: str = "http://127.0.0.1:8001"

    # --- Local embedded storage (NFR-004, D9) ---
    data_dir: Path = Path("./data")

    # --- Observability (D12) ---
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    log_level: str = "INFO"

    # --- Autonomy bands (FR-100, FR-101) ---
    operating_mode: OperatingMode = OperatingMode.SUPERVISED
    band_min_confidence: float = Field(0.75, ge=0.0, le=1.0)
    band_max_delta_pct: float = Field(10.0, gt=0.0, le=100.0)
    band_max_variance: float = Field(0.25, gt=0.0)
    band_margin_buffer_pct: float = Field(3.0, ge=0.0, le=50.0)

    # --- Monte Carlo (FR-083, FR-087, NFR-037) ---
    mc_iterations: int = Field(2000, ge=100, le=100000)
    mc_seed: int = 20260807

    # --- Competitor feed (FR-004, FR-005, D14) ---
    # Persistent positioning per competitor: a multiplier on our reference
    # price, so a discounter sits below us and a premium retailer above.
    # JSON so the roster itself is configurable, not just the numbers — a
    # different market has different rivals, which is the whole point of the
    # pluggable feed interface.
    competitor_bias: str = (
        '{"MarketFresh": 1.04, "ValueMart": 0.93, "Prime Grocer": 1.00}'
    )
    competitor_idiosyncratic_pct: float = Field(14.0, ge=0.0, le=100.0)
    competitor_drift_pct: float = Field(3.5, ge=0.0, le=50.0)
    competitor_out_of_stock_rate: float = Field(0.085, ge=0.0, le=1.0)

    # --- Stability (FR-090, FR-091, FR-094) ---
    oscillation_window: int = Field(4, ge=2, le=50)
    oscillation_max_reversals: int = Field(2, ge=1, le=20)
    damping_factor: float = Field(0.4, gt=0.0, le=1.0)
    min_hours_between_changes: int = Field(0, ge=0)

    # --- Orchestration (D2) ---
    # The graph is a wrapper over the same stage functions, not a second
    # implementation, so turning it off changes checkpointing — never results.
    use_langgraph: bool = True

    # --- Continuous loop (FR-121, FR-122) ---
    loop_default_interval_seconds: int = Field(300, ge=30, le=86400)
    loop_max_iterations: int = Field(100, ge=1, le=100000)

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got '{v}'")
        return upper

    @field_validator("chunk_strategy")
    @classmethod
    def _valid_chunk_strategy(cls, v: str) -> str:
        allowed = {"recursive", "character", "token", "markdown"}
        lowered = v.strip().lower()
        if lowered not in allowed:
            raise ValueError(
                f"CHUNK_STRATEGY must be one of {sorted(allowed)}, got '{v}'"
            )
        return lowered

    @field_validator("competitor_bias")
    @classmethod
    def _valid_competitor_bias(cls, v: str) -> str:
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"COMPETITOR_BIAS must be a JSON object mapping competitor name "
                f"to a price multiplier. Parse failed: {exc}"
            ) from exc
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("COMPETITOR_BIAS must be a non-empty JSON object.")
        for name, multiplier in parsed.items():
            if not isinstance(multiplier, (int, float)) or not 0.1 <= multiplier <= 5.0:
                raise ValueError(
                    f"COMPETITOR_BIAS['{name}'] must be a number between 0.1 and "
                    f"5.0 (a multiplier on our price), got {multiplier!r}."
                )
        return v

    @model_validator(mode="after")
    def _check_chunk_overlap(self) -> "Settings":
        # Overlap at or above chunk size makes the splitter unable to advance;
        # LangChain raises deep inside ingestion rather than at boot.
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be smaller than "
                f"CHUNK_SIZE ({self.chunk_size}); otherwise chunking cannot "
                "make forward progress."
            )
        return self

    @model_validator(mode="after")
    def _check_tls_choice(self) -> "Settings":
        if self.ca_bundle_path and self.allow_insecure_tls:
            raise ValueError(
                "CA_BUNDLE_PATH and ALLOW_INSECURE_TLS are both set. These are "
                "mutually exclusive: either verify against the corporate root CA "
                "(preferred) or disable verification (last resort). Pick one."
            )
        if self.ca_bundle_path and not Path(self.ca_bundle_path).is_file():
            raise ValueError(
                f"CA_BUNDLE_PATH points at '{self.ca_bundle_path}', which is not a "
                "readable file."
            )
        return self

    @model_validator(mode="after")
    def _check_ports_distinct(self) -> "Settings":
        ports = [self.commerce_port, self.pricing_port, self.ui_port]
        if len(set(ports)) != len(ports):
            raise ValueError(
                f"COMMERCE_PORT, PRICING_PORT and UI_PORT must differ, got {ports}"
            )
        return self

    # --- Derived paths (D9: separate DB files, both WAL) ---
    @property
    def app_db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def cache_db_path(self) -> Path:
        return self.data_dir / "llm_cache.db"

    @property
    def chroma_path(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def trace_log_path(self) -> Path:
        return self.data_dir / "traces.jsonl"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def model_for_role(self, role: str) -> str:
        """Resolve an agent role to its configured model alias (D4)."""
        mapping = {
            "router": self.model_router,
            "narrator": self.model_narrator,
            "analyst": self.model_analyst,
            "strategist": self.model_strategist,
            "embeddings": self.model_embedding,
        }
        if role not in mapping:
            raise KeyError(f"Unknown model role '{role}'. Known: {sorted(mapping)}")
        return mapping[role]

    @property
    def competitor_bias_map(self) -> dict[str, float]:
        """Competitor name -> price multiplier. Validated at boot."""
        return {k: float(v) for k, v in json.loads(self.competitor_bias).items()}

    @property
    def chunk_separator_list(self) -> list[str]:
        r"""Split points, highest priority first.

        Pipe-delimited in the environment because separators are themselves
        whitespace — a comma-separated list cannot express " " unambiguously.
        `\n` and `\t` are decoded; a trailing empty entry is meaningful and
        preserved, since it is the character-level split of last resort.
        """
        raw = self.chunk_separators
        return [
            part.replace("\\n", "\n").replace("\\t", "\t")
            for part in raw.split("|")
        ]

    @property
    def gateway_embeddings_enabled(self) -> bool:
        """Is a gateway embedding model configured (FR-004, D4, A-03)?

        Deliberately independent of the chat-role probe. A missing embedding
        alias must not make `probe_gateway().usable` false, because that would
        silently disable all narration over an unrelated capability.
        """
        return bool(self.model_embedding.strip())

    @property
    def configured_models(self) -> set[str]:
        return {
            self.model_router, self.model_narrator,
            self.model_analyst, self.model_strategist,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton. Raises pydantic.ValidationError on bad config."""
    return Settings()  # type: ignore[call-arg]
