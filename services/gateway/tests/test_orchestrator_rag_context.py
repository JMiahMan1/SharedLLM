"""RAG context limits for single-turn inference.

The family chat's @Jarvis path has no conversation history, but the
always-on curriculum can push the retrieved context past an 8k-token
model window. These tests pin the hard cap that keeps the prompt inside
the loaded context.
"""

from unittest.mock import AsyncMock

from services.gateway import orchestrator

_MARKER = "\n\n[Retrieved context truncated to fit the model window.]"


def test_short_context_is_unchanged():
    context = "a short retrieved passage"
    assert orchestrator._truncate_rag_context(context) == context


def test_long_context_is_capped_with_a_marker():
    context = "x" * (orchestrator.MAX_RAG_CONTEXT_CHARS + 500)

    result = orchestrator._truncate_rag_context(context)

    assert result.endswith(_MARKER)
    assert result[: orchestrator.MAX_RAG_CONTEXT_CHARS] == context[: orchestrator.MAX_RAG_CONTEXT_CHARS]
    assert len(result) <= orchestrator.MAX_RAG_CONTEXT_CHARS + len(_MARKER)


async def test_chat_orchestration_can_skip_the_curriculum(monkeypatch):
    seen = {}

    async def fake_fetch(
        query,
        user_id,
        creds=None,
        workspace_id=None,
        *,
        include_curriculum=True,
        include_library=False,
    ):
        seen["include_curriculum"] = include_curriculum
        return "retrieved context"

    async def fake_inference(*args, **kwargs):
        return "answer"

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_fetch)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=[]))

    answer = await orchestrator.process_full_orchestration(
        {
            "query": "hello",
            "creds": {"user": "u"},
            "model": "m",
            "include_curriculum": False,
        }
    )

    assert answer == "answer"
    assert seen["include_curriculum"] is False


async def test_repeated_rag_failures_skip_further_searches(monkeypatch):
    """RAG is optional: three failures open the breaker, not thirty timeouts."""
    from contextlib import asynccontextmanager

    import aiohttp

    from services.common.circuit_breaker import reset_breakers
    from services.gateway import main as gateway_main

    reset_breakers()
    calls = 0

    class Client:
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            raise aiohttp.ClientError("rag is down")

    @asynccontextmanager
    async def fake_client():
        yield Client()

    async def fake_settings():
        return {"rag_svc_url": "http://rag"}

    monkeypatch.setattr(orchestrator, "get_all_settings", fake_settings)
    monkeypatch.setattr(orchestrator, "_collections_for_query", lambda *a, **k: ["system_learnings"])
    monkeypatch.setattr(gateway_main, "shared_http_client", fake_client)

    for _ in range(4):
        assert await orchestrator._fetch_rag_context("hello", "u") == ""

    assert calls == 3


async def test_a_voice_turn_reads_a_short_context_with_devices_first(monkeypatch):
    seen = {}

    async def fake_fetch(query, user_id, creds=None, workspace_id=None, **kwargs):
        seen.update(kwargs)
        return "retrieved context"

    async def fake_inference(*args, **kwargs):
        return "answer"

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_fetch)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=[]))

    await orchestrator.process_full_orchestration({
        "query": "set the thermostat to 70", "creds": {"user": "u"}, "model": "m",
        "voice_context": {"satellite": "assist_satellite.test_speaker"},
    })
    assert seen["include_curriculum"] is False
    assert seen["search_chars"] == orchestrator.VOICE_RAG_CHARS
    assert seen["max_hits"] == orchestrator.VOICE_RAG_HITS
    assert seen["devices_first"] is True

    seen.clear()
    await orchestrator.process_full_orchestration({"query": "hello", "creds": {"user": "u"}, "model": "m"})
    assert seen["include_curriculum"] is True and "search_chars" not in seen


def test_devices_come_first_for_a_voice_turn_even_when_it_sounds_like_coding():
    assert orchestrator._collections_for_query("fix the thermostat")[0] != "ha_entities"
    assert orchestrator._collections_for_query("fix the thermostat", devices_first=True)[0] == "ha_entities"


async def test_the_tool_guide_comes_before_the_time_so_the_prompt_start_is_reused(monkeypatch):
    sent = {}

    async def fake_ollama(payload, use_chat=True):
        sent["system"] = payload["messages"][0]["content"]
        return {"message": {"content": "Done."}}

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE: LightControlRequest, ClimateRequest")
    monkeypatch.setattr(orchestrator, "call_ollama", fake_ollama)
    creds = orchestrator.ResolvedCredentials(user="u")
    await orchestrator._single_turn_inference("turn on the lamp", "m", "PERSONA", "DEVICES", [], creds)
    system = sent["system"]
    assert system.index("PERSONA") < system.index("TOOL GUIDE") < system.index("Current Date/Time") < system.index("DEVICES")
    assert "ClimateRequest" in system and "LightControlRequest" in system
