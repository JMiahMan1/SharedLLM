"""The knowledge map: every tool and knowledge source, generated from the registry.

The map replaced "preload everything just in case" retrieval: the model is told
where information lives and which tool fetches it, so these tests pin that the
map is complete (a new tool can never be invisible), honest (it lists nothing the
caller's path cannot run, and nothing for a hub that is not set up), and fixed
for a configuration (the prompt keeper keeps a system prompt warm only while it
does not change).
"""
from __future__ import annotations

import pathlib
import re

import pytest

from services.gateway import knowledge_map as km
from services.gateway import tool_registry as tr

GUIDE = (pathlib.Path(__file__).resolve().parents[3] / "prompts" / "single_turn_tool_guide.md").read_text(encoding="utf-8")


@pytest.fixture
def hub(monkeypatch):
    monkeypatch.setattr(tr, "hub_tools_offered", lambda: True)
    km._cached_map.cache_clear()
    yield
    km._cached_map.cache_clear()


@pytest.fixture
def no_hub(monkeypatch):
    monkeypatch.setattr(tr, "hub_tools_offered", lambda: False)
    km._cached_map.cache_clear()
    yield
    km._cached_map.cache_clear()


def _registered() -> set[str]:
    names = {row[0] for row in tr._RAVEN_TOOL_TABLE}
    names |= {s["function"]["name"] for s in tr.get_tool_schemas()}
    names |= set(tr.SINGLE_TURN_ONLY_DESCRIPTIONS) | set(tr.SINGLE_TURN_GARDEN_TOOLS)
    return names


def _tools_in(text: str) -> set[str]:
    listed = set(re.findall(r"^- ([A-Za-z_]+):", text, re.M))
    for line in re.findall(r"^- (?:described above|in an open workspace \(described there\)): (.*)$", text, re.M):
        listed |= {n.strip() for n in line.split(",")}
    return listed


def test_every_registered_tool_is_in_a_map_unless_marked_internal(hub):
    shown = _tools_in(km.knowledge_map_for("single_turn", is_admin=True, guide=GUIDE))
    shown |= _tools_in(km.knowledge_map_for("raven", is_admin=True))
    missing = sorted(n for n in _registered() - shown if n not in tr.MAP_INTERNAL)
    assert not missing, f"add these to the map (or to MAP_INTERNAL with a reason): {missing}"


def test_every_internal_entry_names_a_real_tool_and_gives_a_reason():
    registered = _registered()
    for name, reason in tr.MAP_INTERNAL.items():
        assert name in registered, f"MAP_INTERNAL names {name}, which is not registered"
        assert len(reason) > 15


def test_every_listed_tool_has_an_area(hub):
    tools = {**km.single_turn_tools(), **km.raven_tools()}
    unplaced = sorted(n for n in tools if n not in tr.MAP_INTERNAL and km.area_of(n) is None)
    assert not unplaced, f"give these an area in knowledge_map.TOOL_AREAS: {unplaced}"


def test_the_map_is_the_same_text_every_time_for_a_configuration(hub):
    first = km.build_knowledge_map(km.single_turn_tools(), is_admin=False, documented=["BibleRequest"])
    again = km.build_knowledge_map(dict(reversed(list(km.single_turn_tools().items()))),
                                   is_admin=False, documented=["BibleRequest"])
    assert first == again
    assert "Current Date" not in first and not re.search(r"\d{4}-\d{2}-\d{2}", first)


def test_the_map_says_nothing_is_preloaded_and_to_call_the_tool(hub):
    text = km.knowledge_map_for("single_turn", is_admin=False, guide=GUIDE)
    assert text.startswith("## Knowledge map")
    assert "Nothing listed here is in this conversation until you fetch it" in text
    assert "call its tool first" in text


def test_each_source_names_the_existing_tool_that_reads_it(hub):
    text = km.knowledge_map_for("single_turn", is_admin=True, guide=GUIDE)
    for src in tr.KNOWLEDGE_SOURCES:
        coll = re.search(r'"collection_name": "([a-z_]+)"', src.example)
        if coll:  # the indexed collections share one call shape, named once
            assert f"  - {coll.group(1)}: {src.holds}" in text, src.key
        else:
            assert src.example in text, src.key
        assert f'"tool": "{src.tool}"' in src.example
        assert src.tool in _registered(), f"{src.key} points at {src.tool}, which no path registers"


def test_a_source_is_only_offered_where_its_tool_runs(hub):
    raven = km.knowledge_map_for("raven", is_admin=True)
    # Raven has no row for these: the single-turn path alone runs them.
    assert '"tool": "LocationRequest"' not in raven
    assert '"tool": "garden_summary"' not in raven
    assert "  - system_learnings:" in raven


def test_without_a_hub_no_hub_tool_or_source_is_listed(no_hub):
    text = km.knowledge_map_for("single_turn", is_admin=True, guide=tr.hub_tool_guide(GUIDE))
    for name in tr.HUB_TOOLS:
        assert name not in _tools_in(text)
    assert "EntitySearchRequest" not in text and "TodoListRequest" not in text


def test_with_a_hub_the_home_sources_are_listed(hub):
    text = km.knowledge_map_for("single_turn", is_admin=False, guide=GUIDE)
    assert '"tool": "EntitySearchRequest"' in text
    assert '"tool": "TodoListRequest", "action": "list"' in text


def test_operations_tools_and_sources_are_for_admins_only(hub):
    household = km.knowledge_map_for("single_turn", is_admin=False, guide=GUIDE)
    admin = km.knowledge_map_for("single_turn", is_admin=True, guide=GUIDE)
    for name in ("DockerLogsRequest", "ControlPlaneRequest", "RedisInspectRequest"):
        assert name not in _tools_in(household)
        assert name in _tools_in(admin)
    assert "mission_history" not in household and "mission_history" in admin


def test_workspace_tools_are_named_not_described_on_the_chat_path(hub):
    text = km.knowledge_map_for("single_turn", is_admin=False, guide=GUIDE)
    assert "- StudioRunRequest:" not in text
    assert re.search(r"in an open workspace \(described there\): .*StudioRunRequest", text)
    # Raven always works in a workspace, so it gets them in full.
    assert "- StudioRunRequest:" in km.knowledge_map_for("raven", is_admin=False)


def test_the_household_chat_map_stays_short(hub):
    assert len(km.knowledge_map_for("single_turn", is_admin=False, guide=GUIDE)) < 5500


def test_tools_the_guide_documents_are_named_once_not_described_again(hub):
    text = km.knowledge_map_for("single_turn", is_admin=False, guide=GUIDE)
    assert "- BibleRequest:" not in text
    assert re.search(r"described above: .*BibleRequest", text)
    # The guide's combined heading counts for both of its tools.
    assert "- StorageFileWriteRequest:" not in text


def test_each_line_is_one_short_purpose():
    long = "Calendar: read events (action=read; use range_start/range_end; answers what is next). More."
    assert km.short_purpose(long).endswith("...")
    assert len(km.short_purpose(long)) <= km.PURPOSE_MAX_CHARS + 3
    assert km.short_purpose("Read a file. Then more.") == "Read a file."


def test_the_map_carries_no_household_literals(hub):
    text = km.knowledge_map_for("single_turn", is_admin=True, guide=GUIDE)
    # Every example is generic: no entity ids, names or addresses of a real home.
    assert not re.search(r"\b(?:light|switch|climate|media_player)\.[a-z_]+\b", text)
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)


def test_an_unknown_surface_is_refused():
    with pytest.raises(ValueError):
        km.knowledge_map_for("telepathy", is_admin=False)


def test_the_single_turn_surface_lists_only_tools_it_can_dispatch(hub):
    from services.gateway import orchestrator

    dispatchable = set(orchestrator.SINGLE_TURN_TOOL_ENDPOINTS) | {"controlplanerequest"}
    dispatchable |= set(orchestrator._GARDEN_ACTIONS)
    dispatchable |= set(orchestrator.DELEGATION_ACTIONS)
    from services.gateway.studio import tools as studio_tools

    dispatchable |= {n.lower() for n in studio_tools.TOOL_ACTIONS}
    shown = _tools_in(km.knowledge_map_for("single_turn", is_admin=True, guide=GUIDE))
    undispatchable = sorted(n for n in shown if re.sub(r"[\s_]+", "", n).lower() not in dispatchable)
    assert not undispatchable, f"the map offers tools chat cannot run: {undispatchable}"
