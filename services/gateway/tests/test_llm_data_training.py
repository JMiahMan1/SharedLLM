"""Every data-providing channel must be taught to the LLM and wired end-to-end.

SharedLLM provides data through RAG collections, execution tools (Calibre,
Bible, Audiobookshelf, Home Assistant, Location, Notes/Calendar/Chores/Todos/
MealPlan, logs/telemetry), and the always-on protocol curriculum. These tests
pin all five layers together so a real capability cannot go unindexed,
undocumented, unrouted, or lose its citation metadata at dispatch time.
"""
from __future__ import annotations

import ast
import pathlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from services.execution import schemas as exec_schemas
from services.gateway import orchestrator
from services.gateway import schemas as gateway_schemas
from services.gateway.tool_builder import _TOOLS, decide
from services.gateway.tool_registry import _RAVEN_TOOL_TABLE
from services.rag import main as rag_main

ROOT = pathlib.Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "index_capabilities.py"
GUIDE_PATH = ROOT / "prompts" / "single_turn_tool_guide.md"

REAL_COLLECTIONS = {
    "calibre_files",
    "nextcloud_files",
    "home_devices",
    "system_capabilities",
    "system_learnings",
    "user_facts",
    "conversation_memory",
    "mission_history",
    "network_topology",
    "telemetry_alerts",
}

_INLINE_SCHEMA_TOOLS = {
    "RavenMissionRequest",
    "WorkspaceCreateRequest",
    "WorkspaceSettingsUpdateRequest",
    "STTRequest",
    "AiCapabilitiesRequest",
}


def _ast_dict(var_name: str) -> dict[str, str]:
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == var_name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"index_capabilities.py no longer defines {var_name}")


def test_every_live_raven_tool_is_in_capability_index():
    from services.gateway.studio import tools as studio_tools

    # The studio's tools are indexed from its own action table, not a literal.
    indexed = {**_ast_dict("schema_map"), **_ast_dict("storage_map"), **studio_tools.TOOL_ACTIONS}
    expected = {row[0] for row in _RAVEN_TOOL_TABLE if row[0] != "StorageListRequest"}
    expected |= {"LocationRequest", "RavenMissionRequest"}
    missing = sorted(expected - set(indexed))
    assert not missing, f"tools missing from scripts/index_capabilities.py: {missing}"
    assert "StorageListRequest" not in indexed


def test_every_capability_index_entry_resolves_to_a_schema():
    indexed = {**_ast_dict("schema_map"), **_ast_dict("storage_map")}
    unresolved = [
        name
        for name in indexed
        if name not in _INLINE_SCHEMA_TOOLS
        and not hasattr(exec_schemas, name)
        and not hasattr(gateway_schemas, name)
    ]
    assert not unresolved, f"capability index entries with no Pydantic or inline schema: {unresolved}"


def test_index_capabilities_sends_all_schema_map_entries(monkeypatch):
    import scripts.index_capabilities as idx_mod

    posted: dict = {}

    class _Resp:
        def __init__(self, status_code: int, text: str = "ok"):
            self.status_code = status_code
            self.text = text

    monkeypatch.setattr(idx_mod.requests, "get", lambda *a, **k: _Resp(200))

    def _fake_post(url, json=None, headers=None, timeout=None):
        posted["url"] = url
        posted["capabilities"] = json["capabilities"]
        return _Resp(200)

    monkeypatch.setattr(idx_mod.requests, "post", _fake_post)

    idx_mod.index_capabilities()

    sent_names = {c["name"] for c in posted["capabilities"]}
    indexed = {**_ast_dict("schema_map"), **_ast_dict("storage_map")}
    assert set(indexed) <= sent_names
    assert "StorageListRequest" not in sent_names
    for key in ("ContextSearchRequest", "WorkspaceBootstrapRequest", "ControlPlaneRequest", "AudiobookshelfRequest", "LocationRequest"):
        assert key in sent_names


def test_protocol_curriculum_teaches_all_collections_and_data_sources():
    rules = "\n".join(lesson["rule"] for lesson in rag_main._PROTOCOL_LESSONS)
    for coll in REAL_COLLECTIONS:
        assert coll in rules, f"{coll} not taught in _PROTOCOL_LESSONS"
    for tool in (
        "AudiobookshelfRequest",
        "EntitySearchRequest",
        "HAConfigRequest",
        "LogbookRequest",
        "CalendarRequest",
        "NoteRequest",
        "ChoreRequest",
        "TodoListRequest",
        "MealPlanRequest",
        "ExecutionLogRequest",
        "DockerLogsRequest",
        "RedisInspectRequest",
        "LLMInfoRequest",
        "AiCapabilitiesRequest",
        "NetworkDeviceScanRequest",
    ):
        assert tool in rules, f"{tool} not taught in _PROTOCOL_LESSONS"


def test_single_turn_guide_teaches_all_collections_and_audiobookshelf():
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    for coll in REAL_COLLECTIONS:
        assert coll in guide, f"{coll} missing from single_turn_tool_guide.md"
    assert "### AudiobookshelfRequest" in guide
    grounded = orchestrator._grounded_tool_guide(guide)
    assert "### AudiobookshelfRequest" in grounded
    assert guide.count("### ") - grounded.count("### ") == 3


@pytest.mark.parametrize(
    ("query", "expected_collection"),
    [
        ("what do you remember about my allergies?", "user_facts"),
        ("why did the previous Raven mission fail?", "mission_history"),
        ("which docker container exposes that port on the network?", "network_topology"),
        ("are there any recent telemetry alerts for offline sensors?", "telemetry_alerts"),
        ("check the intercom transcript for who said dinner is ready", "conversation_memory"),
    ],
)
def test_intent_routing_surfaces_specialized_rag_collections(query: str, expected_collection: str):
    collections = orchestrator._collections_for_query(query)
    assert expected_collection in collections
    assert orchestrator._collection_header(expected_collection) != expected_collection.upper()


async def test_context_search_tool_preserves_citation_locators(monkeypatch):
    class _Resp:
        status = 200

        async def json(self):
            return {
                "results": [
                    {
                        "content": "Trust in Him at all times.",
                        "metadata": {
                            "title": "Memories of Bethany",
                            "author": "John R. Macduff",
                            "chapter": "XIV.",
                        },
                    }
                ]
            }

    class _Client:
        async def post(self, *args, **kwargs):
            return _Resp()

    @asynccontextmanager
    async def _fake_client():
        yield _Client()

    monkeypatch.setattr(orchestrator, "get_all_settings", AsyncMock(return_value={"rag_svc_url": "http://rag"}))
    monkeypatch.setattr("services.gateway.main.shared_http_client", _fake_client)

    out = await orchestrator._execute_single_tool(
        "ContextSearchRequest",
        {"query": "trust in God", "collection_name": "calibre_files"},
        "trust in God",
        orchestrator.ResolvedCredentials(user="tester"),
    )
    assert "Memories of Bethany by John R. Macduff, chapter XIV." in out


async def test_context_search_refusal_names_all_real_collections():
    out = await orchestrator._execute_single_tool(
        "ContextSearchRequest",
        {"query": "anything"},
        "anything",
        orchestrator.ResolvedCredentials(user="tester"),
    )
    for coll in REAL_COLLECTIONS:
        assert coll in out, f"refusal message omitted {coll}"


def test_tool_builder_covers_data_and_observability_tools():
    names = {t.name for t in _TOOLS}
    for tool in (
        "AudiobookshelfRequest",
        "ContextSearchRequest",
        "EntitySearchRequest",
        "HAConfigRequest",
        "LogbookRequest",
        "ExecutionLogRequest",
        "CodeSearchRequest",
        "RedisInspectRequest",
        "NetworkDeviceScanRequest",
        "SystemLearningRequest",
    ):
        assert tool in names
    assert decide("search for an audiobook in audiobookshelf")["decision"] == "use_existing"
    assert decide("semantic search indexed knowledge in rag")["decision"] == "use_existing"


@pytest.mark.parametrize(
    "query",
    [
        "do I have permission to turn on the porch light?",
        "remind me to call grandma",
        "set an alarm for six",
        "how is the network today",
    ],
)
def test_common_words_do_not_pull_in_specialized_collections(query: str):
    # Every extra collection is one more search on the reply's path, voice included.
    collections = orchestrator._collections_for_query(query)
    for coll in ("user_facts", "mission_history", "network_topology", "telemetry_alerts", "conversation_memory"):
        assert coll not in collections, (query, coll)


@pytest.fixture
def no_hub(monkeypatch):
    from services import config

    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")


@pytest.fixture
def with_hub(monkeypatch):
    from services import config

    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.example.test:8123")


def test_tool_builder_never_suggests_a_hub_tool_without_a_hub(no_hub):
    decision = decide("find home assistant entity for the porch")
    names = {decision.get("tool")} | {s["tool"] for s in decision.get("steps", [])}
    assert not names & {"EntitySearchRequest", "HAConfigRequest", "LogbookRequest"}


def test_tool_builder_suggests_hub_tools_with_a_hub(with_hub):
    assert decide("find home assistant entity for the porch").get("tool") == "EntitySearchRequest"


def test_household_lesson_does_not_depend_on_a_hub(no_hub):
    from services.gateway.tool_registry import names_withheld_hub_tool

    lessons = {lesson["id"]: lesson["rule"] for lesson in rag_main._PROTOCOL_LESSONS}
    assert not names_withheld_hub_tool(lessons["lesson-proto-home-data"])
    assert names_withheld_hub_tool(lessons["lesson-proto-home-hub"])


def test_a_lesson_naming_a_hub_tool_is_withheld_without_a_hub(no_hub):
    from services.gateway.tool_registry import names_withheld_hub_tool

    assert names_withheld_hub_tool("Use EntitySearchRequest to find a device.")
    assert not names_withheld_hub_tool("Use CalendarRequest to read events.")
