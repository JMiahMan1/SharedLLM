"""Saying "learn my voice" opens a short session only for a recognised voice."""
import json

import pytest

import services.gateway.cache as gateway_cache
import services.gateway.main as gateway_main
from services.common import voice_learning
from services.gateway.schemas import ResolvedCredentials


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None):
        self.values[key] = (value, ex)

    def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0


def test_the_phrases_are_the_imperative_ones_not_questions():
    assert gateway_main._wants_voice_learning("Hey Jarvis, learn my voice")
    assert gateway_main._wants_voice_learning("Can you update my voice signature?")
    assert gateway_main._wants_voice_learning("start voice learning")
    assert not gateway_main._wants_voice_learning("How do I update my voice?")
    assert not gateway_main._wants_voice_learning("Tell me how to train my voice")
    assert not gateway_main._wants_voice_learning("What is voice learning mode?")
    assert gateway_main._wants_voice_learning_stop("stop learning my voice")


def test_starting_and_stopping_uses_the_shared_redis_key(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: redis)

    assert gateway_main._start_voice_learning("michele") is True
    key = voice_learning.key("michele")
    value, ttl = redis.values[key]
    assert value == str(voice_learning.TARGET_SAMPLES)
    assert ttl == voice_learning.TTL_SECONDS

    assert gateway_main._stop_voice_learning("michele") is True
    assert key not in redis.values
    assert gateway_main._stop_voice_learning("michele") is False


@pytest.mark.asyncio
async def test_a_recognised_voice_is_told_how_the_session_works(monkeypatch):
    started = []
    monkeypatch.setattr(gateway_main, "_start_voice_learning", lambda who: started.append(who) or True)
    monkeypatch.setattr(gateway_main, "update_history", _no_history)
    creds = ResolvedCredentials(user="michele", identified_by="voice")

    response = await gateway_main._handle_voice_learning(
        "learn my voice", creds, "michele", "assistant", False
    )

    assert started == ["michele"]
    assert "next three voice commands" in _message(response)


@pytest.mark.asyncio
async def test_an_unrecognised_voice_is_sent_to_settings(monkeypatch):
    monkeypatch.setattr(gateway_main, "_start_voice_learning", lambda who: pytest.fail("must not start"))
    monkeypatch.setattr(gateway_main, "update_history", _no_history)
    creds = ResolvedCredentials(user="michele")

    response = await gateway_main._handle_voice_learning(
        "learn my voice", creds, "michele", "assistant", False
    )

    reply = _message(response)
    assert "first three samples" in reply
    assert "longer phrase" in reply


@pytest.mark.asyncio
async def test_stopping_says_whether_a_session_was_open(monkeypatch):
    monkeypatch.setattr(gateway_main, "_stop_voice_learning", lambda who: True)
    monkeypatch.setattr(gateway_main, "update_history", _no_history)
    creds = ResolvedCredentials(user="michele", identified_by="voice")

    response = await gateway_main._handle_voice_learning(
        "stop learning my voice", creds, "michele", "assistant", False
    )

    assert "stopped learning" in _message(response)


async def _no_history(*_args, **_kwargs):
    return None


def _message(response) -> str:
    return json.loads(response.body)["message"]["content"]
