"""Encryption at rest for the one secret this platform stores locally.

The gateway API key has to survive a restart — an operator who configured the
platform once should not have to re-paste a credential every morning, and on a
single-user workstation deployment (NFR-001) there is no secret manager to defer
to. So it is stored, and the question becomes *how*.

Plaintext in `app.db` would be wrong: that file is copied, backed up and
attached to bug reports as a matter of routine, and a key sitting in it travels
with every copy. Instead the ciphertext lives in the database and the key that
decrypts it lives in a separate file with owner-only permissions. Losing one
without the other yields nothing useful, which is the property that makes a
copied database harmless.

**What this is and is not.** It is authenticated encryption built from
`hmac`/`hashlib` — an HMAC-SHA256 keystream in counter mode, with an
encrypt-then-MAC tag over nonce and ciphertext. That construction is standard
and the primitives are the standard library's, so there is no new dependency to
install behind a corporate proxy. What it is *not* is protection against an
attacker who already has read access to this user's home directory: they can
read the key file, and then the ciphertext. Nothing available on a single-user
box would change that. The threat this addresses is the database leaving the
machine, and for that it is sufficient.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import stat
from pathlib import Path

from pricing.core.logging import get_logger

logger = get_logger("pricing.core.secrets")

_MAGIC = b"dpe1"          # version prefix, so the format can change later
_NONCE_BYTES = 16
_TAG_BYTES = 32
_KEY_FILENAME = ".secret.key"


def _key_path() -> Path:
    from pricing.config import get_settings

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s.data_dir / _KEY_FILENAME


def _load_or_create_master() -> bytes:
    """The machine-local master key, created on first use.

    Not cached: `data_dir` is monkeypatched per test and a cached key from a
    previous temp directory would decrypt nothing.
    """
    path = _key_path()
    if path.exists():
        raw = path.read_bytes().strip()
        if len(raw) >= 32:
            return raw
        logger.warning("secrets.key_file_short", path=str(path),
                       detail="Regenerating; previously stored secrets are lost.")

    key = base64.urlsafe_b64encode(os.urandom(48))
    path.write_bytes(key)
    _restrict(path)
    logger.info("secrets.key_created", path=str(path))
    return key


def _restrict(path: Path) -> None:
    """Owner-only permissions, best effort.

    `chmod` is honoured on POSIX. On Windows it only toggles the read-only bit
    and does not touch the ACL, so the real protection there is that the file
    sits under the user's own profile. Worth doing anyway rather than skipping
    by platform — a data directory on a network share behaves like POSIX.
    """
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as exc:
        logger.debug("secrets.chmod_failed", path=str(path), error=str(exc))


def _subkeys(master: bytes) -> tuple[bytes, bytes]:
    """Separate encryption and authentication keys from the one master.

    Distinct keys for distinct purposes: reusing a single key for both the
    keystream and the tag is the classic way an otherwise sound construction
    stops being sound.
    """
    enc = hmac.new(master, b"dpe-encryption", hashlib.sha256).digest()
    mac = hmac.new(master, b"dpe-authentication", hashlib.sha256).digest()
    return enc, mac


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns base64 text safe for a TEXT column."""
    if not plaintext:
        return ""
    master = _load_or_create_master()
    enc_key, mac_key = _subkeys(master)

    nonce = os.urandom(_NONCE_BYTES)
    data = plaintext.encode("utf-8")
    ciphertext = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, _MAGIC + nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(_MAGIC + nonce + ciphertext + tag).decode("ascii")


def decrypt(blob: str) -> str:
    """Recover a stored secret, or return "" if it cannot be authenticated.

    A tampered or undecryptable value degrades to "no key configured" rather
    than raising: a corrupted row must not stop the platform from booting, and
    the operator can always re-enter the key from the settings drawer.
    """
    if not blob:
        return ""
    try:
        raw = base64.urlsafe_b64decode(blob.encode("ascii"))
    except Exception:  # noqa: BLE001
        logger.warning("secrets.undecodable")
        return ""

    if not raw.startswith(_MAGIC) or len(raw) < len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES:
        logger.warning("secrets.malformed")
        return ""

    body = raw[len(_MAGIC):]
    nonce, ciphertext, tag = (
        body[:_NONCE_BYTES], body[_NONCE_BYTES:-_TAG_BYTES], body[-_TAG_BYTES:]
    )

    master = _load_or_create_master()
    enc_key, mac_key = _subkeys(master)

    expected = hmac.new(mac_key, _MAGIC + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        # Almost always means the key file was regenerated or the database was
        # moved between machines — not an attack, but the value is unusable
        # either way and saying so beats returning garbage.
        logger.warning(
            "secrets.authentication_failed",
            detail="Stored gateway key cannot be decrypted with this machine's "
                   "key file. Re-enter it in Settings.",
        )
        return ""

    plaintext = bytes(
        a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))
    )
    return plaintext.decode("utf-8", errors="replace")


def fingerprint(secret: str) -> str:
    """A short, non-reversible identifier for a key.

    Lets the UI show *which* key is loaded — so an operator can tell a stale
    stored credential from the one they just pasted — without the key itself
    ever reaching the browser (FR-072, NFR-011).
    """
    if not secret:
        return ""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
