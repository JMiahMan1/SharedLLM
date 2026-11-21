"""The stack's Fernet key ring: ``FERNET_KEY`` plus, during a rotation, ``FERNET_KEY_PREVIOUS``.

Every service that seals data with ``FERNET_KEY`` (identity's credential columns, the
workspace runtime's env and webhook secrets, the home hub's sealed store, telemetry's
concern images) builds its cipher here, so a key rotation is one rule everywhere:

* **Encrypt** with ``FERNET_KEY`` only, the new key.
* **Decrypt** with ``FERNET_KEY`` first, then each key in ``FERNET_KEY_PREVIOUS``
  (comma separated). A value sealed before the rotation keeps reading while
  ``scripts/rotate_fernet_key.sh`` re-encrypts it.
* **Digests** (identity's API-key lookup hashes are HMACs keyed with the Fernet key)
  are computed with the primary key and *recognised* under the previous ones, so a key
  hashed before the rotation is still found and can be re-hashed on use.

Both values are read once, when the service starts (the owner's rule: ``.env`` is read
at startup only). A malformed previous key is an error at that point, never a silently
dropped key: a value it sealed would otherwise just stop decrypting.

Nothing here ever logs or returns a key. :func:`fingerprint` gives a short one-way
label (the first 12 hex digits of its SHA-256) so an operator can confirm two places hold
the same key without anyone seeing it.
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

#: Every Fernet token is base64 of a 0x80 version byte and a timestamp, so it starts with this.
TOKEN_PREFIX = "gAAAAA"


class FernetKeyError(ValueError):
    """A configured key is missing or not a Fernet key. The message names the variable, never the value."""


def _as_bytes(key: str | bytes) -> bytes:
    return key.encode() if isinstance(key, str) else key


def parse_previous(raw: str | None) -> list[str]:
    """``FERNET_KEY_PREVIOUS`` as a list: comma separated, blanks dropped, order kept."""
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def build_fernet(key: str | bytes, name: str = "FERNET_KEY") -> Fernet:
    try:
        return Fernet(_as_bytes(key))
    except (ValueError, TypeError) as exc:
        raise FernetKeyError(
            f"{name} is not a valid Fernet key (32 url-safe base64-encoded bytes)"
        ) from exc


class KeyRing:
    """The primary key and the keys it replaced, as ciphers. Built once per process."""

    def __init__(self, primary: str | bytes | None, previous: Iterable[str | bytes] = ()) -> None:
        if not primary:
            raise FernetKeyError(
                "FERNET_KEY is not set. Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        self._primary_key = _as_bytes(primary)
        self.primary = build_fernet(primary)
        seen = {self._primary_key}
        self._previous_keys: list[bytes] = []
        for index, key in enumerate(previous):
            raw = _as_bytes(key)
            if raw in seen:
                continue  # the same key twice (a rotation step re-run): harmless, kept once
            build_fernet(raw, f"FERNET_KEY_PREVIOUS[{index}]")
            seen.add(raw)
            self._previous_keys.append(raw)
        self.previous = [Fernet(k) for k in self._previous_keys]
        self.multi = MultiFernet([self.primary, *self.previous])

    @classmethod
    def from_values(cls, primary: str | None, previous_raw: str | None) -> KeyRing:
        return cls(primary, parse_previous(previous_raw))

    @property
    def rotating(self) -> bool:
        """True while a previous key is configured (a rotation is under way)."""
        return bool(self._previous_keys)

    # -- ciphers -------------------------------------------------------------
    def encrypt(self, data: bytes) -> bytes:
        return self.primary.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        """Primary first, then each previous key. Raises ``InvalidToken`` when none opens it."""
        return self.multi.decrypt(token)

    def opens_with_primary(self, token: bytes) -> bool:
        try:
            self.primary.decrypt(token)
        except InvalidToken:
            return False
        return True

    def rotate(self, token: bytes) -> bytes:
        """The same plaintext sealed under the primary key (``InvalidToken`` if no key opens it)."""
        return self.multi.rotate(token)

    # -- digests -------------------------------------------------------------
    def digest(self, secret: str) -> str:
        """HMAC-SHA256 of ``secret`` keyed with the primary key (identity's lookup hashes)."""
        return hmac_digest(self._primary_key, secret)

    def digests(self, secret: str) -> list[str]:
        """The primary digest first, then one per previous key: what a stored hash may equal."""
        return [hmac_digest(k, secret) for k in (self._primary_key, *self._previous_keys)]

    def fingerprints(self) -> dict[str, object]:
        return {
            "primary": fingerprint(self._primary_key),
            "previous": [fingerprint(k) for k in self._previous_keys],
        }


def hmac_digest(key: bytes, secret: str) -> str:
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()


def fingerprint(key: str | bytes | None) -> str:
    """A short one-way label for a key (12 hex digits of SHA-256); '' for none. Never the key."""
    if not key:
        return ""
    return hashlib.sha256(_as_bytes(key)).hexdigest()[:12]


def looks_like_token(value: object) -> bool:
    """Is this stored value a Fernet token (text or bytes)?"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value[:6]) == TOKEN_PREFIX.encode()
    return isinstance(value, str) and value.startswith(TOKEN_PREFIX)


def ring_from_env() -> KeyRing:
    """The key ring from the process environment (each service calls this once, at start)."""
    import os

    return KeyRing.from_values(os.getenv("FERNET_KEY"), os.getenv("FERNET_KEY_PREVIOUS"))


__all__ = [
    "TOKEN_PREFIX",
    "FernetKeyError",
    "InvalidToken",
    "KeyRing",
    "build_fernet",
    "fingerprint",
    "hmac_digest",
    "looks_like_token",
    "parse_previous",
    "ring_from_env",
]
