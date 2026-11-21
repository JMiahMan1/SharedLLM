"""The chat handler no longer searches four collections fifteen deep for every question.

Before: every /api/chat turn posted /rag/search for home_devices, nextcloud_files,
system_capabilities and system_learnings with k=15, and an ordinary queued turn
threw the result away (only a Raven mission or the agentic path used it). Now it
searches only for those paths, routed like everything else, and hands the
classifier's verdict to the queued turn so the orchestrator routes its pre-fetch.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway import main as gateway_main
from services.gateway.tests.test_chat_storage_routing import _make_session, client, get_test_settings


@pytest.fixture
def queued(monkeypatch):
    payloads: list[dict] = []
    queue = MagicMock()

    async def enqueue_job(user_id, job_payload):
        payloads.append(job_payload)
        return "job-1"

    queue.enqueue_job = enqueue_job
    queue.get_job_status = AsyncMock(return_value={"status": "completed", "result": "ok"})
    queue.get_chunks = AsyncMock(return_value=["ok"])
    monkeypatch.setattr(gateway_main, "job_queue", queue)
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda q, m: "# System")
    monkeypatch.setattr(gateway_main.engine, "is_fast_path", lambda *a: False)
    return payloads


def _post(monkeypatch, query):
    sess = _make_session(get_test_settings(), iter([]))
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    with patch("aiohttp.ClientSession", return_value=sess), \
            patch("services.gateway.main.shared_http_client", return_value=sess):
        resp = client.post("/api/chat", json={"query": query, "stream": False},
                           headers={"Authorization": "Bearer test-token"})
    searches = [c for c in sess.post.call_args_list if "/rag/search" in str(c[0][0])]
    return resp, searches


def test_an_ordinary_question_is_queued_without_any_search_in_the_handler(monkeypatch, queued):
    monkeypatch.setattr(gateway_main.engine, "classify", lambda q: ("ha_status", 0.42))
    resp, searches = _post(monkeypatch, "What's the verse of the day?")
    assert resp.status_code == 200
    assert searches == []
    assert queued and queued[0]["intent"] == "ha_status"
    assert queued[0]["intent_confidence"] == pytest.approx(0.42)


def test_a_device_question_is_queued_with_its_intent_for_the_orchestrator(monkeypatch, queued):
    monkeypatch.setattr(gateway_main.engine, "classify", lambda q: ("turn_on", 0.93))
    _resp, searches = _post(monkeypatch, "brighten the den")
    assert searches == []  # the orchestrator pre-fetches, once, routed by this intent
    assert queued[0]["intent"] == "turn_on"
