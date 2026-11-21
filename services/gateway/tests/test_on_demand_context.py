"""On-demand context on the single-turn path: the map in the system message, the
tools that read it, and the prompt-size record of every request."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from services.gateway import knowledge_map as km
from services.gateway import orchestrator, prompt_budget
from services.gateway import tool_registry as tr
from services.gateway.config import RAG_HIT_MAX_CHARS, RAG_SEARCH_MIN_SIMILARITY

CREDS = orchestrator.ResolvedCredentials(user="u")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    prompt_budget.reset()
    km._cached_map.cache_clear()
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "## Available Tools\n### BibleRequest\nBible.")
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"rag_svc_url": "http://rag"}))
    yield
    km._cached_map.cache_clear()


def _capture(monkeypatch, replies):
    sent: list[list[dict]] = []
    it = iter(replies)

    async def fake_ollama(payload, use_chat=True):
        sent.append([dict(m) for m in payload["messages"]])
        return {"message": {"content": next(it)}}

    monkeypatch.setattr(orchestrator, "call_ollama", fake_ollama)
    return sent


async def test_the_system_message_carries_the_map_and_stays_the_same(monkeypatch):
    sent = _capture(monkeypatch, ["One.", "Two."])
    await orchestrator._single_turn_inference("verse of the day?", "m", "PERSONA", "", [], CREDS)
    await orchestrator._single_turn_inference("turn on the porch light", "m", "PERSONA", "DEVICES", [], CREDS)
    first, second = sent[0][0]["content"], sent[1][0]["content"]
    assert first == second
    assert "## Knowledge map" in first
    assert first.index("PERSONA") < first.index("## Available Tools") < first.index("## Knowledge map")
    assert "DEVICES" not in first


async def test_a_grounded_turn_gets_no_map(monkeypatch):
    sent = _capture(monkeypatch, ["Answer."])
    await orchestrator._single_turn_inference("q", "m", "PERSONA", "PASSAGES", [], CREDS, grounded=True)
    assert "## Knowledge map" not in sent[0][0]["content"]


async def test_every_request_records_its_prompt_by_section(monkeypatch):
    _capture(monkeypatch, ['```json\n{"tool": "BibleRequest", "action": "votd"}\n```', "John 3:16."])
    monkeypatch.setattr(orchestrator, "_execute_single_tool", AsyncMock(return_value="VOTD: John 3:16"))
    await orchestrator._single_turn_inference(
        "verse of the day?", "m", "PERSONA", "", [{"role": "user", "content": "hi"}], CREDS,
        budget_path="voice", context_sections={},
    )
    rec = prompt_budget.snapshot()["recent"][0]
    assert rec["path"] == "voice"
    s = rec["sections"]
    assert s["system"] == len("PERSONA") and s["knowledge_map"] > 0 and s["tool_guide"] > 0
    assert s["history"] == 2
    assert s["tool_results"] == len("VOTD: John 3:16")
    assert not any(k.startswith("prefetch") for k in s)
    assert rec["per_request_chars"] < rec["total_chars"]


async def test_prefetched_sections_are_recorded_by_collection(monkeypatch):
    _capture(monkeypatch, ["Done."])
    await orchestrator._single_turn_inference(
        "lights", "m", "P", "[HOME_DEVICES]\n- Device: Lamp", [], CREDS,
        context_sections={"prefetch:home_devices": 30},
    )
    assert prompt_budget.snapshot()["recent"][0]["sections"]["prefetch:home_devices"] == 30


# --- ContextSearchRequest: a few close hits, compact, and a clear failure ----------------

class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        import json

        return json.dumps(self._payload)


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.sent: list[dict] = []

    async def post(self, url, json=None, **kwargs):
        self.sent.append(json)
        return self.resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


async def _search(payload, resp):
    client = _Client(resp)
    with patch("services.gateway.main.shared_http_client", return_value=client):
        out = await orchestrator._execute_single_tool("ContextSearchRequest", payload, "q", CREDS)
    return out, client.sent[0] if client.sent else None


async def test_a_knowledge_search_asks_for_three_close_hits_by_default():
    _out, sent = await _search({"collection_name": "system_learnings", "query": "push fails"},
                               _Resp(200, {"results": []}))
    assert sent["k"] == tr.KNOWLEDGE_SEARCH_DEFAULT_K == 3
    assert sent["min_similarity"] == RAG_SEARCH_MIN_SIMILARITY


async def test_a_larger_k_is_capped():
    _out, sent = await _search({"collection_name": "nextcloud_files", "query": "x", "k": 50},
                               _Resp(200, {"results": []}))
    assert sent["k"] == tr.KNOWLEDGE_SEARCH_MAX_K


async def test_hits_come_back_cut_to_size_and_attributed():
    long = "word " * 400
    out, _ = await _search(
        {"collection_name": "nextcloud_files", "query": "soup"},
        _Resp(200, {"results": [{"content": long, "metadata": {"title": "Soup.md"}}]}),
    )
    assert "Found 1 relevant results in nextcloud_files" in out
    assert "[cut]" in out and "(Source: Soup.md)" in out
    assert len(out) < RAG_HIT_MAX_CHARS + 200


async def test_nothing_close_enough_says_so_and_points_back_to_the_map():
    out, _ = await _search({"collection_name": "user_facts", "query": "x"}, _Resp(200, {"results": []}))
    assert "Nothing in user_facts matched closely enough" in out
    assert "knowledge map" in out


async def test_a_search_backend_that_is_down_is_reported_not_hidden():
    out, _ = await _search({"collection_name": "nextcloud_files", "query": "x"},
                           _Resp(503, {"detail": "Search failed: OperationalError: disk I/O error"}))
    assert out.startswith("Sorry, I couldn't complete that action")
    assert "Search failed" in out


# --- garden tools run in-process on the chat path -----------------------------------------

async def test_garden_summary_runs_as_the_turns_user(monkeypatch):
    from services.gateway import garden_assist

    summarize = AsyncMock(return_value={"status": "ok", "message": "Water the basil.", "due": 1})
    monkeypatch.setattr(garden_assist, "summarize", summarize)
    out = await orchestrator._execute_single_tool("garden_summary", {"tool": "garden_summary"}, "q", CREDS)
    assert out.startswith("Water the basil.")
    assert summarize.await_args.args[0] is CREDS


async def test_garden_log_care_keeps_its_care_verb(monkeypatch):
    from services.gateway import garden_assist

    sent = _capture(monkeypatch, ['```json\n{"tool": "garden_log_care", "target": "basil", "action": "mist"}\n```', "Misted."])
    log_care = AsyncMock(return_value={"status": "logged", "message": "Misted the basil."})
    monkeypatch.setattr(garden_assist, "log_care", log_care)
    await orchestrator._single_turn_inference("mist the basil", "m", "P", "", [], CREDS)
    assert log_care.await_args.args[1] == "basil"
    assert log_care.await_args.kwargs["action"] == "mist"
    assert "Misted the basil." in sent[1][-1]["content"]


async def test_a_garden_harvest_needs_a_quantity():
    out = await orchestrator._execute_single_tool("garden_log_harvest", {"target": "tomato"}, "q", CREDS)
    assert out == "A harvest needs a quantity."


# --- the admin views -----------------------------------------------------------------------

def _client(monkeypatch, admin: bool):
    from fastapi import HTTPException

    from services.gateway import main

    async def require_admin(request):
        if not admin:
            raise HTTPException(status_code=403, detail="Admins only")
        return "admin"

    monkeypatch.setattr(main, "_require_admin", require_admin)
    # The routes alone: the gateway's middleware (identity, rate limits) is not what is tested.
    from fastapi import FastAPI

    from services.gateway import context_routes

    app = FastAPI()
    app.include_router(context_routes.router)
    return TestClient(app)


def test_the_prompt_budget_endpoint_is_admin_only(monkeypatch):
    assert _client(monkeypatch, admin=False).get("/api/admin/prompt-budget").status_code == 403


def test_the_prompt_budget_endpoint_shows_aggregates_by_path_and_section(monkeypatch):
    prompt_budget.record("voice", {"system": 100, "knowledge_map": 50, "prefetch:home_devices": 40, "question": 10})
    prompt_budget.record("voice", {"system": 100, "knowledge_map": 50, "question": 30})
    body = _client(monkeypatch, admin=True).get("/api/admin/prompt-budget").json()
    voice = body["paths"]["voice"]
    assert voice["requests"] == 2
    assert voice["sections"]["prefetch:home_devices"]["max"] == 40
    assert voice["sections"]["prefetch:home_devices"]["p50"] == 20
    assert voice["per_request_chars"]["max"] == 50
    assert body["est_chars_per_token"] == 4
    assert _client(monkeypatch, admin=True).delete("/api/admin/prompt-budget").json() == {"status": "SUCCESS"}
    assert prompt_budget.snapshot()["window"] == 0


def test_the_knowledge_map_endpoint_shows_the_text_a_model_sees(monkeypatch):
    from services.gateway import prompts

    monkeypatch.setattr(prompts, "load_prompt_sync", lambda name: "## Available Tools\n")
    body = _client(monkeypatch, admin=True).get("/api/admin/knowledge-map?surface=raven&admin=true").json()
    assert body["surface"] == "raven" and body["map"].startswith("## Knowledge map")
    assert body["chars"] == len(body["map"])
    assert _client(monkeypatch, admin=True).get("/api/admin/knowledge-map?surface=x").status_code == 422
