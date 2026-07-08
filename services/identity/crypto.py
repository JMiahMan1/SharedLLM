# services/identity/crypto.py
"""
Fernet encryption helpers for credential fields.
The FERNET_KEY env var must be a URL-safe base64-encoded 32-byte key.
Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import hashlib
import hmac
import logging

from cryptography.fernet import Fernet, InvalidToken

from services.config import FERNET_KEY

log = logging.getLogger("identity.crypto")

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
    """Encrypt a plaintext string. Returns None if input is None/empty."""
    if not plaintext:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str | None) -> str | None:
    """Decrypt a Fernet ciphertext. Returns None if input is None/empty."""
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, Exception) as e:
        log.error(f"[crypto] Decryption failed: {e}")
        return None


def digest_secret(secret: str | None) -> str | None:
    """Derive a stable HMAC-SHA256 fingerprint for lookup without storing plaintext."""
    if not secret:
        return None
    return hmac.new(_KEY, secret.encode(), hashlib.sha256).hexdigest()
