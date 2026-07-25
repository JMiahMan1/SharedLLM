"""Tests for the numbered-prose tool-call parser (Raven 35B fallback)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.gateway.prose_tools import extract_action_prose


def test_recovers_full_push_chain():
    text = (
        "1. WorkspaceCreateRequest name 'pushtest-verify' display_name 'pushtest'\n"
        "2. GitOperationRequest action 'repo_create' repo_name 'pus-verify' private true\n"
        "3. GitOperationRequest action 'add' path 'README.md'\n"
        "4. GitOperationRequest action 'commit' commit_message '6push' branch 'main'\n"
        "5. GitOperationRequest action 'push' branch 'main'\n"
    )
    batch = extract_action_prose(text)
    assert batch is not None
    assert len(batch) == 5
    assert batch[0]["@type"] == "WorkspaceCreateRequest"
    assert batch[0]["payload"]["name"] == "pushtest-verify"
    # The critical step Raven was missing: the push tool call.
    assert batch[-1]["payload"]["action"] == "push"
    # repo_create must carry the private flag as a bool.
    assert batch[1]["payload"]["private"] is True


def test_no_false_positive_on_clean_json():
    # Clean JSON must be deferred to the JSON extractor, not prose-parsed.
    assert extract_action_prose('{"@type":"GitOperationRequest","action":"push"}') is None
    assert extract_action_prose('[{"@type":"GitOperationRequest","action":"push"}]') is None


def test_no_false_positive_on_plain_text():
    assert extract_action_prose("The mission is complete, all tests pass.") is None
    assert extract_action_prose("") is None


def test_handles_double_quotes_and_shell_command():
    text = (
        "1. WorkspaceShellRequest command \"gh repo create myrepo --private\" workspace_id 'w1'\n"
        "2. GitOperationRequest action 'push' branch 'main'\n"
    )
    batch = extract_action_prose(text)
    assert batch is not None
    assert batch[0]["@type"] == "WorkspaceShellRequest"
    assert "gh repo create" in batch[0]["payload"]["command"]
    assert batch[1]["payload"]["action"] == "push"


def test_handles_lowercased_type():
    text = "1. gitoperationrequest action 'push' branch 'main'"
    batch = extract_action_prose(text)
    assert batch is not None
    assert batch[0]["@type"] == "GitOperationRequest"
    assert batch[0]["payload"]["action"] == "push"


def test_returns_none_when_no_tool_type():
    # Prose without any Raven tool type must not fabricate calls.
    assert extract_action_prose("First I will think about the plan. Then I will write code.") is None


def test_settings_update_recovery():
    text = (
        "1. WorkspaceSettingsUpdateRequest workspace_id 'w1' "
        "repo_url 'https://github.com/JMiahMan1/x.git' default_branch 'main'"
    )
    batch = extract_action_prose(text)
    assert batch is not None
    assert batch[0]["@type"] == "WorkspaceSettingsUpdateRequest"
    assert batch[0]["payload"]["repo_url"].endswith("x.git")
