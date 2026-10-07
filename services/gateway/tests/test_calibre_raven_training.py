"""Raven must be taught the Calibre shelf, and a grounded turn must still win.

The read-only CalibreRequest tool (ea0bdc13) is useless if no prompt ever
advertises it: the single-turn guide is the only place the Librarian and the
single-task mode learn what tools exist, and ``scripts/index_capabilities.py``
is the only place ``system_capabilities`` learns it. These tests pin that
teaching -- and that adding the section does not reopen the grounded-turn door
closed by the Macbeth incident (a grounded turn must still refuse every
search/fetch tool, Calibre included).
"""
from __future__ import annotations

import ast
import pathlib

from services.gateway import orchestrator

GUIDE_PATH = pathlib.Path(__file__).resolve().parents[3] / "prompts" / "single_turn_tool_guide.md"
GUIDE = GUIDE_PATH.read_text(encoding="utf-8")
SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "index_capabilities.py"


def _schema_map() -> dict:
    """Literal-eval the schema_map without executing the indexing script."""
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "schema_map" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("index_capabilities.py no longer defines schema_map")


def _section(header: str) -> str:
    start = GUIDE.index(header)
    end = GUIDE.find("\n### ", start + 1)
    if end == -1:
        end = GUIDE.index("\n## ", start)
    return GUIDE[start:end]


def test_the_guide_advertises_calibre_request_with_its_four_actions():
    section = _section("### CalibreRequest")

    for action in ("`list`", "`search`", "`get_book`", "`fetch_text`"):
        assert action in section, f"the guide never names the {action} action"
    assert "NO delete" in section
    assert "calibre_files" in section, "the section should cross-reference the RAG collection"


def test_the_guide_teaches_context_search_to_name_a_collection():
    section = _section("### ContextSearchRequest")

    assert "collection_name" in section
    assert "REQUIRED" in section
    assert "calibre_files" in section


def test_the_capability_index_advertises_calibre_request():
    entry = _schema_map()["CalibreRequest"]

    assert "fetch_text" in entry
    assert entry.startswith("Read-only")


def test_the_capability_index_entry_resolves_to_a_real_schema():
    """A schema_map key with no matching class is silently skipped by the script."""
    from services.execution import schemas as exec_schemas

    assert hasattr(exec_schemas, "CalibreRequest")


async def test_a_grounded_turn_refuses_the_calibre_fetch(monkeypatch):
    calls: list[str] = []
    seen_messages: list[list[dict]] = []

    async def _execute(action, tool_data, query, creds):
        calls.append(action)
        return "executed"

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "CalibreRequest", "action": "fetch_text", "book_id": 617}',
        "A cited answer from the retrieved passages.",
    ])

    async def _call_ollama(payload, **kwargs):
        seen_messages.append(payload["messages"])
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)

    answer = await orchestrator._single_turn_inference(
        query="What did Macduff say about poverty?",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="[LIBRARY] a passage about trusting God in trial",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
        grounded=True,
    )

    assert calls == [], "the Calibre fetch ran even though passages were already loaded"
    handbacks = [
        m["content"]
        for turn in seen_messages
        for m in turn
        if "Tool result:" in m.get("content", "")
    ]
    assert handbacks, "no tool result was ever handed back to the model"
    assert all("not available for this turn" in h for h in handbacks)
    assert answer == "A cited answer from the retrieved passages."


async def test_an_ungrounded_turn_may_still_read_a_book(monkeypatch):
    calls: list[str] = []

    async def _execute(action, tool_data, query, creds):
        calls.append(action)
        return "Memories of Bethany by John R. Macduff (617)"

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "CalibreRequest", "action": "search", "query": "macduff"}',
        "A final answer.",
    ])

    async def _call_ollama(payload, **kwargs):
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)

    answer = await orchestrator._single_turn_inference(
        query="Which Macduff books do I own?",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
    )

    assert calls == ["calibrerequest"]
    assert answer == "A final answer."


def test_the_grounded_guide_still_omits_exactly_the_three_tools():
    grounded = orchestrator._grounded_tool_guide(GUIDE)

    for header in ("### ContextSearchRequest", "### WebSearchRequest", "### CalibreRequest"):
        assert header not in grounded
    assert GUIDE.count("### ") - grounded.count("### ") == 3
