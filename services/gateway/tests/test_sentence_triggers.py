"""Chat and voice turns ask the device hub's sentence triggers first (services/gateway/sentence_triggers.py)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import sentence_triggers


class _Ctx:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc = resp, exc

    async def __aenter__(self):
        if self.exc:
            raise self.exc
        return self.resp

    async def __aexit__(self, *a):
        return False


def _resp(status, body):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=body)
    m.text = AsyncMock(return_value=json.dumps(body))
    m.release = AsyncMock()
    m.raise_for_status = MagicMock()
    return m


def _session(hub_status=200, hub_body=None, hub_exc=None, calls=None):
    calls = calls if calls is not None else []

    def post(url, **kw):
        calls.append((url, kw))
        if url.endswith("/hub/conversation/triggers"):
            return _Ctx(_resp(hub_status, hub_body), hub_exc)
        raise AssertionError(f"Jarvis must not be asked when a sentence matched: {url}")

    sess = MagicMock()
    sess.post = MagicMock(side_effect=post)
    sess.get = MagicMock(side_effect=lambda url, **kw: _Ctx(_resp(200, {})))
    return sess


async def test_a_matched_sentence_returns_the_automation_reply(monkeypatch):
    calls: list = []
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _session(
        hub_body={"matched": True, "response": "Movie time!", "automations": ["automation.movie"]}, calls=calls))
    out = await sentence_triggers.ask_hub("Movie time", {"satellite": "assist_satellite.den", "conversation_id": "c1"})
    assert out == {"response": "Movie time!", "automations": ["automation.movie"]}
    url, kw = calls[0]
    assert url.endswith("/hub/conversation/triggers")
    assert kw["json"] == {"text": "Movie time", "satellite_id": "assist_satellite.den", "conversation_id": "c1"}
    assert kw["headers"]["Authorization"].startswith("Bearer ")


async def test_no_match_lets_jarvis_answer(monkeypatch):
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _session(hub_body={"matched": False}))
    assert await sentence_triggers.ask_hub("what is the weather") is None


async def test_a_match_without_text_still_counts_as_handled(monkeypatch):
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _session(hub_body={"matched": True, "response": ""}))
    assert (await sentence_triggers.ask_hub("go"))["response"] == "Done"


@pytest.mark.parametrize("kw", [
    {"hub_exc": aiohttp.ClientConnectionError("refused")},
    {"hub_exc": TimeoutError()},
    {"hub_status": 503, "hub_body": {"detail": "the automation engine is not running yet"}},
    {"hub_status": 200, "hub_body": ["not", "a", "dict"]},
])
async def test_a_hub_that_cannot_answer_is_logged_and_jarvis_answers(monkeypatch, caplog, kw):
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _session(**kw))
    assert await sentence_triggers.ask_hub("movie time") is None
    assert any("Jarvis answers this turn" in r.message for r in caplog.records)


def test_chat_answers_a_matched_sentence_without_a_model(monkeypatch):
    sess = _session(hub_body={"matched": True, "response": "Lights are set for the film.",
                              "automations": ["automation.movie"]})
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    history: list = []

    async def update_history(user, role, text):
        history.append((user, role, text))

    monkeypatch.setattr(gateway_main, "update_history", update_history)
    monkeypatch.setattr(gateway_main, "_recognised_speaker", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": "sam"}))
    r = TestClient(gateway_main.app).post("/api/chat", json={"query": "movie time", "model": "any-model",
                                                              "stream": False},
                                          headers={"Authorization": "Bearer made-up-key"})
    assert r.status_code == 200
    assert r.json()["message"]["content"] == "Lights are set for the film."
    assert history == [("sam", "user", "movie time"), ("sam", "assistant", "Lights are set for the film.")]
