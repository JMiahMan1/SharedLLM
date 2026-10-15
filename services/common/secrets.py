"""Comparing a presented secret with the configured one."""
import hmac


def secret_matches(provided: str | None, expected: str | None) -> bool:
    """Constant-time equality, and False (never an exception) for anything odd.

    ``hmac.compare_digest`` on two ``str`` raises TypeError when either holds a
    non-ASCII character, so a header with one byte >127 turned an
    authentication check into a 500. Comparing UTF-8 bytes avoids that, and an
    empty expected value never matches, so an unset secret cannot be met by an
    empty header.
    """
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
