"""
Fernet encryption helpers for workspace-scoped secrets.

Same key ring as identity (services/common/fernet_keys.py): sealed with
FERNET_KEY, opened with FERNET_KEY then FERNET_KEY_PREVIOUS during a rotation.
"""
import logging

from cryptography.fernet import InvalidToken

from services.common.fernet_keys import KeyRing
from services.config import FERNET_KEY, FERNET_KEY_PREVIOUS

log = logging.getLogger("workspace_runtime.crypto")

_ring: KeyRing | None = None


def key_ring() -> KeyRing:
    global _ring
    if _ring is None:
        _ring = KeyRing.from_values(FERNET_KEY, FERNET_KEY_PREVIOUS)
    return _ring


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return key_ring().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return key_ring().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception) as exc:
        log.error("[crypto] Decryption failed: %s", type(exc).__name__)
        return None
