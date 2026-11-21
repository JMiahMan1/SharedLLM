"""The raw internal completion route: the model and the caller's messages only.

Garden drafts species profiles with it; ``/v1/chat/completions`` would run the
same text as the household, with fast paths (a draft containing "water monty"
could water Monty), tools and memory.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.config import INTERNAL_SECRET

URL = "/api/internal/llm/complete"


@pytest.fixture()
def model(monkeypatch):
    seen: list[tuple[str, list[dict], dict]] = []
    state = {"answer": "<think>hmm</think>{\"common_name\": \"Fern\"}", "raises": None}

    class Provider:
        async def generate(self, model, messages, options=None, chunk_callback=None):
            if state["raises"]:
                raise state["raises"]
            seen.append((model, messages, options or {}))
            return state["answer"]

    async def settings():
        return {}

    async def provider(_settings):
        return Provider()

    async def assistant():
        return "assistant-model"

    async def never(*_a, **_k):
        raise AssertionError("the raw route must not run the chat fast paths")

    monkeypatch.setattr(gateway_main, "get_llm_settings", settings)
    monkeypatch.setattr(gateway_main, "get_provider", provider)
    monkeypatch.setattr(gateway_main, "get_assistant_model", assistant)
    monkeypatch.setattr(gateway_main.fast_garden, "try_handle", never)
    return TestClient(gateway_main.app), seen, state


def test_it_needs_the_internal_secret(model):
    client, seen, _ = model

    assert client.post(URL, json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 403
    assert seen == []


@pytest.mark.parametrize("body", ({}, {"messages": []}, {"messages": [{"role": "tool", "content": "x"}]}))
def test_a_bad_body_is_a_422(model, body):
    client, _, _ = model

    assert client.post(URL, json=body, headers={"X-Internal-Secret": INTERNAL_SECRET}).status_code == 422


def test_it_sends_only_the_callers_messages(model):
    client, seen, _ = model
    messages = [{"role": "system", "content": "Draft JSON."}, {"role": "user", "content": "water monty"}]

    resp = client.post(URL, json={"messages": messages, "temperature": 0.1, "max_tokens": 99999},
                       headers={"X-Internal-Secret": INTERNAL_SECRET})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"content": "{\"common_name\": \"Fern\"}", "model": "assistant-model"}
    (_model, sent, options), = seen
    assert sent == messages
    assert options == {"temperature": 0.1, "num_predict": 4096}


def test_a_model_failure_is_a_502(model):
    client, _, state = model
    state["raises"] = RuntimeError("ollama down")

    resp = client.post(URL, json={"messages": [{"role": "user", "content": "x"}]},
                       headers={"X-Internal-Secret": INTERNAL_SECRET})

    assert resp.status_code == 502
    assert "RuntimeError" in resp.json()["detail"]
