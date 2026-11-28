"""PR #316: cross-harness delegation, guardrails, and context firewall.

These tests pin the bridge's deterministic refusals (depth, cycles), the fact
that caller identity is copied and never escalated, the compact result packets
the UI renders, the read-only Explorer firewall, and the wiring of all five
``Delegate*Request`` actions into the single-turn orchestrator and Raven's
AgentLoop.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from services.gateway import agent_loop, orchestrator
from services.gateway import harness_bridge as bridge
from services.gateway import main as gateway_main
from services.gateway.schemas import ResolvedCredentials


@pytest.fixture(autouse=True)
def _reset_contextvars():
    depth_token = bridge.HARNESS_CALL_DEPTH.set(0)
    chain_token = bridge.HARNESS_CALL_CHAIN.set(())
    try:
        yield
    finally:
        bridge.HARNESS_CALL_CHAIN.reset(chain_token)
        bridge.HARNESS_CALL_DEPTH.reset(depth_token)


_DELEGATION_ARG_KEYS = {
    "DelegateRavenRequest": ("goal",),
    "DelegateWorkspaceRequest": ("workspace_id", "prompt"),
    "DelegateLookupRequest": ("query",),
    "DelegateSubagentRequest": ("question",),
    "DelegateNotifyRequest": ("message",),
}


def _assert_delegation_call(action, captured, expected):
    keys = _DELEGATION_ARG_KEYS[action]
    args = captured.get("args", ())
    for key, value in zip(keys, args, strict=False):
        assert value == expected[key]
    for key, value in expected.items():
        if key not in keys:
            assert captured.get(key) == value
    creds = captured.get("creds") or args[len(keys)]
    assert creds.user == "alice"


def _creds(**overrides) -> ResolvedCredentials:
    body = {"user": "alice", "is_admin": False, **overrides}
    return ResolvedCredentials(**body)


# --- depth, cycles, context reset -------------------------------------------


def test_depth_limit_refuses_deterministically():
    bridge.HARNESS_CALL_DEPTH.set(bridge.MAX_HARNESS_DEPTH)

    assert bridge.can_delegate("chat", "raven") is False
    assert bridge.delegation_refusal("chat", "raven") == bridge.DEPTH_LIMIT_MESSAGE
    assert bridge.DEPTH_LIMIT_MESSAGE == (
        "Delegation depth limit (2) reached; answer directly instead."
    )


def test_cycle_raven_workspace_raven_is_refused_deterministically():
    with bridge.delegation_context("raven"):
        assert bridge.HARNESS_CALL_DEPTH.get() == 1
        assert bridge.HARNESS_CALL_CHAIN.get() == ("raven",)

        refusal = bridge.delegation_refusal("workspace", "raven")
        assert refusal is not None
        assert "raven -> workspace -> raven" in refusal
        assert "cycle" in refusal.lower()

        with bridge.delegation_context("workspace"):
            assert bridge.HARNESS_CALL_CHAIN.get() == ("raven", "workspace")
            assert bridge.can_delegate("lookup", "lookup") is False


def test_raven_to_raven_is_refused_as_a_same_harness_cycle():
    refusal = bridge.delegation_refusal("raven", "raven")
    assert refusal is not None
    assert "same harness twice" in refusal
    # A workspace may hand work to a different workspace, but the access check
    # still decides whether it can.
    assert bridge.delegation_refusal("workspace", "workspace", allow_same_harness=True) is None


def test_contextvars_reset_even_when_the_delegated_call_raises():
    with pytest.raises(RuntimeError), bridge.delegation_context("raven"):
        assert bridge.HARNESS_CALL_DEPTH.get() == 1
        assert bridge.HARNESS_CALL_CHAIN.get() == ("raven",)
        raise RuntimeError("boom")

    assert bridge.HARNESS_CALL_DEPTH.get() == 0
    assert bridge.HARNESS_CALL_CHAIN.get() == ()


async def test_contextvars_reset_when_the_raven_enqueue_raises(monkeypatch):
    async def fake_prompt(_query):
        return "system"

    async def failing_enqueue(**_kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(gateway_main, "_build_raven_system_prompt", fake_prompt)
    monkeypatch.setattr(gateway_main, "_enqueue_user_mission", failing_enqueue)

    result = json.loads(
        await bridge.delegate_to_raven("do it", _creds(), source_harness="chat")
    )
    assert result["status"] == "ERROR"
    assert bridge.HARNESS_CALL_DEPTH.get() == 0
    assert bridge.HARNESS_CALL_CHAIN.get() == ()


def test_source_harness_maps_satellite_to_voice_or_chat():
    assert bridge.normalize_source_harness("satellite", _creds(identified_by="voice")) == "voice"
    assert bridge.normalize_source_harness("satellite", _creds()) == "chat"
    assert bridge.normalize_source_harness("mission") == "raven"


# --- Raven: async launch, caller identity, voice fast-return ----------------


async def test_delegate_to_raven_returns_a_mission_id_and_keeps_callers_identity(monkeypatch):
    captured: dict = {}

    async def fake_prompt(query):
        return f"RAVEN SYSTEM for {query}"

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"id": 42, "workspace_id": "ws-1"}

    monkeypatch.setattr(gateway_main, "_build_raven_system_prompt", fake_prompt)
    monkeypatch.setattr(gateway_main, "_enqueue_user_mission", fake_enqueue)

    result = json.loads(
        await bridge.delegate_to_raven(
            "Fix the failing tests in the billing service",
            _creds(ha_token="token-123"),
            source_harness="voice",
            workspace_id="ws-1",
            context_summary="x" * 4000,
        )
    )

    assert result["status"] == "SUCCESS"
    assert result["mission_id"] == 42
    assert result["workspace_id"] == "ws-1"
    assert "Started Raven mission #42" in result["summary"]
    assert "I'll notify you" in result["summary"]
    assert result["delegated_from"] == "voice"

    assert captured["query"] == "Fix the failing tests in the billing service"
    assert captured["creds"]["user"] == "alice"
    assert captured["creds"]["is_admin"] is False
    assert captured["creds"]["ha_token"] == "token-123"
    assert "is_admin" not in captured["creds"] or captured["creds"]["is_admin"] is False
    packet = captured["system"]
    assert "HarnessHandoff" in packet
    handoff = json.loads(packet.split("## Inter-harness handoff\n", 1)[1])
    assert handoff["delegated_from"] == "voice"
    assert handoff["harness_call_chain"] == ["voice"]
    assert len(handoff["context_summary"]) <= 1500
    assert bridge.HARNESS_CALL_DEPTH.get() == 0


async def test_voice_delegate_raven_fast_returns_the_spoken_confirmation(monkeypatch):
    calls: list[int] = []
    seen: dict = {}

    async def fake_ollama(payload, **_kwargs):
        calls.append(1)
        seen["messages"] = payload["messages"]
        return {
            "message": {
                "content": '{"tool": "DelegateRavenRequest", "goal": "Audit the release"}'
            }
        }

    async def fake_execute(action, tool_data, query, creds, workspace_id=None):
        seen["action"] = action
        seen["workspace_id"] = workspace_id
        return json.dumps({
            "status": "SUCCESS",
            "mission_id": 77,
            "workspace_id": "ws-9",
            "summary": "Started Raven mission #77 for Audit the release. I'll notify you when it finishes.",
        })

    monkeypatch.setattr(orchestrator, "call_ollama", fake_ollama)
    monkeypatch.setattr(orchestrator, "_execute_single_tool", fake_execute)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: "TOOL GUIDE")

    answer = await orchestrator._single_turn_inference(
        query="audit the release for me",
        model="test-model",
        system_prompt="SYSTEM",
        rag_context="",
        history=[],
        creds=_creds(identified_by="voice"),
        harness="satellite",
    )

    assert answer.startswith("Started Raven mission #77")
    # One model call: the confirmation is the tool result, not another round trip.
    assert len(calls) == 1
    assert seen["action"] == "delegateravenrequest"
    assert seen["workspace_id"] is None


# --- Workspace: access isolation, bounded summary, async queueing ------------


async def test_delegate_to_workspace_is_bounded_and_workspace_scoped(monkeypatch):
    seen: dict = {}

    async def fake_request(url, payload, *, method="POST", **_kwargs):
        seen["url"] = url
        assert method == "GET"
        return 200, {"workspaces": [{"id": "ws-1"}]}

    async def fake_turn(workspace_id, creds, query, mode, **_kwargs):
        seen.update(workspace_id=workspace_id, creds=creds, query=query, mode=mode)
        return {"status": "SUCCESS", "answer": "A" * 5000, "resolved_mode": "single_task"}

    monkeypatch.setattr("services.gateway.workspace_api.request", fake_request)
    monkeypatch.setattr(gateway_main, "_run_workspace_turn", fake_turn)

    result = json.loads(
        await bridge.delegate_to_workspace(
            "ws-1",
            "Summarise the repo",
            _creds(),
            source_harness="raven",
        )
    )

    assert result["status"] == "SUCCESS"
    assert result["workspace_id"] == "ws-1"
    assert len(result["summary"]) <= 2000
    assert seen["workspace_id"] == "ws-1"
    assert seen["creds"]["user"] == "alice"
    assert "rag_user=alice" in seen["url"]


async def test_delegate_to_workspace_refuses_an_inaccessible_workspace(monkeypatch):
    called = False

    async def fake_request(_url, _payload, *, method="POST", **_kwargs):
        return 200, {"workspaces": [{"id": "ws-mine"}]}

    async def fake_turn(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"answer": "should not run"}

    monkeypatch.setattr("services.gateway.workspace_api.request", fake_request)
    monkeypatch.setattr(gateway_main, "_run_workspace_turn", fake_turn)

    result = json.loads(
        await bridge.delegate_to_workspace(
            "admin-only", "Do work", _creds(), source_harness="chat"
        )
    )

    assert result["status"] == "REFUSED"
    assert "do not have access" in result["message"]
    assert called is False


async def test_voice_long_workspace_task_is_queued_and_notifies_later(monkeypatch):
    queued: dict = {}

    async def fake_request(_url, _payload, *, method="POST", **_kwargs):
        return 200, {"workspaces": [{"id": "ws-1"}]}

    async def fake_async_turn(workspace_id, prompt, mode, creds, source, **_kwargs):
        queued.update(workspace_id=workspace_id, prompt=prompt, mode=mode, creds=creds, source=source)

    monkeypatch.setattr("services.gateway.workspace_api.request", fake_request)
    monkeypatch.setattr(bridge, "_workspace_async_turn", fake_async_turn)

    result = json.loads(
        await bridge.delegate_to_workspace(
            "ws-1",
            "Please refactor the billing module and run the tests",
            _creds(identified_by="voice"),
            source_harness="voice",
        )
    )

    assert result["status"] == "QUEUED"
    assert result["async_mode"] is True
    assert "notify" in result["summary"].lower()
    await asyncio.gather(*list(bridge._BACKGROUND_TASKS))
    assert queued["workspace_id"] == "ws-1"
    assert queued["creds"]["user"] == "alice"


# --- Lookup: bounded and read-only ------------------------------------------


async def test_delegate_lookup_is_bounded_and_read_only(monkeypatch):
    seen: dict = {}

    async def fake_rag(query, user, *args, **kwargs):
        seen.update(query=query, user=user, kwargs=kwargs)
        return "calendar: dentist at 3pm"

    async def fake_inference(**kwargs):
        seen["inference"] = kwargs
        return "B" * 4000

    async def fake_model():
        return "test-model"

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", fake_rag)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(gateway_main, "get_assistant_model", fake_model)
    monkeypatch.setattr(
        gateway_main, "select_system_instruction_for_query", lambda *_a, **_k: "SYS"
    )

    result = json.loads(
        await bridge.delegate_lookup(
            "when is the dentist", _creds(), source_harness="raven", domain="calendar"
        )
    )

    assert result["status"] == "SUCCESS"
    assert result["read_only"] is True
    assert len(result["summary"]) <= 1500
    assert seen["user"] == "alice"
    assert seen["inference"]["read_only"] is True
    assert seen["inference"]["max_turns"] == 1
    assert seen["inference"]["harness"] == "satellite"
    assert seen["inference"]["creds"].user == "alice"


# --- Explorer subagent: firewall + context condensation ---------------------


def test_read_only_firewall_allows_reads_and_blocks_mutations():
    assert bridge.is_read_only_action("WorkspaceFileReadRequest") is True
    assert bridge.is_read_only_action("WorkspaceSearchRequest") is True
    assert bridge.is_read_only_action("git_operation", {"action": "diff"}) is False
    assert bridge.is_read_only_action("GitOperationRequest", {"action": "diff"}) is True
    assert bridge.is_read_only_action("GitOperationRequest", {"action": "commit"}) is False
    assert bridge.is_read_only_action("WorkspaceFileWriteRequest") is False
    assert bridge.is_read_only_action("WorkspaceShellRequest") is False
    assert bridge.is_read_only_action("DelegateRavenRequest") is False


async def test_read_only_turn_refuses_a_mutating_tool_before_dispatch(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("a mutating tool must never be dispatched read-only")

    monkeypatch.setattr(orchestrator, "_execute_single_tool", forbidden)
    token = orchestrator.READ_ONLY_TURN.set(True)
    try:
        result = await orchestrator._run_single_tool(
            "WorkspaceFileWriteRequest",
            {"action": "WorkspaceFileWriteRequest", "payload": {"file_path": "a.py"}},
            "write the file",
            _creds(),
        )
    finally:
        orchestrator.READ_ONLY_TURN.reset(token)

    assert result == bridge.READ_ONLY_REFUSAL


async def test_explorer_subagent_returns_one_condensed_summary_and_traces(monkeypatch):
    seen: dict = {}
    traces: list[dict] = []

    async def fake_inference(**kwargs):
        seen.update(kwargs)
        callback = kwargs.get("event_callback")
        if callback:
            await callback({"type": "tool_call", "name": "WorkspaceFileReadRequest", "output": "read 120 lines"})
        return "C" * 5000

    async def fake_model():
        return "test-model"

    monkeypatch.setattr(orchestrator, "_single_turn_inference", fake_inference)
    monkeypatch.setattr(gateway_main, "get_assistant_model", fake_model)

    async def on_event(event):
        traces.append(event)

    result = json.loads(
        await bridge.delegate_explorer_subagent(
            "Where is the delegation dispatch?",
            _creds(),
            source_harness="raven",
            paths=["services/gateway/orchestrator.py"],
            max_turns=99,
            event_callback=on_event,
        )
    )

    assert result["status"] == "SUCCESS"
    assert result["read_only"] is True
    assert result["max_turns"] == 6
    assert len(result["summary"]) <= 2000
    assert seen["read_only"] is True
    assert seen["max_turns"] == 6
    assert seen["harness"] == "raven"
    assert "services/gateway/orchestrator.py" in seen["system_prompt"]
    assert traces and traces[0]["type"] == "subagent"
    assert traces[0]["phase"] == "tool_call"


# --- Notify: permission propagation ----------------------------------------


async def test_non_admin_cannot_notify_another_user(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("a refused notice must not be sent")

    monkeypatch.setattr("services.common.notify.send", forbidden)

    result = json.loads(
        await bridge.delegate_notify(
            "hello bob", _creds(), source_harness="chat", target_user="bob"
        )
    )

    assert result["status"] == "REFUSED"
    assert "Only an admin" in result["message"]


async def test_admin_can_notify_another_user_and_caller_notifies_self(monkeypatch):
    sent: list[dict] = []

    async def fake_send(payload, **_kwargs):
        sent.append(payload)
        return True

    monkeypatch.setattr("services.common.notify.send", fake_send)

    self_result = json.loads(
        await bridge.delegate_notify(
            "done", _creds(), source_harness="raven", mission_id=9
        )
    )
    admin_result = json.loads(
        await bridge.delegate_notify(
            "done for bob",
            _creds(is_admin=True),
            source_harness="raven",
            target_user="bob",
        )
    )

    assert self_result["status"] == "SUCCESS"
    assert self_result["user"] == "alice"
    assert admin_result["status"] == "SUCCESS"
    assert admin_result["user"] == "bob"
    assert sent[0]["user"] == "alice"
    assert sent[1]["user"] == "bob"
    assert sent[0]["data"]["mission_id"] == 9


# --- Orchestrator dispatch --------------------------------------------------


@pytest.mark.parametrize(
    ("action", "bridge_func", "payload", "expected"),
    [
        (
            "DelegateRavenRequest",
            "delegate_to_raven",
            {"goal": "build it", "workspace_id": "ws-1"},
            {"goal": "build it", "workspace_id": "ws-1"},
        ),
        (
            "DelegateWorkspaceRequest",
            "delegate_to_workspace",
            {"workspace_id": "ws-1", "prompt": "fix it", "async_mode": True},
            {"workspace_id": "ws-1", "prompt": "fix it", "async_mode": True},
        ),
        (
            "DelegateLookupRequest",
            "delegate_lookup",
            {"query": "weather", "domain": "weather"},
            {"query": "weather", "domain": "weather"},
        ),
        (
            "DelegateSubagentRequest",
            "delegate_explorer_subagent",
            {"question": "where?", "paths": "a.py,b.py", "max_turns": 4},
            {"question": "where?", "paths": ["a.py", "b.py"], "max_turns": 4},
        ),
        (
            "DelegateNotifyRequest",
            "delegate_notify",
            {"message": "done", "mission_id": 5},
            {"message": "done", "mission_id": 5},
        ),
    ],
)
async def test_single_turn_dispatch_wires_every_delegation_tool(
    monkeypatch, action, bridge_func, payload, expected
):
    captured: dict = {}

    async def fake_bridge(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return json.dumps({"status": "SUCCESS", "summary": "ok"})

    async def fake_settings():
        return {}

    monkeypatch.setattr(bridge, bridge_func, fake_bridge)
    monkeypatch.setattr(orchestrator, "get_all_settings", fake_settings)

    result = await orchestrator._run_single_tool(
        action, {"action": action, "payload": payload}, "do it", _creds(), workspace_id="ws-1"
    )

    assert json.loads(result)["status"] == "SUCCESS"
    _assert_delegation_call(action, captured, expected)
    assert captured["source_harness"] in ("satellite", "voice", "chat")


# --- AgentLoop dispatch -----------------------------------------------------


def test_agent_loop_advertises_and_routes_all_delegation_tools():
    for name in (
        "delegateravenrequest",
        "delegateworkspacerequest",
        "delegatelookuprequest",
        "delegatesubagentrequest",
        "delegatenotifyrequest",
    ):
        assert name in agent_loop.ALLOWED_TOOLS
        assert name in agent_loop.DELEGATION_ACTIONS


@pytest.mark.parametrize(
    ("action", "bridge_func", "payload", "expected"),
    [
        (
            "DelegateRavenRequest",
            "delegate_to_raven",
            {"goal": "build it"},
            {"goal": "build it"},
        ),
        (
            "DelegateWorkspaceRequest",
            "delegate_to_workspace",
            {"workspace_id": "ws-2", "prompt": "fix it"},
            {"workspace_id": "ws-2", "prompt": "fix it"},
        ),
        (
            "DelegateLookupRequest",
            "delegate_lookup",
            {"query": "weather"},
            {"query": "weather"},
        ),
        (
            "DelegateSubagentRequest",
            "delegate_explorer_subagent",
            {"question": "where?", "max_turns": 99},
            {"question": "where?", "max_turns": 6},
        ),
        (
            "DelegateNotifyRequest",
            "delegate_notify",
            {"message": "done"},
            {"message": "done"},
        ),
    ],
)
async def test_agent_loop_run_delegation_maps_every_tool(
    monkeypatch, action, bridge_func, payload, expected
):
    captured: dict = {}

    async def fake_bridge(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return json.dumps({"status": "SUCCESS", "summary": "ok"})

    monkeypatch.setattr(bridge, bridge_func, fake_bridge)

    result = await agent_loop.run_delegation(action, payload, _creds(), workspace_id="ws-1")

    assert result["status"] == "SUCCESS"
    _assert_delegation_call(action, captured, expected)
    assert captured["source_harness"] == "raven"


async def test_agent_loop_delegation_refusal_is_an_error_not_a_success(monkeypatch):
    async def refusing(*_args, **_kwargs):
        return json.dumps({"status": "REFUSED", "summary": "Delegation cycle refused."})

    monkeypatch.setattr(bridge, "delegate_to_raven", refusing)

    result = await agent_loop.run_delegation(
        "DelegateRavenRequest", {"goal": "loop"}, _creds()
    )
    assert result["status"] == "REFUSED"
