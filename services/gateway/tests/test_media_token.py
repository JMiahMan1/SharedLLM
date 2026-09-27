"""Tests for signed media tokens (§7.4): services/shared/media_token.py and
POST /api/media/token."""
import time

import pytest

from services.shared.media_token import SCOPE, TOKEN_TTL_SECONDS, sign, verify


def test_sign_verify_roundtrip():
    token, expires_at = sign("alice", now=1_000_000)
    assert expires_at == 1_000_000 + TOKEN_TTL_SECONDS
    assert verify(token, "alice", now=1_000_000) is True


def test_verify_rejects_expired_token():
    token, _ = sign("alice", now=1_000_000)
    assert verify(token, "alice", now=1_000_000 + TOKEN_TTL_SECONDS + 1) is False


def test_verify_accepts_token_just_before_expiry():
    token, _ = sign("alice", now=1_000_000)
    assert verify(token, "alice", now=1_000_000 + TOKEN_TTL_SECONDS - 1) is True


def test_verify_rejects_tampered_signature():
    token, _ = sign("alice", now=1_000_000)
    exp, signature = token.split(".")
    tampered = f"{exp}.{'0' if signature[0] != '0' else '1'}{signature[1:]}"
    assert verify(tampered, "alice", now=1_000_000) is False


def test_verify_rejects_wrong_user():
    token, _ = sign("alice", now=1_000_000)
    assert verify(token, "bob", now=1_000_000) is False


def test_verify_rejects_wrong_scope():
    import hashlib
    import hmac
    import os

    exp = 1_000_000 + TOKEN_TTL_SECONDS
    payload = f"alice:other:{exp}".encode()
    secret = (os.getenv("MEDIA_TOKEN_SECRET") or os.getenv("INTERNAL_SECRET") or "").encode()
    signature = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    assert verify(f"{exp}.{signature}", "alice", now=1_000_000) is False


@pytest.mark.parametrize("garbage", ["", "garbage", "123", "abc.def.ghi", ".sig", "123."])
def test_verify_rejects_malformed_tokens(garbage):
    assert verify(garbage, "alice", now=1_000_000) is False


def test_scope_is_media():
    assert SCOPE == "media"


@pytest.mark.asyncio
async def test_endpoint_issues_token_for_caller(client):
    before = int(time.time())
    resp = client.post("/api/media/token")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"token", "expires_at"}
    assert verify(body["token"], "testuser", now=before) is True
    assert before + TOKEN_TTL_SECONDS <= body["expires_at"] <= before + TOKEN_TTL_SECONDS + 2


@pytest.mark.asyncio
async def test_endpoint_token_is_user_specific(client):
    resp = client.post("/api/media/token")
    token = resp.json()["token"]
    assert verify(token, "someone-else", now=int(time.time())) is False
