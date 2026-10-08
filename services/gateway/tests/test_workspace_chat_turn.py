"""Workspace chat turns stream as events and are kept as a conversation."""
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import services.gateway.orchestrator as orchestrator
import services.gateway.workspace_chat as workspace_chat
from services.gateway import main as gateway_main
from services.gateway.main import app
from services.gateway.workspace_chat import TurnTranscript, history_from


@pytest.fixture
def store(monkeypatch):
    """An in-memory stand-in for identity's chat store."""
    chats = {"c1": {"id": "c1", "title": "New chat", "messages": [
        {"role": "user", "parts": [{"type": "text", "text": "What is in this workspace?"}]},
        {"role": "assistant", "parts": [{"type": "reasoning", "text": "hmm"}, {"type": "text", "text": "A sermon video."}]},
    ]}}
    calls = []

    async def fake_identity_chat(method, path, *, identity_url, auth_header, json=None, params=None):
        calls.append((method, path, json))
        chat_id = path.strip("/").split("/")[0] if path else ""
        if method == "GET" and path == "/c1":
            return 200, chats["c1"]
        if method == "POST" and path.endswith("/messages"):
            chats[chat_id]["messages"].append(json)
            return 200, {"id": len(chats[chat_id]["messages"]), **json}
        return 404, {"detail": "Chat not found"}

    monkeypatch.setattr(workspace_chat, "identity_chat", fake_identity_chat)
    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", AsyncMock(return_value={"user": "jeremiah", "is_admin": True}))
    monkeypatch.setattr(gateway_main, "get_assistant_model", AsyncMock(return_value="assistant-model"))
    monkeypatch.setattr(gateway_main, "_workspace_context", AsyncMock(return_value=""))
    monkeypatch.setattr(gateway_main, "select_system_instruction_for_query", lambda q, m: "SYSTEM")
    return {"chats": chats, "calls": calls}


def _turn(client, message="Transcribe the mkv", mode="single_task"):
    resp = client.post("/api/workspaces/sermon/chats/c1/turn", json={"message": message, "mode": mode},
                       headers={"Authorization": "Bearer t"})
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_a_turn_streams_its_steps_and_is_stored_as_parts(store, monkeypatch):
    seen = {}

    async def fake_inference(**kwargs):
        seen.update(kwargs)
        emit = kwargs["event_callback"]
        await emit({"type": "step", "n": 1})
        await emit({"type": "thinking", "text": "Find the video.", "step": 1})
        await emit({"type": "tool_call", "id": "step1", "name": "WorkspaceSearchRequest", "input": {"query": "mkv"}, "preamble": "Let me look."})
        await emit({"type": "tool_result", "id": "step1", "output": "File names that match:\n- service.mkv"})
        await emit({"type": "text", "text": "It is ", "step": "final"})
        return "It is service.mkv; transcribing needs Raven."

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    events = _turn(TestClient(app))

    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert {"thinking", "tool_call", "tool_result", "text"} <= set(kinds)

    # The conversation so far went to the model: prose only, no thinking.
    assert seen["history"] == [
        {"role": "user", "content": "What is in this workspace?"},
        {"role": "assistant", "content": "A sermon video."},
    ]
    stored = store["chats"]["c1"]["messages"]
    assert stored[-2] == {"role": "user", "parts": [{"type": "text", "text": "Transcribe the mkv"}], "meta": {"mode": "single_task"}}
    reply = stored[-1]
    assert [p["type"] for p in reply["parts"]] == ["reasoning", "text", "tool", "text"]
    assert reply["parts"][2]["status"] == "done" and "service.mkv" in reply["parts"][2]["output"]
    assert reply["parts"][-1]["text"] == "It is service.mkv; transcribing needs Raven."
    assert reply["meta"]["status"] == "done" and reply["meta"]["model"] == "assistant-model"


def test_a_failed_turn_is_stored_with_its_error(store, monkeypatch):
    async def refusing(**kwargs):
        raise orchestrator.InferenceUnavailable("queue_timeout")

    monkeypatch.setattr(orchestrator, "_single_turn_inference", refusing)
    events = _turn(TestClient(app))
    assert events[-1]["type"] == "error"
    reply = store["chats"]["c1"]["messages"][-1]
    assert reply["meta"]["status"] == "error"
    assert reply["parts"][-1]["type"] == "error"


def test_an_empty_message_is_refused(store):
    resp = TestClient(app).post("/api/workspaces/sermon/chats/c1/turn", json={"message": "  "}, headers={"Authorization": "Bearer t"})
    assert resp.status_code == 400


def test_a_missing_chat_is_not_found(store):
    resp = TestClient(app).post("/api/workspaces/sermon/chats/nope/turn", json={"message": "hi"}, headers={"Authorization": "Bearer t"})
    assert resp.status_code == 404


def test_tool_inputs_never_carry_credentials():
    safe = orchestrator._public_tool_input({"payload": {"query": "mkv", "user_context": {"nextcloud_pass": "x"}, "password": "p", "api_key": "k"}})
    assert safe == {"query": "mkv"}


def test_a_tool_preamble_keeps_the_prose_and_drops_the_call():
    assert orchestrator._tool_preamble('Let me look.\n```json\n{"tool": "WorkspaceSearchRequest", "query": "mkv"}\n```') == "Let me look."


def test_history_keeps_prose_and_trims_from_the_oldest():
    messages = [{"role": "user", "parts": [{"type": "text", "text": "x" * 7000}]},
                {"role": "assistant", "parts": [{"type": "tool", "name": "t"}, {"type": "text", "text": "y" * 7000}]},
                {"role": "user", "parts": [{"type": "text", "text": "now?"}]}]
    history = history_from(messages)
    assert [m["role"] for m in history] == ["assistant", "user"]


def test_a_stopped_turn_marks_running_tools_aborted():
    t = TurnTranscript()
    t.add({"type": "tool_call", "id": "s1", "name": "STTRequest", "input": {}})
    parts = t.finish(None, status="aborted")
    assert parts[0]["status"] == "aborted"


async def test_the_inference_loop_reports_each_step_as_events(monkeypatch):
    replies = iter([
        ("Find it.", 'Let me look.\n```json\n{"tool": "WorkspaceSearchRequest", "query": "mkv"}\n```'),
        ("Got it.", "It is service.mkv."),
    ])

    async def fake_call(payload, **kwargs):
        thinking, content = next(replies)
        cb = payload["chunk_callback"]
        await cb({"type": "thinking", "text": thinking})
        await cb({"type": "content", "text": content})
        return {"message": {"content": content}}

    async def fake_exec(action, tool_data, query, creds, workspace_id=None):
        return "File names that match:\n- service.mkv"

    monkeypatch.setattr(orchestrator, "call_ollama", fake_call)
    monkeypatch.setattr(orchestrator, "_execute_single_tool", fake_exec)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _n: "Tools: WorkspaceSearchRequest")
    events = []

    async def collect(event):
        events.append(event)

    answer = await orchestrator._single_turn_inference(
        query="find the mkv", model="m", system_prompt="S", rag_context="", history=[],
        creds=orchestrator.ResolvedCredentials(user="u"), workspace_id="sermon", event_callback=collect,
    )
    assert answer == "It is service.mkv."
    kinds = [e["type"] for e in events]
    assert kinds == ["step", "thinking", "text", "tool_call", "tool_result", "step", "thinking", "text"]
    call = events[3]
    assert call["name"] == "WorkspaceSearchRequest" and call["preamble"] == "Let me look." and call["input"] == {"query": "mkv"}
