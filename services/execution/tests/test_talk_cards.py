import os
import sys
from types import SimpleNamespace

import pytest

os.environ["INTERNAL_SECRET"] = "test-secret"
sys.path.insert(0, os.path.abspath("."))


def _request(**kw):
    from services.execution.schemas import TalkRequest

    base = {"user_context": {"user": "dad"}, "action": "post_card", "token": "room-1"}
    base.update(kw)
    return TalkRequest(**base)


@pytest.fixture(autouse=True)
def _provider(monkeypatch):
    import services.execution.handlers.talk as talk

    monkeypatch.setattr(
        talk, "resolve_personal_data_provider", lambda ctx: SimpleNamespace(username="dad", password="x")
    )
    return talk


@pytest.mark.asyncio
async def test_card_is_posted_with_readable_text_and_json(monkeypatch):
    import services.execution.handlers.talk as talk

    sent = {}

    async def fake_request(provider, method, endpoint, **kwargs):
        sent["data"] = kwargs.get("data")
        return True, {}, ""

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)

    result = await talk.handle_talk(
        _request(
            message="Nice one!",
            card_kind="activity",
            card_title="First 10k steps",
            card_detail="Mom hit her goal",
            card_stars=2,
            card_stats=[{"label": "Steps", "value": "10,240"}, {"nope": 1}],
        )
    )

    assert result.status == "SUCCESS"
    wire = sent["data"]["message"]
    assert wire.startswith("Nice one!")
    assert "```jarvis-envelope" in wire
    assert result.detail["card"]["stars"] == 2


@pytest.mark.asyncio
async def test_unknown_card_kind_is_rejected(monkeypatch):
    import services.execution.handlers.talk as talk

    async def fail(*a, **k):
        raise AssertionError("must not post an unknown card kind")

    monkeypatch.setattr(talk, "_talk_request_with_retry", fail)

    result = await talk.handle_talk(_request(card_kind="hologram", card_title="x"))
    assert result.status == "FAILURE"
    assert "card_kind must be one of" in result.message


@pytest.mark.asyncio
async def test_card_requires_a_title_and_a_token(monkeypatch):
    import services.execution.handlers.talk as talk

    result = await talk.handle_talk(_request(card_kind="game", card_title="  "))
    assert result.status == "FAILURE"
    assert "card_title" in result.message

    result = await talk.handle_talk(
        _request(token=None, card_kind="game", card_title="Quiz night")
    )
    assert result.status == "FAILURE"
    assert "token" in result.message.lower()


@pytest.mark.asyncio
async def test_upstream_failure_is_reported(monkeypatch):
    import services.execution.handlers.talk as talk

    async def fake_request(provider, method, endpoint, **kwargs):
        return False, None, "Talk said no"

    monkeypatch.setattr(talk, "_talk_request_with_retry", fake_request)

    result = await talk.handle_talk(_request(card_kind="game", card_title="Quiz night"))
    assert result.status == "FAILURE"
    assert "Talk said no" in result.message


def test_encoder_drops_malformed_stats():
    import json

    from services.execution.handlers.talk import _encode_envelope

    wire = _encode_envelope(
        "hi",
        {"kind": "activity", "title": "t", "stats": [{"label": "a", "value": "1"}, {"bad": True}]},
    )
    body = json.loads(wire.split("```jarvis-envelope")[1].strip())
    assert body["stats"] == [{"label": "a", "value": "1"}]
