"""The household persona prompt rides at the END of the assistant's system message.

At the top of a ~20K-character system message the model followed the tool guide and
ignored the character, so the persona is appended last. It must be the same text for
every question (the model server keeps that prefix warm) and only the assistant path
gets it.
"""
from __future__ import annotations

import logging

import pytest

from services.gateway import orchestrator

PERSONA = "# Jarvis: Who You Are\nA cheerful dad-like character."


def _creds():
    return orchestrator.ResolvedCredentials(user="tester")


@pytest.fixture
def captured(monkeypatch):
    systems: list[str] = []

    async def _call_ollama(payload, **kwargs):
        systems.append(payload["messages"][0]["content"])
        return {"message": {"content": "Blue, like the sky."}}

    prompts = {"single_turn_tool_guide": "## Available Tools\n", "assistant_persona": PERSONA}

    def _load(name):
        if name not in prompts:
            raise ValueError(f"Prompt not found in settings DB: {name}")
        return prompts[name]

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", _load)
    monkeypatch.setattr(orchestrator, "knowledge_map_for", lambda *a, **k: "KNOWLEDGE MAP")
    monkeypatch.setattr(orchestrator, "_PERSONA_WARNED", False)
    return systems, prompts


async def _ask(question: str, persona: bool) -> str:
    return await orchestrator._single_turn_inference(
        query=question, model="test-model", system_prompt="You are the voice assistant.",
        rag_context="", history=[], creds=_creds(), persona=persona,
    )


async def test_the_persona_is_the_last_thing_in_the_system_message(captured):
    systems, _ = captured
    await _ask("What is your favorite color?", persona=True)
    assert systems[0].rstrip().endswith(PERSONA)
    assert systems[0].index("KNOWLEDGE MAP") < systems[0].index(PERSONA)


async def test_the_system_message_is_the_same_for_every_question(captured):
    systems, _ = captured
    await _ask("What is your favorite color?", persona=True)
    await _ask("Who wrote Romans?", persona=True)
    assert systems[0] == systems[1]


async def test_other_prompts_do_not_get_the_persona(captured):
    systems, _ = captured
    await _ask("Fix this function", persona=False)
    assert PERSONA not in systems[0]


async def test_a_missing_persona_setting_warns_once_and_still_answers(captured, caplog):
    systems, prompts = captured
    del prompts["assistant_persona"]
    with caplog.at_level(logging.WARNING):
        assert await _ask("Hello", persona=True) == "Blue, like the sky."
        await _ask("Hello again", persona=True)
    warnings = [r for r in caplog.records if "assistant_persona" in r.getMessage()]
    assert len(warnings) == 1
    assert PERSONA not in systems[0]
