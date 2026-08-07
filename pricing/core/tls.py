"""The single place TLS verification is decided (NFR-018).

Centralised on purpose. `verify=False` scattered across a codebase is impossible
to audit and tends to outlive the problem it was added for. Every outbound
client in this project gets its verify setting from here and nowhere else.

Order of preference (NFR-016, NFR-017):

1. **An explicit CA bundle.** Set CA_BUNDLE_PATH. Wins over everything because
   it is a deliberate operator instruction.
2. **The operating system trust store.** On a corporate machine the intercepting
   proxy's root CA is already installed there — that is how every browser on the
   same machine reaches the same hosts without complaint. Python does not
   consult it by default; it ships `certifi`, a fixed bundle of public roots
   that cannot know about a private CA. `truststore` bridges that gap, so
   verification stays fully ON and no certificate has to be exported by hand.
   This resolves the CERTIFICATE_VERIFY_FAILED that otherwise blocks the LLM
   gateway, embeddings, and the MiniLM download (PRD assumption A-03).
3. **Disable verification.** Requires ALLOW_INSECURE_TLS=true, explicitly.

What (3) actually costs, stated plainly: the client will accept *any*
certificate, including one forged by an attacker. There is no longer any
cryptographic assurance about who is on the other end of the connection. That is
tolerable only because traffic here stays inside a corporate network already
performing sanctioned interception, and because the alternative is a
non-functional prototype. It is not acceptable for production, and not for any
traffic crossing an untrusted network. With (2) available it should almost never
be needed.
"""

from __future__ import annotations

import logging
import ssl
from functools import lru_cache
from typing import Union

from pricing.config import Settings, get_settings

logger = logging.getLogger("pricing.tls")

# httpx accepts: True (default CAs), False (no verification), a CA bundle path,
# or an ssl.SSLContext.
VerifyOption = Union[bool, str, ssl.SSLContext]

_warned = False


@lru_cache(maxsize=1)
def install_os_trust_store() -> bool:
    """Make the OS certificate store the default for the whole process.

    Returning a per-client SSLContext only covers clients we construct. It does
    nothing for the libraries underneath us — `tiktoken` fetching a BPE vocab
    over `requests`, ChromaDB downloading the MiniLM ONNX model, `huggingface_hub`
    — each of which builds its own default context and fails with
    CERTIFICATE_VERIFY_FAILED behind an intercepting proxy. Those failures then
    surface far from their cause: as "no semantic model available", or as a
    chunking strategy that inexplicably refuses to initialise.

    `inject_into_ssl()` patches `ssl.SSLContext` itself, so every library gets
    the OS store without knowing about it. That is global monkeypatching, which
    is worth being uneasy about — it is the library's documented purpose, it is
    gated behind USE_OS_TRUST_STORE, and it only ever *adds* the certificates
    the machine already trusts. Verification stays fully on.

    Cached: injection is idempotent but there is no reason to repeat it.
    """
    try:
        import truststore

        truststore.inject_into_ssl()
        logger.info(
            "TLS: verifying against the OS certificate store for all libraries."
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("OS trust store unavailable: %s: %s", type(exc).__name__, exc)
        return False


@lru_cache(maxsize=1)
def _os_trust_context() -> ssl.SSLContext | None:
    """An explicit context backed by the OS store, or None if unavailable."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception as exc:  # noqa: BLE001
        logger.debug("OS trust store unavailable: %s: %s", type(exc).__name__, exc)
        return None


def verify_option(settings: Settings | None = None) -> VerifyOption:
    """Resolve the verify setting for any outbound HTTPS client."""
    s = settings or get_settings()

    if s.ca_bundle_path:
        return s.ca_bundle_path

    if s.allow_insecure_tls:
        _warn_once()
        return False

    if s.use_os_trust_store:
        # Global injection covers third-party libraries too; the explicit
        # context is belt-and-braces for the clients we build ourselves.
        install_os_trust_store()
        context = _os_trust_context()
        if context is not None:
            return context

    return True


def _warn_once() -> None:
    """Loud, once per process (NFR-019)."""
    global _warned
    if _warned:
        return
    _warned = True
    logger.warning(
        "=" * 78 + "\n"
        "  TLS CERTIFICATE VERIFICATION IS DISABLED (ALLOW_INSECURE_TLS=true).\n"
        "  This client will accept ANY certificate, including a forged one.\n"
        "  There is no protection against man-in-the-middle interception.\n"
        "  Acceptable only inside a corporate network already intercepting TLS.\n"
        "  Preferred fix: set CA_BUNDLE_PATH to the corporate root CA instead.\n"
        + "=" * 78
    )


def tls_status(settings: Settings | None = None) -> dict[str, object]:
    """Machine-readable TLS posture, surfaced to the UI banner (NFR-019)."""
    s = settings or get_settings()
    if s.ca_bundle_path:
        return {
            "mode": "ca_bundle",
            "secure": True,
            "detail": f"Verifying against corporate root CA at {s.ca_bundle_path}",
        }
    if s.allow_insecure_tls:
        return {
            "mode": "insecure",
            "secure": False,
            "detail": (
                "Certificate verification DISABLED. No MITM protection. "
                "Set CA_BUNDLE_PATH or enable USE_OS_TRUST_STORE to restore it."
            ),
        }
    if s.use_os_trust_store and _os_trust_context() is not None:
        return {
            "mode": "os_trust_store",
            "secure": True,
            "detail": (
                "Verifying against the operating system certificate store, "
                "which carries the corporate root CA"
            ),
        }
    return {
        "mode": "certifi_default",
        "secure": True,
        "detail": (
            "Verifying against the bundled certifi roots. A corporate "
            "intercepting proxy will fail here — install `truststore` or set "
            "CA_BUNDLE_PATH."
        ),
    }
