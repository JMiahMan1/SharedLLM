"""
Fernet encryption helpers for workspace-scoped secrets.
"""
import logging

from services.config import FERNET_KEY
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger("workspace_runtime.crypto")

_KEY = FERNET_KEY.encode()
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        if not _KEY:
            raise RuntimeError(
                "FERNET_KEY environment variable is not set. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(_KEY)
    return _fernet


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception) as exc:
        log.error("[crypto] Decryption failed: %s", exc)
        return None
