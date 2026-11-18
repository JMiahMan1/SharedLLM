"""Hub tools are offered to a model only while a home hub is set up (integrations step 2).

With neither HomeHub nor Home Assistant on, the device tools are left out of the
tool schemas, the workspace tool catalog and the single-turn tool guide. The
result depends only on the startup configuration, so the prompt stays the same
from one request to the next.
"""
import pytest

import services.config as config
from services.gateway import tool_registry as tr

GUIDE = (
    "# Single-Turn Tool Guide\n\n## Available Tools\n\n"
    "### LightControlRequest\nTurn lights on.\n\n"
    "### NoteRequest\nNotes.\n\n"
    "### TodoListRequest\nThe hub's lists.\n\n"
    "## Rules\n1. Be brief.\n"
)


@pytest.fixture
def no_hub(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")


@pytest.fixture
def homehub_only(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.example.test:8123")


def _schema_names() -> set[str]:
    return {s["function"]["name"] for s in tr.get_raven_tool_schemas()}


def test_every_hub_tool_is_a_real_row():
    rows = {row[0] for row in tr._RAVEN_TOOL_TABLE}
    assert rows >= tr.HUB_TOOLS


def test_no_hub_leaves_the_hub_tools_out(no_hub):
    assert not tr.hub_tools_offered()
    names = _schema_names()
    assert not (names & tr.HUB_TOOLS)
    assert "NoteRequest" in names and "MediaPlayRequest" in names
    catalog = tr.tool_catalog()
    assert "LightControlRequest" not in catalog and "TodoListRequest" not in catalog
    assert "WebSearchRequest" in catalog


def test_a_hub_offers_them(homehub_only):
    assert tr.hub_tools_offered()
    assert _schema_names() >= tr.HUB_TOOLS
    assert "LightControlRequest" in tr.tool_catalog()


def test_the_guide_drops_hub_sections_and_says_why(no_hub):
    guide = tr.hub_tool_guide(GUIDE)
    assert "### LightControlRequest" not in guide and "### TodoListRequest" not in guide
    assert "### NoteRequest" in guide and "## Rules" in guide
    assert "No home hub is set up" in guide
    # Fixed per configuration: the same text every time.
    assert tr.hub_tool_guide(GUIDE) == guide


def test_the_shipped_guide_keeps_no_hub_section_without_a_hub(no_hub):
    from pathlib import Path

    shipped = (Path(__file__).resolve().parents[3] / "prompts" / "single_turn_tool_guide.md").read_text()
    guide = tr.hub_tool_guide(shipped)
    for name in tr.HUB_TOOLS:
        assert f"### {name}" not in guide
    assert "### NoteRequest" in guide and "### MediaPlayRequest" in guide


def test_the_guide_is_unchanged_with_a_hub(homehub_only):
    assert tr.hub_tool_guide(GUIDE) == GUIDE


def test_routing_is_not_changed_by_the_switch(no_hub):
    # A model that names a hub tool anyway still reaches execution, which answers
    # the shared "no home hub" message instead of an unknown-tool error.
    assert "lightcontrolrequest" in tr.single_turn_tools()
