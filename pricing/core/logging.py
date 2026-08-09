"""Structured JSON logging with automatic secret redaction (NFR-012, NFR-027).

Redaction runs as a structlog processor rather than at call sites, because
relying on every caller to remember not to log an API key is a policy that fails
the first time someone is in a hurry.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# Keys whose values are replaced wholesale, matched case-insensitively on a
# substring so `llm_gateway_api_key` and `authorization` both hit.
SENSITIVE_KEY_PARTS = (
    "api_key", "apikey", "secret", "password", "passwd", "token",
    "authorization", "auth", "credential", "cookie", "session_key",
)

# Token *credentials* must always be hidden, but usage counters are essential
# operational telemetry. Check this small explicit allow-list before the broad
# "token" credential matcher below.
SAFE_TELEMETRY_KEYS = frozenset({"tokens_in", "tokens_out", "tokens_total"})

# Patterns caught inside free-text values, where a key name gives no warning.
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),                    # OpenAI-style keys
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}", re.I),       # bearer tokens
    re.compile(r"\b[\w.\-]+@[\w\-]+\.[A-Za-z]{2,}\b"),        # email (PII)
)

REDACTED = "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_TELEMETRY_KEYS:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in SENSITIVE_VALUE_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value
    if isinstance(value, dict):
        return _scrub_mapping(value)
    if isinstance(value, (list, tuple)):
        scrubbed = [_scrub_value(v) for v in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    return value


def _scrub_mapping(mapping: dict) -> dict:
    out: dict = {}
    for key, value in mapping.items():
        if isinstance(key, str) and _is_sensitive_key(key):
            out[key] = REDACTED
        else:
            out[key] = _scrub_value(value)
    return out


def redaction_processor(_logger, _name, event_dict: dict) -> dict:
    """structlog processor — the single enforcement point for redaction."""
    return _scrub_mapping(event_dict)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Idempotent. Safe to call from both services and from scripts."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redaction_processor,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
