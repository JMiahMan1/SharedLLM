"""Raven's agent loop is not offered hub tools while no home hub is set up (integrations step 3).

Same rule as the single-turn path (test_hub_tools_offered.py), applied to Raven's
prose prompt, its dispatcher and the context it is given: no device tools, no
device snapshot, no device index. All of it follows the startup configuration,
so a prompt is the same from one mission to the next.
"""
from pathlib import Path

import pytest

import services.config as config
from services.common.hub_login import NO_HUB_MESSAGE
from services.gateway import agent_loop
from services.gateway import tool_registry as tr
from services.gateway.orchestrator import _collections_for_query

PROMPTS = Path(__file__).resolve().parents[3] / "prompts"

RAVEN = (
    "You are Raven.\n"
    "- **[WORKSPACE TOOLCHAIN]**: CLI tools.\n"
    "- **[HOME ASSISTANT SNAPSHOT]**: use `HAConfigRequest` / `EntitySearchRequest` with them.\n"
    "- Call securityrequest to arm the alarm.\n"
    "- Use WorkspaceShellRequest for commands.\n"
)


@pytest.fixture
def no_hub(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "")


@pytest.fixture
def homehub_only(monkeypatch):
    monkeypatch.setattr(config, "HA_URL", "")
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.example.test:8123")


def test_the_prompt_drops_every_line_naming_a_hub_tool(no_hub):
    prompt = tr.hub_tool_prompt(RAVEN)
    assert "HAConfigRequest" not in prompt and "securityrequest" not in prompt
    assert "You are Raven." in prompt and "WorkspaceShellRequest" in prompt
    assert prompt.rstrip().endswith(tr.NO_HUB_TOOLS_NOTICE.strip())


def test_the_prompt_is_fixed_and_the_notice_said_once(no_hub):
    once = tr.hub_tool_prompt(RAVEN)
    assert tr.hub_tool_prompt(RAVEN) == once
    assert tr.hub_tool_prompt(once) == once
    assert once.count("No home hub is set up") == 1


def test_the_prompt_is_unchanged_with_a_hub(homehub_only):
    assert tr.hub_tool_prompt(RAVEN) == RAVEN


@pytest.mark.parametrize("name", ["raven_autonomous_protocol.md", "raven_plan_prompt.md"])
def test_the_shipped_raven_prompts_name_no_hub_tool_without_a_hub(no_hub, name):
    prompt = tr.hub_tool_prompt((PROMPTS / name).read_text())
    for tool in tr.HUB_TOOLS:
        assert tool.lower() not in prompt.lower()
    assert "WorkspaceCreateRequest" in prompt


@pytest.mark.parametrize("spelling", ["LightControlRequest", "lightcontrolrequest", "light_control_request"])
def test_any_spelling_of_a_hub_tool_is_withheld_without_a_hub(no_hub, spelling):
    assert tr.hub_tool_withheld(spelling)


def test_other_tools_are_never_withheld(no_hub):
    for name in ("WorkspaceShellRequest", "mediaplayrequest", "NoteRequest", "", "tool_name"):
        assert not tr.hub_tool_withheld(name)


def test_nothing_is_withheld_with_a_hub(homehub_only):
    assert not any(tr.hub_tool_withheld(name) for name in tr.HUB_TOOLS)


def test_every_hub_tool_is_one_the_dispatcher_knows():
    # The gate runs on the dispatcher's resolved name, so each hub tool must be a name it resolves to.
    assert {name.lower() for name in tr.HUB_TOOLS} <= agent_loop.ALLOWED_TOOLS


def test_the_dispatcher_answers_the_no_hub_message_for_a_hub_tool(no_hub):
    for name in tr.HUB_TOOLS:
        assert agent_loop._hub_tool_unavailable(name.lower()) == {"status": "ERROR", "message": NO_HUB_MESSAGE}
    assert agent_loop._hub_tool_unavailable("workspaceshellrequest") is None


def test_the_dispatcher_sends_hub_tools_with_a_hub(homehub_only):
    assert agent_loop._hub_tool_unavailable("lightcontrolrequest") is None


@pytest.mark.parametrize("query", ["what is on the calendar", "fix the code in my workspace"])
def test_no_device_index_is_searched_without_a_hub(no_hub, query):
    assert "ha_entities" not in _collections_for_query(query)
    assert "ha_entities" not in _collections_for_query(query, devices_first=True)


def test_the_device_index_is_searched_with_a_hub(homehub_only):
    assert _collections_for_query("turn the lamp on", devices_first=True)[0] == "ha_entities"
