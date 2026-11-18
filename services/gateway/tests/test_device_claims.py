"""A device command the model answered may only claim what a tool did in that turn.

The replies below are the shape of what happened on 2026-11-18 (names made up): one
bulb tried and unavailable, then "the other three should be off now"; asked again,
no tool at all and the same answer, copied from the voice history.
"""
from __future__ import annotations

import pytest

from services.gateway import device_claims, orchestrator

MADE_UP = ("I tried to turn off all the pantry lights, but Pantry Light 1 isn't responding. "
           "The other three pantry lights should be off now. Would you like me to check it?")
UNAVAILABLE = ("Sorry, I couldn't complete that action: The light 'Pantry Light 1' (light.pantry_1) is currently "
               "unavailable on the home hub. The device may be powered off.")


@pytest.mark.parametrize("query, state", [
    ("Turn off the pantry lights.", "off"), ("turn the den lamp on", "on"), ("Please switch on the porch", "on"),
    ("Is the den lamp on?", None), ("What turns the lights off at night", None), ("play some music", None),
])
def test_what_counts_as_a_device_command(query, state):
    assert device_claims.device_command(query) == state


def test_a_claim_with_no_device_tool_is_replaced():
    out = device_claims.honest_reply("Turn off the pantry lights.", MADE_UP, [])
    assert out == "I didn't switch anything just now, so I can't say it's off. Please ask me again."


def test_a_claim_after_a_failed_tool_says_what_failed():
    out = device_claims.honest_reply("Turn off the pantry lights.", MADE_UP, [("lightcontrolrequest", UNAVAILABLE)])
    assert out == ("I couldn't turn that off, so nothing was switched. The light 'Pantry Light 1' (light.pantry_1) "
                   "is currently unavailable on the home hub.")


@pytest.mark.parametrize("query, answer, runs", [
    # A tool did switch something: the model's wording stands.
    ("Turn off the pantry lights.", "The pantry lights are off.", [("haservicerequest", "Done: light.turn_off")]),
    # No claim: a question back is fine.
    ("Turn off the pantry lights.", "Which pantry light do you mean?", []),
    # Not a device command.
    ("Is the den lamp on?", "The den lamp is on.", []),
])
def test_honest_answers_stand(query, answer, runs):
    assert device_claims.honest_reply(query, answer, runs) == answer


def test_a_repeated_request_is_not_shown_its_old_answer():
    history = [
        {"role": "user", "content": "Turn off the pantry lights."},
        {"role": "assistant", "content": MADE_UP},
        {"role": "user", "content": "What time is it?"},
        {"role": "assistant", "content": "Ten past ten."},
    ]
    kept = orchestrator._voice_history(history, "turn off the pantry lights")
    assert [m["content"] for m in kept] == ["What time is it?", "Ten past ten."]
    # Something else asked: the history is as it was (a follow-up needs it).
    assert len(orchestrator._voice_history(history, "and the porch?")) == 4


async def test_the_model_cannot_claim_a_switch_it_did_not_make(monkeypatch):
    """The second voice turn: no tool call, the old answer word for word. It is not said."""
    async def fake_call(payload, **kwargs):
        return {"message": {"content": MADE_UP}}

    monkeypatch.setattr(orchestrator, "call_ollama", fake_call)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _n: "Tools: LightControlRequest")
    answer = await orchestrator._single_turn_inference(
        query="Turn off the pantry lights.", model="m", system_prompt="S", rag_context="", history=[],
        creds=orchestrator.ResolvedCredentials(user="u"))
    assert answer == "I didn't switch anything just now, so I can't say it's off. Please ask me again."


async def test_after_a_failed_bulb_the_model_cannot_say_the_rest_are_off(monkeypatch):
    """The first voice turn: one bulb tried and unavailable, then a claim about three others."""
    replies = iter(['{"tool": "LightControlRequest", "action": "turn_off", "entity_id": "light.pantry_1"}', MADE_UP])

    async def fake_call(payload, **kwargs):
        return {"message": {"content": next(replies)}}

    async def fake_exec(action, tool_data, query, creds, workspace_id=None):
        return UNAVAILABLE

    monkeypatch.setattr(orchestrator, "call_ollama", fake_call)
    monkeypatch.setattr(orchestrator, "_execute_single_tool", fake_exec)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _n: "Tools: LightControlRequest")
    answer = await orchestrator._single_turn_inference(
        query="Turn off the pantry lights.", model="m", system_prompt="S", rag_context="", history=[],
        creds=orchestrator.ResolvedCredentials(user="u"))
    assert answer.startswith("I couldn't turn that off, so nothing was switched.")
    assert "should be off" not in answer
