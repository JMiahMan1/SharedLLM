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


async def test_the_system_message_is_the_same_for_every_question_so_it_stays_cached(monkeypatch):
    sent: list[list[dict]] = []

    async def fake_ollama(payload, use_chat=True):
        sent.append(payload["messages"])
        return {"message": {"content": "Done."}}

    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: "TOOL GUIDE: LightControlRequest, ClimateRequest")
    monkeypatch.setattr(orchestrator, "call_ollama", fake_ollama)
    creds = orchestrator.ResolvedCredentials(user="u")
    await orchestrator._single_turn_inference("turn on the lamp", "m", "PERSONA", "DEVICES", [], creds,
                                              turn_note="[Voice context]\nIn the den.")
    await orchestrator._single_turn_inference("what is the weather", "m", "PERSONA", "FORECAST", [], creds)
    first, second = sent
    # Persona then tool guide, and nothing that changes per question.
    system = first[0]["content"]
    assert system.index("PERSONA") < system.index("TOOL GUIDE")
    assert "ClimateRequest" in system and "LightControlRequest" in system
    assert first[0] == second[0]
    for part in ("Current Date/Time", "DEVICES", "Speaker:", "In the den"):
        assert part not in system
    # The question carries the time, the speaker, the note and the context, then the words.
    question = first[-1]["content"]
    assert first[-1]["role"] == "user"
    assert question.index("Current Date/Time") < question.index("Speaker: u.") < question.index("In the den") \
        < question.index("DEVICES") < question.index("turn on the lamp")
    assert question.endswith("[Question]\nturn on the lamp")


def test_an_unrecognised_speaker_is_never_given_a_name():
    line = orchestrator._speaker_line(orchestrator.ResolvedCredentials(user="household"))
    assert "not identified" in line and "Never guess or invent a name" in line
    named = orchestrator.ResolvedCredentials(user="robin", identified_by="voice")
    assert orchestrator._speaker_line(named) == "Speaker: robin (recognised by their voice)."
    assert orchestrator._speaker_line(orchestrator.ResolvedCredentials(user="robin")) == "Speaker: robin."


async def test_the_voice_note_reaches_the_question_and_raven_keeps_it_in_its_instructions(monkeypatch):
    seen = {}

    async def fake_fetch(*a, **k):
        return ""

    async def fake_inference(*args, **kwargs):
        seen["single"] = (args[2], kwargs.get("turn_note"))
        return "answer"

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_fetch)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=[]))
    await orchestrator.process_full_orchestration({
        "query": "lights on", "creds": {"user": "u"}, "model": "m", "system": "PERSONA",
        "turn_note": "[Voice context]\nIn the den.",
    })
    assert seen["single"] == ("PERSONA", "[Voice context]\nIn the den.")


def test_a_spoken_turn_reads_only_its_last_exchanges_cut_short():
    long_answer = "Owls " * 300
    history = [{"role": "user", "content": f"q{i}"} if i % 2 == 0 else {"role": "assistant", "content": long_answer}
               for i in range(10)]
    kept = orchestrator._voice_history(history)
    assert [m["role"] for m in kept] == ["user", "assistant", "user", "assistant"]
    assert kept[0]["content"] == "q6" and kept[2]["content"] == "q8"
    assert len(kept[1]["content"]) <= orchestrator.VOICE_HISTORY_CHARS + 3 and kept[1]["content"].endswith("...")
    assert orchestrator._voice_history([]) == []


async def test_a_voice_turn_gets_the_short_history_and_a_typed_one_the_full_one(monkeypatch):
    seen = {}
    history = [{"role": "user", "content": f"m{i}"} for i in range(10)]

    async def fake_fetch(*a, **k):
        return ""

    async def fake_inference(*args, **kwargs):
        seen["history"] = args[4]
        return "answer"

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_fetch)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(orchestrator, "get_history", AsyncMock(return_value=history))
    await orchestrator.process_full_orchestration({
        "query": "lights on", "creds": {"user": "u"}, "model": "m",
        "voice_context": {"satellite": "assist_satellite.den"},
    })
    assert [m["content"] for m in seen["history"]] == ["m6", "m7", "m8", "m9"]
    await orchestrator.process_full_orchestration({"query": "hello", "creds": {"user": "u"}, "model": "m"})
    assert len(seen["history"]) == 10
