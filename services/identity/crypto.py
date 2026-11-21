# services/identity/crypto.py
"""
Fernet encryption helpers for credential fields.

The FERNET_KEY env var must be a URL-safe base64-encoded 32-byte key.
Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

During a key rotation FERNET_KEY_PREVIOUS holds the key being replaced: values
sealed with it still decrypt, new values are sealed with FERNET_KEY only, and a
lookup hash made with it is still recognised (``digest_candidates``). See
services/common/fernet_keys.py and scripts/rotate_fernet_key.sh.
"""
import logging

from cryptography.fernet import InvalidToken

from services.common.fernet_keys import KeyRing
from services.config import FERNET_KEY, FERNET_KEY_PREVIOUS

log = logging.getLogger("identity.crypto")

_ring: KeyRing | None = None


def key_ring() -> KeyRing:
    """The process's key ring, built on first use from the values read at start."""
    global _ring
    if _ring is None:
        _ring = KeyRing.from_values(FERNET_KEY, FERNET_KEY_PREVIOUS)
    return _ring


def encrypt(plaintext: str | None) -> str | None:
    """Encrypt a plaintext string with the primary key. Returns None if input is None/empty."""
    if not plaintext:
        return None
    return key_ring().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a Fernet ciphertext (primary key, then previous). Returns None if input is None/empty."""
    if not ciphertext:
        return None
    try:
        return key_ring().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception) as e:
        log.error(f"[crypto] Decryption failed: {type(e).__name__}")
        return None


def digest_secret(secret: str | None) -> str | None:
    """Derive a stable HMAC-SHA256 fingerprint for lookup without storing plaintext."""
    if not secret:
        return None
    return key_ring().digest(secret)


def digest_candidates(secret: str | None) -> list[str]:
    """Every hash a stored lookup value for ``secret`` may hold: the primary key's first.

    While a rotation is under way a key hashed under the previous Fernet key is still
    found; the caller re-hashes it with ``digest_secret`` on that first use.
    """
    if not secret:
        return []
    return key_ring().digests(secret)
