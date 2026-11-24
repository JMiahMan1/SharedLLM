"""Regression tests for single-turn tool discriminator normalization.

When the model emits {"tool": "TimerRequest", "action": "add", ...} the
discriminator names the tool and action carries the verb. Single-turn must
route to TimerRequest with the verb preserved in payload.action, not to "add".
"""
from __future__ import annotations

from services.gateway import orchestrator


def _creds():
    return orchestrator.ResolvedCredentials(user="tester")


async def test_single_turn_timer_request_verb_action_routed_to_timer(monkeypatch):
    """Exact bug shape: tool discriminator + verb action must reach timer, not 'add'."""
    calls: list[dict] = []

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append({"action": action, "tool_data": tool_data})
        return "Timer set for 1 minute."

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "TimerRequest", "action": "add", "duration_str": "1 minute", "title": "Timer"}',
        "Done. Timer set for 1 minute.",
    ])

    async def _call_ollama(payload, **kwargs):
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: "## Available Tools\n")
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")

    answer = await orchestrator._single_turn_inference(
        query="Set a timer for one minute.",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="",
        history=[],
        creds=_creds(),
    )

    assert calls, "tool was never dispatched"
    assert calls[0]["action"] == "timerrequest"
    payload = calls[0]["tool_data"].get("payload", calls[0]["tool_data"])
    assert payload.get("action") == "add"
    assert payload.get("duration_str") == "1 minute"
    assert payload.get("title") == "Timer"
    assert answer == "Done. Timer set for 1 minute."


async def test_single_turn_homeassistant_device_control_shape_still_works(monkeypatch):
    """Device-control shape with tool discriminator must keep working."""
    calls: list[dict] = []

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append({"action": action, "tool_data": tool_data})
        return "Turned on."

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "HAServiceRequest", "action": "turn_on", "entity_id": "light.kitchen"}',
        "The kitchen light is on.",
    ])

    async def _call_ollama(payload, **kwargs):
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: "## Available Tools\n")
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "")

    answer = await orchestrator._single_turn_inference(
        query="Turn on the kitchen light.",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="",
        history=[],
        creds=_creds(),
    )

    assert calls, "tool was never dispatched"
    assert calls[0]["action"] == "haservicerequest"
    payload = calls[0]["tool_data"].get("payload", calls[0]["tool_data"])
    assert payload.get("action") == "turn_on"
    assert payload.get("entity_id") == "light.kitchen"
    assert answer == "The kitchen light is on."
