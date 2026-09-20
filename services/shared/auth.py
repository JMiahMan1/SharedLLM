"""Shared inter-service authentication validation."""
import os
from fastapi import HTTPException, Header

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")


def verify_internal_secret(x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret")) -> None:
    """Validate the inter-service secret header.
    
    Raises HTTPException(403) if the header is missing or does not match INTERNAL_SECRET.
    """
    expected = os.getenv("INTERNAL_SECRET", INTERNAL_SECRET)
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=403, detail="Invalid internal secret")
