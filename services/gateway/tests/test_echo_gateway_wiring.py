"""Wiring tests for Whisper Mode context and Tier-1 FastPath Drop-In in gateway/main.py."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from services.gateway import main as gw


def test_voice_context_from_preserves_whispered_flag_for_internal_callers(monkeypatch) -> None:
    monkeypatch.setattr(gw, "INTERNAL_SECRET", "top-secret")
    req = SimpleNamespace(headers={"X-Internal-Secret": "top-secret"})
    body = {
        "voice_context": {
            "area": "Bedroom",
            "satellite": "assist_satellite.bedroom_echo",
            "whispered": True,
        }
    }
    ctx = gw._voice_context_from(req, body)
    assert ctx is not None
    assert ctx["area"] == "Bedroom"
    assert ctx["satellite"] == "assist_satellite.bedroom_echo"
    assert ctx["whispered"] is True

    # Non-boolean / falsey values must not trigger whisper mode.
    body_false = {"voice_context": {"area": "Bedroom", "whispered": False}}
    ctx_false = gw._voice_context_from(req, body_false)
    assert ctx_false is not None
    assert "whispered" not in ctx_false


def test_voice_context_note_adds_whisper_instruction_only_when_whispered() -> None:
    note_whisper = gw._voice_context_note({"area": "Bedroom", "whispered": True})
    assert "whisper" in note_whisper.lower()
    assert "quiet" in note_whisper.lower()

    note_normal = gw._voice_context_note({"area": "Bedroom"})
    assert "whisper" not in note_normal.lower()

    # Even with no area, whispered alone still emits a voice context note.
    note_bare = gw._voice_context_note({"whispered": True})
    assert "[Voice context]" in note_bare
    assert "whisper" in note_bare.lower()


@pytest.mark.asyncio
async def test_fast_drop_in_answer_start_end_nonmatch_and_error(monkeypatch) -> None:
    monkeypatch.setattr(gw, "EXECUTION_SVC", "http://execution:8002")
    monkeypatch.setattr(gw, "INTERNAL_SECRET", "s3cr3t")

    calls: list[dict] = []

    class _FakeResp:
        def __init__(self, status: int, body: dict) -> None:
            self.status = status
            self._body = body

        async def json(self, content_type=None) -> dict:
            return self._body

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeClient:
        def __init__(self, status: int = 200, body: dict | None = None, boom: Exception | None = None) -> None:
            self._status = status
            self._body = body or {"status": "SUCCESS", "message": "Connected to Kitchen Echo."}
            self._boom = boom

        def post(self, url: str, *, headers=None, json=None, timeout=None):
            calls.append({"url": url, "headers": dict(headers or {}), "json": dict(json or {})})
            if self._boom is not None:
                raise self._boom
            return _FakeResp(self._status, self._body)

    @asynccontextmanager
    async def _borrow(client):
        yield client

    creds = {
        "user": "jeremiah",
        "role": "admin",
        "family_role": "father",
        "ha_url": "http://homehub:8123",
        "ha_token": "tok",
    }

    # 1. Start drop-in
    monkeypatch.setattr(gw, "borrow_http_client", lambda: _borrow(_FakeClient()))
    ans = await gw._fast_drop_in_answer(
        "drop in on kitchen",
        creds,
        {"satellite": "assist_satellite.office_echo"},
    )
    assert ans == "Connected to Kitchen Echo."
    assert len(calls) == 1
    assert calls[0]["url"] == "http://execution:8002/execute/drop_in"
    assert calls[0]["headers"]["X-Internal-Secret"] == "s3cr3t"
    assert calls[0]["json"]["action"] == "start"
    assert calls[0]["json"]["target_room"] == "kitchen"
    assert calls[0]["json"]["user_context"]["voice_satellite"] == "assist_satellite.office_echo"

    # 2. End drop-in
    monkeypatch.setattr(
        gw,
        "borrow_http_client",
        lambda: _borrow(_FakeClient(body={"status": "SUCCESS", "message": "Hung up."})),
    )
    ans_end = await gw._fast_drop_in_answer("hang up", creds, {})
    assert ans_end == "Hung up."
    assert calls[-1]["json"]["action"] == "end"

    # 3. Non-drop-in query returns None with zero HTTP calls
    before = len(calls)
    assert await gw._fast_drop_in_answer("what is the weather today", creds, {}) is None
    assert len(calls) == before

    # 4. Execution HTTP 500 or network error surfaces cleanly without raising
    monkeypatch.setattr(gw, "borrow_http_client", lambda: _borrow(_FakeClient(status=500, body={})))
    assert await gw._fast_drop_in_answer("drop in on kitchen", creds, {}) == "Could not reach Drop In right now."

    monkeypatch.setattr(
        gw,
        "borrow_http_client",
        lambda: _borrow(_FakeClient(boom=RuntimeError("connection refused"))),
    )
    assert await gw._fast_drop_in_answer("drop in on kitchen", creds, {}) == "Could not reach Drop In right now."
