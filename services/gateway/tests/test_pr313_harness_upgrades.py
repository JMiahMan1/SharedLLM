"""PR #313: SOTA harness upgrades that are pure, unit-testable seams.

These pin the chat/voice/workspace fixes: shadow-execution gating, the narrowed
DockerLogsRequest override, voice speech-stream routing, Turn 0 prose streaming,
response cleanup, output/history budgets, async tool cache, workspace prompt
separation, tool memory, and self-repair patch mode.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from services.gateway import main as gateway_main
from services.gateway import orchestrator, tool_cache, workspace_chat
from services.gateway.trainer import selffix

# --- main.py: shadow execution / log override / speech routing -----------------


def test_shadow_execution_is_never_run_for_non_autonomous_turns():
    for query in (
        "how to fix a complex bug",
        "design a better error page",
        "implement the refactor logic",
    ):
        assert gateway_main._shadow_execution_wanted(False, query) is False


def test_shadow_execution_still_runs_for_an_autonomous_complex_mission():
    assert gateway_main._shadow_execution_wanted(True, "fix the complex bug") is True
    # Autonomous but with no complex signal: no extra blocking LLM call either.
    assert gateway_main._shadow_execution_wanted(True, "what time is it") is False


@pytest.mark.parametrize(
    "query",
    [
        "How do I fix a Python KeyError?",
        "build me a log cabin",
        "write the output to a file",
        "show me the error message",
        "what is a log?",
        "why do I get a server error?",
        "the service returned an error",
    ],
)
def test_everyday_questions_do_not_get_the_docker_logs_override(query):
    assert gateway_main._wants_container_logs(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "show me the docker logs",
        "docker logs for the gateway container",
        "container logs please",
        "any gateway error logs?",
        "check the service logs",
        "rag telemetry logs from last night",
        "nginx logs",
    ],
)
def test_real_container_log_questions_still_get_the_override(query):
    assert gateway_main._wants_container_logs(query) is True


def test_a_voice_satellite_with_speech_stream_reaches_the_speech_generator():
    assert gateway_main._serves_speech_stream("voice", True, True, False) is True
    # A voice satellite without speech streaming still queues normally.
    assert gateway_main._serves_speech_stream("voice", False, True, False) is False
    # Standard clients keep their existing stream path.
    assert gateway_main._serves_speech_stream("web", True, True, True) is True
    assert gateway_main._serves_speech_stream("web", True, False, True) is False


# --- orchestrator.py: prose gate, JSON cleanup, budgets ------------------------


async def test_prose_stream_gate_streams_plain_prose_live_without_rebuffering():
    seen: list[str] = []

    async def callback(chunk):
        seen.append(chunk["text"] if isinstance(chunk, dict) else chunk)

    gate = orchestrator._ProseStreamGate(callback)
    for chunk in ("Here ", "is ", "the ", "answer ", "you asked for."):
        await gate.feed(chunk)

    assert gate.streamed is True
    assert "".join(seen) == "Here is the answer you asked for."


async def test_prose_stream_gate_holds_a_tool_call_until_the_turn_finishes():
    seen: list[str] = []

    async def callback(chunk):
        seen.append(chunk)

    gate = orchestrator._ProseStreamGate(callback)
    for chunk in ('{"action": "WorkspaceFileReadRequest", ', '"payload": {}}'):
        await gate.feed(chunk)

    assert gate.streamed is False
    assert seen == []


async def test_prose_stream_gate_lets_thinking_through_even_when_holding_json():
    seen: list[str] = []

    async def callback(chunk):
        seen.append(chunk)

    gate = orchestrator._ProseStreamGate(callback)
    await gate.feed({"type": "thinking", "text": "planning"})
    await gate.feed('{"action": "ShellRequest"}')

    assert seen == [{"type": "thinking", "text": "planning"}]
    assert gate.streamed is False


def test_strip_json_keeps_prose_before_a_trailing_tool_call():
    text = (
        "I checked the logs and the service is healthy.\n\n"
        '{"action": "DockerLogsRequest", "payload": {"container": "gateway"}}'
    )
    assert orchestrator.strip_json_from_response(text) == (
        "I checked the logs and the service is healthy."
    )


def test_strip_json_keeps_a_fenced_python_code_block():
    text = "Use this:\n\n```python\ndef f(x):\n    return {x: x * 2}\n```\n"
    assert orchestrator.strip_json_from_response(text) == text.strip()


def test_strip_json_keeps_curly_braces_in_math_and_prose():
    text = "The set {a, b} has two members and {x | x > 0} is its positive half."
    assert orchestrator.strip_json_from_response(text) == text


def test_strip_json_extracts_the_message_from_a_pure_tool_response():
    text = '{"status": "SUCCESS", "message": "The file was read."}'
    assert orchestrator.strip_json_from_response(text) == "The file was read."


def test_strip_json_discards_a_pure_fenced_tool_call_without_leaking_json():
    text = '```json\n{"action": "WorkspaceListRequest", "payload": {}}\n```'
    assert orchestrator.strip_json_from_response(text) == ""


def test_tool_output_cap_keeps_head_and_tail_with_a_notice():
    text = "HEAD" + ("x" * 20_000) + "TAIL"
    capped = orchestrator._cap_tool_output(text, limit=8_000)

    assert len(capped) < 8_200
    assert capped.startswith("HEAD")
    assert capped.endswith("TAIL")
    assert "characters truncated" in capped


def test_history_budget_masks_old_bulky_answers_but_keeps_recent_turns():
    history = [
        {"role": "assistant", "content": f"old answer {i} " + ("x" * 2_000)}
        for i in range(8)
    ]
    fitted = orchestrator._fit_history(history, budget_chars=50_000)

    assert fitted[0]["content"].startswith("[masked assistant message:")
    assert fitted[1]["content"].startswith("[masked assistant message:")
    assert fitted[-1]["content"].startswith("old answer 7")
    assert len(fitted) == 8


def test_history_budget_drops_only_after_masking_and_still_respects_limit():
    history = [
        {"role": "assistant", "content": f"turn {i}: " + ("z" * 1_200)}
        for i in range(20)
    ]
    fitted = orchestrator._fit_history(history, budget_chars=3_000)

    assert sum(len(m["content"]) for m in fitted) <= 3_000
    assert fitted[-1]["content"].startswith("turn 19")


# --- tool_cache.py: Redis is offloaded off the event loop ---------------------


async def test_tool_cache_runs_redis_work_in_a_worker_thread(monkeypatch):
    calls: list[str] = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append(getattr(fn, "__name__", "callable"))
        return fn(*args, **kwargs)

    monkeypatch.setattr(tool_cache.asyncio, "to_thread", fake_to_thread)

    async def source():
        return "answer"

    await tool_cache.call("CalendarRequest", {"action": "read"}, user="alice", fetch=source, conversation="c1")
    await tool_cache.call("CalendarRequest", {"action": "read"}, user="alice", fetch=source, conversation="c1")

    assert calls, "the async call path must not touch redis-py on the event loop"


# --- workspace_chat.py: tool memory survives between turns -------------------


def test_workspace_history_keeps_a_compact_tool_summary_for_followups():
    messages = [
        {
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "I read the parser."},
                {"type": "tool", "name": "WorkspaceFileReadRequest",
                 "output": "File content of parser.py:\n" + ("x" * 1_000)},
                {"type": "tool", "name": "WorkspaceShellRequest",
                 "output": "pytest: 3 passed"},
            ],
        }
    ]
    history = workspace_chat.history_from(messages)

    assert len(history) == 1
    assert "[used WorkspaceFileReadRequest:" in history[0]["content"]
    assert "[used WorkspaceShellRequest: pytest: 3 passed]" in history[0]["content"]
    for line in history[0]["content"].splitlines():
        if line.startswith("[used "):
            assert len(line) <= workspace_chat.TOOL_NOTE_CHARS


# --- selffix.py: patch support, dropped-SIM attribution, change summary ------


def test_search_replace_patches_apply_and_reject_ambiguous_matches():
    source = "a = 1\nb = 2\n"
    patched, error = selffix.apply_patches(source, [("b = 2", "b = 3")])
    assert patched == "a = 1\nb = 3\n"
    assert error == ""

    patched, error = selffix.apply_patches("x = 1\n", [("missing", "y")])
    assert patched is None and "did not match" in error
    patched, error = selffix.apply_patches("a = 1\na = 1\n", [("a = 1", "a = 2")])
    assert patched is None and "more than once" in error


def test_patch_format_is_extracted_from_an_answer():
    answer = "Here:\n<<<SEARCH\nold\n===\nnew\n>>>REPLACE\n"
    assert selffix.extract_patches(answer) == [("old", "new")]


# --- rag/sim.py: no embed function means no lazy backfill call ----------------


def test_sim_backfill_is_skipped_when_no_embedder_is_configured(tmp_path):
    from services.rag import sim
    from services.rag.tests.test_rag_sim_memory import _conn, _record

    conn = _conn(tmp_path)
    _record(conn, edit_or_rule="Prefer the canonical tool name.")

    # A lexical-only caller supplies a query embedding but no embed_fn. The
    # lazy backfill must be a no-op instead of calling None.
    results = sim.query(
        conn,
        user_id="default",
        harness="satellite",
        query="canonical tool name",
        model="qwen3-6-35b",
        query_embedding=[1.0, 1.0],
        embed_fn=None,
    )
    assert isinstance(results, list)


# --- agent_loop.py: batch classification, masking, hard compact, lint ---------


def test_read_only_batch_stops_at_the_first_mutating_call():
    from services.gateway import agent_loop

    calls = [
        {"action": "WorkspaceFileReadRequest", "payload": {"file_path": "a.py"}},
        {"action": "WorkspaceSearchRequest", "payload": {"query": "TODO"}},
        {"action": "GitOperationRequest", "payload": {"action": "status"}},
        {"action": "WorkspaceFileWriteRequest", "payload": {"file_path": "b.py", "content": "x"}},
        {"action": "WorkspaceFileReadRequest", "payload": {"file_path": "c.py"}},
    ]
    assert agent_loop._read_only_run(calls) == 3
    assert agent_loop._is_read_only_call(calls[2]) is True
    assert agent_loop._is_read_only_call(
        {"action": "GitOperationRequest", "payload": {"action": "push"}}
    ) is False


async def test_read_only_batch_runs_concurrently_and_combines_one_observation(monkeypatch):
    from services.gateway import agent_loop

    class _Resp:
        status = 200

        def __init__(self, action):
            self._action = action

        async def json(self, **_kwargs):
            return {"status": "SUCCESS", "message": f"read {self._action}"}

        async def text(self):
            return ""

    class _Client:
        def __init__(self):
            self.urls: list[str] = []

        async def post(self, url, **_kwargs):
            self.urls.append(url)
            await asyncio.sleep(0.04)
            return _Resp(url.rsplit("/", 1)[-1])

    client = _Client()

    class _CM:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(agent_loop, "shared_http_client", lambda: _CM())

    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await agent_loop._execute_read_only_group(
        [
            {"action": "WorkspaceFileReadRequest", "payload": {"file_path": "a.py"}},
            {"action": "WorkspaceFileReadRequest", "payload": {"file_path": "b.py"}},
            {"action": "WorkspaceSearchRequest", "payload": {"query": "TODO"}},
        ],
        user_context={"user": "alice"},
        workspace_id="w1",
    )
    elapsed = loop.time() - started

    assert result["status"] == "SUCCESS"
    assert len(result["detail"]["batch_results"]) == 3
    assert len(client.urls) == 3
    assert elapsed < 0.10  # three 40 ms reads overlapped, not 120 ms serial


def test_no_consecutive_user_messages_survive_compaction():
    from services.gateway import agent_loop

    out = agent_loop._no_consecutive_user([
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "answer"},
    ])
    assert [m["role"] for m in out] == ["user", "assistant"]
    assert out[0]["content"] == "first\n\nsecond"


def test_jetbrains_observation_masking_keeps_the_newest_four_turns():
    from services.gateway import agent_loop

    conv = [{"role": "system", "content": "static system"}]
    for i in range(6):
        conv.append({"role": "assistant", "content": f'{{"action": "read", "n": {i}}}'})
        conv.append({
            "role": "user",
            "content": f"LAST TOOL RESULT:\n{{\"action\": \"read\", \"status\": \"SUCCESS\", \"n\": {i}}}\n"
            + ("x" * 700),
        })

    masked = agent_loop._mask_observations(conv)

    assert masked[0] == conv[0]
    # The two oldest observations become one-line stubs.
    assert masked[2]["content"].startswith("[masked observation:")
    assert masked[4]["content"].startswith("[masked observation:")
    # The newest four turns are verbatim.
    for index in range(6, len(conv)):
        assert masked[index] == conv[index]


def test_hard_compact_always_pins_mission_lock_and_static_system():
    from services.gateway import agent_loop

    static = "STATIC SYSTEM PREFIX"
    pinned = "MISSION LOCK: build the thing\n\nPLAN:\n1. read\n2. write"
    conv = [{"role": "system", "content": static}, {"role": "user", "content": pinned}]
    for i in range(10):
        conv.append({"role": "assistant", "content": f"step {i}"})
        conv.append({"role": "user", "content": "observation " + ("z" * 900)})

    compacted = agent_loop._hard_compact_conversation(conv, "build the thing", fuel_budget=2_000)

    assert compacted[0]["content"] == static
    assert any(str(m["content"]).startswith("MISSION LOCK:") for m in compacted)
    assert [m["role"] for m in compacted[:2]] == ["system", "user"]


def test_compaction_keeps_the_static_system_prefix_and_pinned_mission_lock():
    from services.gateway import agent_loop

    static = "STATIC SYSTEM PREFIX"
    conv = [
        {"role": "system", "content": static},
        {"role": "user", "content": "MISSION LOCK: build it\nPLAN"},
    ]
    for i in range(12):
        conv.append({"role": "assistant", "content": f"assistant turn {i} " + ("y" * 800)})
        conv.append({"role": "user", "content": f"tool result {i} " + ("z" * 800)})

    compacted = agent_loop._compact_conversation(
        conv, {"num_ctx": 4_000, "num_predict": 512}
    )

    assert compacted[0]["content"] == static
    assert any(str(m["content"]).startswith("MISSION LOCK:") for m in compacted)
    assert compacted[0]["role"] == "system"


async def test_run_post_write_lint_forwards_workspace_id_and_fails_closed(monkeypatch):
    from services.gateway import agent_loop

    bodies: list[dict] = []
    response = {
        "status": "SUCCESS",
        "message": "",
        "detail": {"passed": True, "verified": True, "results": []},
    }

    class _Resp:
        def __init__(self, status=200, body=None):
            self.status = status
            self._body = body if body is not None else response

        async def json(self):
            return self._body

        async def text(self):
            return json.dumps(self._body)

    class _Client:
        def __init__(self):
            self.next_resp = _Resp()

        async def post(self, _url, **kwargs):
            bodies.append(kwargs.get("json") or {})
            return self.next_resp

    class _CM:
        def __init__(self, client):
            self.client = client

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, *_exc):
            return False

    client = _Client()
    monkeypatch.setattr(agent_loop, "shared_http_client", lambda: _CM(client))
    log = logging.getLogger("test")

    assert await agent_loop.run_post_write_lint(
        "a.py", "http://exec", "secret", log, workspace_id="w1"
    ) is None
    assert bodies[-1]["workspace_id"] == "w1"

    client.next_resp = _Resp(body={"status": "ERROR", "message": "lint crashed"})
    feedback = await agent_loop.run_post_write_lint(
        "a.py", "http://exec", "secret", log, workspace_id="w1"
    )
    assert feedback and "lint crashed" in feedback

    client.next_resp = _Resp(body={"status": "SUCCESS", "detail": {}})
    feedback = await agent_loop.run_post_write_lint("a.py", "http://exec", "secret", log)
    assert feedback and "UNVERIFIED" in feedback

    client.next_resp = _Resp(status=500, body={"detail": "boom"})
    feedback = await agent_loop.run_post_write_lint("a.py", "http://exec", "secret", log)
    assert feedback and "HTTP 500" in feedback


# --- workspace harness ---------------------------------------------------------


async def test_workspace_prompt_keeps_live_context_out_of_the_static_system(monkeypatch):
    seen: list[list[dict]] = []

    async def fake_ollama(payload, **_kwargs):
        seen.append(payload["messages"])
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(orchestrator, "call_ollama", fake_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: "STATIC BASE PROMPT")

    await orchestrator._single_turn_inference(
        query="what is in my workspace?",
        model="test-model",
        system_prompt="STATIC BASE PROMPT",
        rag_context="",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
        workspace_context="FILE TREE: secret_live_file.py 42 bytes",
        workspace_id="ws1",
        harness="workspace",
    )

    system = seen[0][0]["content"]
    question = seen[0][-1]["content"]
    assert "secret_live_file.py" not in system, "live workspace tree invalidated the cached system prefix"
    assert "secret_live_file.py" in question


async def test_workspace_turn_never_grounds_on_raven_memory_and_sets_harness_flags(monkeypatch):
    seen: dict = {}

    async def fake_rag(*_args, **_kwargs):
        # _fetch_rag_context always appends raven_memory.md/workspace_memory,
        # which used to make context non-empty and wrongly grounded=True.
        return "raven_memory.md: remember the user likes tea"

    async def fake_workspace_context(*_args, **_kwargs):
        return "FILE TREE: app.py 120 bytes"

    async def fake_inference(**kwargs):
        seen.update(kwargs)
        seen["conversation"] = tool_cache.CONVERSATION.get()
        return "an answer"

    async def fake_librarian_model():
        return "librarian-model"

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_rag, raising=False)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(gateway_main, "_workspace_context", fake_workspace_context)
    monkeypatch.setattr(gateway_main, "get_librarian_model", fake_librarian_model)
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda *_a: "BASE")

    result = await gateway_main._run_workspace_turn(
        "ws-42", {"user": "tester"}, "what did we decide about tea?", "librarian"
    )

    assert result["status"] == "SUCCESS"
    assert seen["grounded"] is False
    assert seen["show_thinking"] is True
    assert seen["budget_path"] == "workspace"
    assert seen["harness"] == "workspace"
    assert seen["workspace_context"] == "FILE TREE: app.py 120 bytes"
    assert seen["rag_context"], "the retrieved context should still be passed to the model"
    assert seen["conversation"] == "workspace:ws-42"
    # Contextvar must not leak a workspace id into the next request.
    assert tool_cache.CONVERSATION.get() == "default"


def test_verification_gate_beats_the_completion_phrase():
    from services.gateway import agent_loop

    answer = "All done. Mission complete!"
    assert agent_loop.completion_hold_kind(answer, [], ["app.py"]) == "verification"
    # Open objectives still have priority over verification.
    assert agent_loop.completion_hold_kind(answer, ["deploy"], ["app.py"]) == "objectives"
    # Nothing pending and no completion phrase: the loop keeps working normally.
    assert agent_loop.completion_hold_kind(answer, [], []) is None
    assert agent_loop.completion_hold_kind("still working", [], ["app.py"]) is None
