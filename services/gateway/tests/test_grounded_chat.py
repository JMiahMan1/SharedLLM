import pytest


def test_a_messages_system_prompt_is_used():
    from services.gateway import main

    assert main._chat_system_prompt({
        "messages": [
            {"role": "system", "content": "Answer from the passage only."},
            {"role": "user", "content": "Why?"},
        ],
    }) == "Answer from the passage only."


def test_a_top_level_system_prompt_wins():
    from services.gateway import main

    assert main._chat_system_prompt({
        "system": "Top level.",
        "messages": [{"role": "system", "content": "From messages."}],
    }) == "Top level."


def test_no_system_prompt_is_none():
    from services.gateway import main

    assert main._chat_system_prompt({
        "messages": [{"role": "user", "content": "Why?"}],
    }) is None


@pytest.mark.asyncio
async def test_a_grounded_turn_skips_rag_and_memory(monkeypatch):
    from services.gateway import orchestrator

    async def no_rag(*args, **kwargs):
        raise AssertionError("grounded turns must not retrieve household RAG context")

    async def no_history(*args, **kwargs):
        raise AssertionError("grounded turns must not load chat history")

    async def infer(query, model, system_prompt, rag_context, history, creds, callback, **kwargs):
        assert query == "Explain John 3:16"
        assert system_prompt == "You are a Bible study helper."
        assert rag_context == ""
        assert history == []
        assert kwargs["grounded"] is True
        return "A grounded answer."

    monkeypatch.setattr(orchestrator, "_fetch_rag_context", no_rag)
    monkeypatch.setattr(orchestrator, "get_history", no_history)
    monkeypatch.setattr(orchestrator, "_single_turn_inference", infer)

    answer = await orchestrator.process_full_orchestration({
        "query": "Explain John 3:16",
        "model": "assistant",
        "system": "You are a Bible study helper.",
        "creds": {"user": "jeremiah"},
        "grounded": True,
    })

    assert answer == "A grounded answer."
