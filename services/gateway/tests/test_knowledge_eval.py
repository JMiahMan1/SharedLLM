"""The on-demand context eval: its cases, its scorer, and its routing half.

The model half (does a real model call the right tool?) runs against the live
stack: ``scripts/eval_knowledge_tools.py`` or ``pytest -m live``.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from unittest.mock import AsyncMock

import pytest

from services.gateway import knowledge_eval as ke
from services.gateway import knowledge_map as km
from services.gateway import orchestrator
from services.gateway import tool_registry as tr


@pytest.fixture
def hub(monkeypatch):
    from services import config

    monkeypatch.setattr(config, "any_home_hub", lambda: True)
    monkeypatch.setattr(tr, "hub_tools_offered", lambda: True)
    km._cached_map.cache_clear()
    yield
    km._cached_map.cache_clear()


def test_the_cases_are_generic_and_well_formed():
    raw = pathlib.Path(ke.CASES_PATH).read_text(encoding="utf-8")
    cases = json.loads(raw)["cases"]
    assert len(cases) >= 15
    assert len({c["id"] for c in cases}) == len(cases)
    assert sum(c["tool"] is None for c in cases) >= 2, "keep cases where the right move is no tool"
    assert not re.search(r"\b(?:light|lock|climate|switch)\.[a-z_]+", raw), "no entity ids of a real home"


def test_every_case_routes_its_pre_fetch_and_names_a_mapped_tool(hub):
    assert ke.check_routing(hub=True) == []


def test_without_a_hub_the_hub_cases_are_left_out(monkeypatch):
    monkeypatch.setattr(tr, "hub_tools_offered", lambda: False)
    km._cached_map.cache_clear()
    ids = {c["id"] for c in ke.load_cases(hub=False)}
    assert "device_state" not in ids and "votd" in ids
    assert ke.check_routing(hub=False) == []
    km._cached_map.cache_clear()


@pytest.mark.parametrize("case, call, ok", [
    ({"id": "a", "tool": "BibleRequest", "args": {"action": "votd"}},
     {"action": "biblerequest", "payload": {"action": "votd"}}, True),
    ({"id": "b", "tool": "BibleRequest", "args": {"action": "votd"}},
     {"action": "biblerequest", "payload": {"sub_action": "votd"}}, True),
    ({"id": "c", "tool": "BibleRequest", "args": {"action": "votd"}},
     {"action": "biblerequest", "payload": {"action": "read"}}, False),
    ({"id": "d", "tool": "ContextSearchRequest", "args": {"collection_name": "user_facts"}},
     {"action": "contextsearchrequest", "payload": {"collection_name": "nextcloud_files"}}, False),
    ({"id": "e", "tool": "WebSearchRequest", "args": {}}, None, False),
    ({"id": "f", "tool": None, "args": {}}, None, True),
    ({"id": "g", "tool": None, "args": {}}, {"action": "websearchrequest", "payload": {}}, False),
])
def test_the_scorer(case, call, ok):
    assert ke.score(case, call)["ok"] is ok


def _model_that_calls(expected: bool):
    """A stand-in model: calls each case's expected tool (or, when not ``expected``, never)."""
    cases = {c["question"]: c for c in ke.load_cases(hub=True)}

    async def call_ollama(payload, use_chat=True):
        question = payload["messages"][-1]["content"].rsplit("[Question]\n", 1)[-1]
        case = cases[question]
        if not expected or case["tool"] is None:
            return {"message": {"content": "Here is my answer."}}
        return {"message": {"content": "```json\n" + json.dumps({"tool": case["tool"], **case["args"]}) + "\n```"}}

    return call_ollama


async def _run(monkeypatch, expected):
    monkeypatch.setattr(orchestrator, "call_ollama", _model_that_calls(expected))
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda name: (pathlib.Path(__file__).resolve().parents[3] / "prompts" / "single_turn_tool_guide.md").read_text())
    monkeypatch.setattr(orchestrator, "_fetch_rag_context", AsyncMock(return_value=""))
    return await ke.run("m", system_prompt="PERSONA", hub=True)


async def test_a_model_that_fetches_what_it_needs_passes_every_case(monkeypatch, hub):
    report = await _run(monkeypatch, expected=True)
    failed = [r for r in report["results"] if not r["ok"]]
    assert not failed, failed
    assert report["pass_rate"] == 1.0


async def test_a_model_that_never_fetches_fails_every_case_that_needs_a_tool(monkeypatch, hub):
    report = await _run(monkeypatch, expected=False)
    needs = sum(1 for c in ke.load_cases(hub=True) if c["tool"])
    assert report["cases"] - report["passed"] == needs


async def test_the_run_never_executes_a_tool(monkeypatch, hub):
    run_tool = AsyncMock(side_effect=AssertionError("the eval must not act"))
    monkeypatch.setattr(orchestrator, "_run_single_tool", run_tool)
    await _run(monkeypatch, expected=True)
    assert run_tool.await_count == 0


@pytest.mark.live
async def test_the_live_model_fetches_what_it_needs():
    """Against the deployed model (SHAREDLLM_RUN_LIVE=1 and KNOWLEDGE_EVAL_MODEL)."""
    model = os.environ.get("KNOWLEDGE_EVAL_MODEL")
    if not model:
        pytest.skip("set KNOWLEDGE_EVAL_MODEL to the model to evaluate")
    from services import config

    report = await ke.run(model, system_prompt="You are Jarvis, the family's assistant.", hub=config.any_home_hub())
    assert report["pass_rate"] >= 0.8, json.dumps(report, indent=1)
