"""Raven must be taught the Bible tool -- and grounding must NOT strip it.

The read-only BibleRequest tool is useless if no prompt ever advertises it:
the single-turn guide is the only place the Librarian and the single-task
mode learn what tools exist, ``scripts/index_capabilities.py`` is the only
place ``system_capabilities`` learns it, and the protocol curriculum is what
Raven carries into every mission.

These tests also pin the deliberate asymmetry with Calibre: ``biblerequest``
stays OUT of the grounded-turn omission lists. The Macbeth incident stripped
CalibreRequest from grounded turns because ``calibre_files`` passages are
already in the prompt -- but scripture is NOT in RAG (there is no bible
collection), so stripping BibleRequest would leave a grounded Bible question
with nothing to answer from.
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


def test_the_guide_advertises_bible_request_with_its_five_actions():
    section = _section("### BibleRequest")

    for action in ("`read`", "`search`", "`study_notes`", "`votd`", "`catalogue`"):
        assert action in section, f"the guide never names the {action} action"
    assert "NO write action" in section
    assert "NOT in RAG" in section, "the section must say scripture text is not preloaded"
    assert "{reference} ({version})" in section, "citations must name reference and version"


def test_the_guide_teaches_study_notes_and_catalogue_as_the_discovery_path():
    section = _section("### BibleRequest")

    assert "study_notes" in section
    assert "catalogue" in section
    assert "version" in section


def test_the_capability_index_advertises_bible_request():
    entry = _schema_map()["BibleRequest"]

    assert "study_notes" in entry
    assert entry.startswith("Holy Bible reader, read-only")
    assert "No state or mark writes" in entry


def test_the_capability_index_entry_resolves_to_a_real_schema():
    """A schema_map key with no matching class is silently skipped by the script."""
    from services.execution import schemas as exec_schemas

    assert hasattr(exec_schemas, "BibleRequest")


def test_grounding_deliberately_keeps_the_bible_tool():
    """Scripture is not in RAG context, so stripping this tool would leave a
    grounded Bible question unanswerable -- unlike calibre_files, whose
    passages are already in the prompt when a turn is grounded."""
    assert "biblerequest" not in orchestrator._GROUNDED_OMITTED_ACTIONS
    assert not any("Bible" in tool for tool in orchestrator._GROUNDED_OMITTED_TOOLS)


def test_the_grounded_guide_still_omits_exactly_the_three_search_tools():
    grounded = orchestrator._grounded_tool_guide(GUIDE)

    for header in ("### ContextSearchRequest", "### WebSearchRequest", "### CalibreRequest"):
        assert header not in grounded
    assert GUIDE.count("### ") - grounded.count("### ") == 3
    assert "### BibleRequest" in grounded, "grounding must not strip scripture"


async def test_a_grounded_turn_may_still_read_scripture(monkeypatch):
    calls: list[str] = []

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append(action)
        return "John 3:16 (nkjv): For God so loved the world..."

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "BibleRequest", "action": "read", "ref": "John 3:16"}',
        "A cited answer quoting the verse.",
    ])

    async def _call_ollama(payload, **kwargs):
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)

    answer = await orchestrator._single_turn_inference(
        query="What does John 3:16 say?",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="[LIBRARY] an unrelated retrieved passage",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
        grounded=True,
    )

    assert calls == ["biblerequest"], "a grounded turn must still be able to read scripture"
    assert answer == "A cited answer quoting the verse."


async def test_an_ungrounded_turn_may_also_read_scripture(monkeypatch):
    calls: list[str] = []

    async def _execute(action, tool_data, query, creds, workspace_id=None):
        calls.append(action)
        return "Psalm 23:1 (nkjv): The Lord is my shepherd."

    monkeypatch.setattr(orchestrator, "_execute_single_tool", _execute)

    replies = iter([
        '{"tool": "BibleRequest", "action": "search", "q": "shepherd"}',
        "A final answer.",
    ])

    async def _call_ollama(payload, **kwargs):
        return {"message": {"content": next(replies)}}

    monkeypatch.setattr(orchestrator, "call_ollama", _call_ollama)
    monkeypatch.setattr(orchestrator, "load_prompt_sync", lambda _name: GUIDE)

    answer = await orchestrator._single_turn_inference(
        query="What does scripture say about shepherds?",
        model="test-model",
        system_prompt="You answer carefully.",
        rag_context="",
        history=[],
        creds=orchestrator.ResolvedCredentials(user="tester"),
    )

    assert calls == ["biblerequest"]
    assert answer == "A final answer."
