"""The intent-gated pre-fetch: only what the question names, a little, above a floor.

Production, before: "What's the verse of the day?" carried 15 home_devices, 15
system_capabilities and 15 system_learnings rows (about 4,900 characters), none
used. Now a question pre-fetches only from collections its intent or wording
routes to, ``RAG_PREFETCH_K`` hits each above ``RAG_PREFETCH_MIN_SIMILARITY``,
de-duplicated, inside a character budget; everything else is fetched on demand
through the tools the knowledge map names.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.gateway import context_router, orchestrator, prompt_budget
from services.gateway.config import (
    RAG_PREFETCH_K,
    RAG_PREFETCH_MAX_CHARS,
    RAG_PREFETCH_MIN_SIMILARITY,
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    from services.common.circuit_breaker import reset_breakers

    reset_breakers()
    prompt_budget.reset()
    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"rag_svc_url": "http://rag"}))
    yield
    reset_breakers()


@pytest.fixture
def hub(monkeypatch):
    from services import config

    monkeypatch.setattr(config, "any_home_hub", lambda: True)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self):
        return self._payload


class _Session:
    def __init__(self, hits_by_collection):
        self.hits = hits_by_collection
        self.searches: list[dict] = []

    async def post(self, url, json=None, **kwargs):
        if url.endswith("/rag/search"):
            self.searches.append(json)
            return _Resp({"results": list(self.hits.get(json["collection_name"], []))})
        return _Resp({})

    async def get(self, url, **kwargs):
        return _Resp({})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _hit(text, **meta):
    return {"content": text, "metadata": meta, "similarity": 0.7}


async def _fetch(session, query, **kwargs):
    with patch("services.gateway.main.shared_http_client", return_value=session):
        return await orchestrator._fetch_rag_context(query, "u", None, include_curriculum=False, **kwargs)


# --- routing ------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "What's the verse of the day?",
    "tell me a joke",
    "who is the president of France",
    "what's on my calendar tomorrow",
    "where is everyone",
])
def test_a_question_that_names_no_source_pre_fetches_nothing(question, hub):
    assert orchestrator._collections_for_query(question) == []


@pytest.mark.parametrize("question, expected", [
    ("turn on the porch light", ["home_devices"]),
    ("is the back door locked", ["home_devices"]),
    ("find my budget spreadsheet", ["nextcloud_files"]),
    ("how do I make the chicken soup recipe", ["nextcloud_files"]),
    ("what can you do", ["system_capabilities"]),
    ("why does the git push in my workspace fail", ["system_learnings"]),
    ("what do you remember about my allergies", ["user_facts"]),
])
def test_a_question_pre_fetches_only_the_source_it_names(question, expected, hub):
    assert orchestrator._collections_for_query(question) == expected


def test_a_command_starting_can_you_is_not_a_capability_question(hub):
    assert orchestrator._collections_for_query("can you turn off the fan") == ["home_devices"]


def test_a_confident_intent_routes_even_without_the_words(hub):
    assert orchestrator._collections_for_query("make it brighter", intent="turn_on", confidence=0.9) == ["home_devices"]
    assert orchestrator._collections_for_query("make it brighter", intent="turn_on", confidence=0.3) == []
    assert orchestrator._collections_for_query("the quarterly numbers", intent="storage_search", confidence=0.8) \
        == ["nextcloud_files"]


def test_the_verse_of_the_day_never_summons_book_passages(hub):
    assert context_router.tool_answers("What's the verse of the day?")
    # The library still answers a passage question when the caller asked for books.
    assert orchestrator._collections_for_query("verse of the day", include_library=True) == ["calibre_files"]


# --- the fetch itself ------------------------------------------------------------------

async def test_an_unrouted_question_sends_no_search_at_all(hub):
    session = _Session({"home_devices": [_hit("Device: Porch Light")]})
    sections: dict[str, int] = {}
    assert await _fetch(session, "What's the verse of the day?", sections=sections) == ""
    assert session.searches == []
    assert sections == {}


async def test_each_search_asks_for_a_few_hits_above_the_floor(hub):
    session = _Session({"home_devices": [_hit("Device: Porch Light (ID: light.porch)")]})
    await _fetch(session, "turn on the porch light")
    assert session.searches == [{
        "collection_name": "home_devices", "query": "turn on the porch light", "user_id": "u",
        "k": RAG_PREFETCH_K, "min_similarity": RAG_PREFETCH_MIN_SIMILARITY,
    }]
    assert RAG_PREFETCH_K <= 3


async def test_the_same_row_from_two_collections_is_included_once(hub):
    row = _hit("Recipes/Soup.md: simmer the chicken with onion")
    session = _Session({"nextcloud_files": [row], "system_capabilities": [row]})
    ctx = await _fetch(session, "what can you do with the soup recipe file")
    assert ctx.count("simmer the chicken") == 1


async def test_the_character_budget_holds_across_collections(hub):
    big = [_hit(f"Device {i}: " + "x" * 500) for i in range(3)]
    session = _Session({"home_devices": big, "nextcloud_files": big})
    ctx = await _fetch(session, "the files about the lights", search_chars=700)
    assert len(ctx) < 700 + 200  # headers and citations ride on top of the hits


async def test_the_default_budget_is_small(hub):
    assert RAG_PREFETCH_MAX_CHARS <= 2000
    big = [_hit("Device: " + "y" * 590) for _ in range(3)]
    session = _Session({"home_devices": big})
    ctx = await _fetch(session, "turn on the light")
    assert len(ctx) <= RAG_PREFETCH_MAX_CHARS + 200


async def test_sections_record_what_each_collection_contributed(hub):
    session = _Session({"home_devices": [_hit("Device: Den TV")], "nextcloud_files": [_hit("Manuals/TV.pdf")]})
    sections: dict[str, int] = {}
    ctx = await _fetch(session, "find the tv manual file", sections=sections)
    assert set(sections) == {"prefetch:home_devices", "prefetch:nextcloud_files"}
    # Each block's characters, including its own header and line breaks (stripped at the ends).
    assert sum(sections.values()) - len(ctx) in (1, 2)


async def test_a_voice_turn_passes_its_intent_and_a_small_budget(monkeypatch):
    seen = {}

    async def fake_fetch(query, user_id, creds=None, workspace_id=None, **kwargs):
        seen.update(kwargs)
        return ""

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_fetch)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", AsyncMock(return_value="ok"))
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=[]))
    await orchestrator.process_full_orchestration({
        "query": "lights off", "creds": {"user": "u"}, "model": "m",
        "voice_context": {"satellite": "assist_satellite.test"}, "intent": "turn_off", "intent_confidence": 0.97,
    })
    assert seen["intent"] == "turn_off" and seen["confidence"] == pytest.approx(0.97)
    assert seen["search_chars"] == orchestrator.VOICE_RAG_CHARS <= 1000
    assert seen["devices_first"] is True


async def test_a_raven_mission_records_its_prompt_size(monkeypatch):
    import services.gateway.agent_loop as agent_loop

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    monkeypatch.setattr(agent_loop, "AgentLoop", AsyncMock(return_value="done"))
    await orchestrator.process_full_orchestration({
        "query": "raven build a script", "creds": {"user": "u"}, "model": "m", "_mission_id": 7,
        "system": "RAVEN PROTOCOL",
    })
    snap = prompt_budget.snapshot()
    assert snap["paths"]["raven"]["requests"] == 1
    assert snap["recent"][0]["sections"]["system"] == len("RAVEN PROTOCOL")
