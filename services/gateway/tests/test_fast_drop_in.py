"""Tier-1 Drop In FastPath: unambiguous commands only."""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from services.gateway import main as gw_main
from services.gateway.fast_drop_in import parse_drop_in_command
from services.gateway.schemas import ResolvedCredentials


@pytest.mark.parametrize(
    ("query", "target"),
    [
        ("drop in on the kitchen", "kitchen"),
        ("drop in to the den", "den"),
        ("drop into the office", "office"),
        ("drop in kitchen", "kitchen"),
        ("intercom the living room", "living room"),
        ("Intercom office.", "office"),
        ("  drop   in   on   the   kitchen.  ", "kitchen"),
        ("drop in on room_of:alex", "room_of:alex"),
    ],
)
def test_start_phrases(query: str, target: str):
    assert parse_drop_in_command(query) == {
        "tool": "DropInRequest",
        "payload": {"action": "start", "target_room": target},
    }


@pytest.mark.parametrize(
    "query",
    [
        "hang up",
        "hang up the phone",
        "end drop in",
        "end the drop in",
        "stop drop in",
        "stop the drop-in",
        "end the call",
        "end this call",
        "Hang up!",
    ],
)
def test_end_phrases(query: str):
    assert parse_drop_in_command(query) == {
        "tool": "DropInRequest",
        "payload": {"action": "end"},
    }


@pytest.mark.parametrize(
    "query",
    [
        "drop in the laundry",
        "announce dinner is ready",
        "tell the kitchen dinner is ready",
        "intercom the kitchen dinner is ready",
        "intercom the kitchen and say dinner is ready",
        "drop in on the kitchen and tell them dinner is ready",
        "drop in on the kitchen to say dinner is ready",
        "drop in",
        "drop in on",
        "intercom",
        "turn off the kitchen lights",
        "what is the weather",
        "",
        "   ",
    ],
)
def test_non_drop_in_phrases_are_left_to_the_model(query: str):
    assert parse_drop_in_command(query) is None


@pytest.mark.asyncio
async def test_fast_drop_in_answer_with_pydantic_credentials(monkeypatch) -> None:
    """ResolvedCredentials is a pydantic model, not a dict, so creds.get() must not be called."""

    class _FakeResp:
        def __init__(self) -> None:
            self.status = 200

        async def json(self, content_type=None) -> dict:
            return {"message": "Dropping in on the kitchen."}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeClient:
        def post(self, url, **kwargs):
            return _FakeResp()

    @asynccontextmanager
    async def _borrow(client):
        yield client

    monkeypatch.setattr(gw_main, "borrow_http_client", lambda: _borrow(_FakeClient()))
    monkeypatch.setattr(gw_main, "EXECUTION_SVC", "http://execution:8002")
    monkeypatch.setattr(gw_main, "INTERNAL_SECRET", "s3cr3t")

    creds = ResolvedCredentials(user="someone")
    result = await gw_main._fast_drop_in_answer("drop in on the kitchen", creds, None)
    assert result == "Dropping in on the kitchen."


@pytest.mark.asyncio
async def test_drop_in_forwards_who_is_asking_and_how(monkeypatch):
    """Execution limits a voice request by via/identified_by; the fast path must pass them on."""
    from contextlib import asynccontextmanager

    from services.gateway import main
    from services.gateway.schemas import ResolvedCredentials

    sent = {}

    class _Resp:
        status = 200

        async def json(self, content_type=None):
            return {"message": "ok"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def post(self, url, *, headers=None, json=None, timeout=None):
            sent.update(json or {})
            return _Resp()

    @asynccontextmanager
    async def _borrow():
        yield _Client()

    monkeypatch.setattr(main, "borrow_http_client", _borrow)
    creds = ResolvedCredentials(user="parent-a", is_admin=True, identified_by="voice", via="assistant")
    await main._fast_drop_in_answer("drop in on the kitchen", creds, {"satellite": "assist_satellite.den"})
    ctx = sent["user_context"]
    assert ctx["user"] == "parent-a" and ctx["is_admin"] is True
    assert ctx["identified_by"] == "voice" and ctx["via"] == "assistant"
    assert ctx["voice_satellite"] == "assist_satellite.den"
