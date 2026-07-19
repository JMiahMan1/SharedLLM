
from services.gateway.agent_loop import (
    _has_valid_workspace_id,
    _next_batch_step,
    action_signature,
    is_verification_action,
    outcome_digest,
)


def test_next_batch_step_logic():
    # Empty batch
    skip, tool = _next_batch_step([])
    assert skip is False
    assert tool is None

    # Non-empty batch
    batch = [{"action": "first"}, {"action": "second"}]
    skip, tool = _next_batch_step(batch)
    assert skip is True
    assert tool == {"action": "first"}
    assert len(batch) == 1
    assert batch[0] == {"action": "second"}

def test_is_verification_action():
    assert is_verification_action("workspacelintrequest", {}) is True
    assert is_verification_action("workspaceshellrequest", {"command": "ruff check ."}) is True
    assert is_verification_action("workspaceshellrequest", {"command": "pytest"}) is True
    assert is_verification_action("workspaceshellrequest", {"command": "python main.py"}) is False

def test_action_signature():
    assert action_signature("WorkspaceFileWriteRequest", {"file_path": "game.py"}) == "workspacefilewriterequest::'game.py'"
    assert action_signature("WorkspaceShellRequest", {"command": "ruff check"}) == "workspaceshellrequest::'ruff check'"

def test_outcome_digest():
    assert outcome_digest({"message": "Success!"}) == "Success!"
    assert outcome_digest("not a dict") == "na"
    assert outcome_digest({"detail": "Error occurred somewhere deep"}) == "Error occurred somewhere deep"


def test_has_valid_workspace_id():
    # Valid ids
    assert _has_valid_workspace_id("raven-proj") is True
    assert _has_valid_workspace_id("  ws-1  ") is True
    # Blank / unassigned -> must be treated as invalid so the guard fires and
    # we never send a workspace_shell with no id (execution service 400).
    assert _has_valid_workspace_id(None) is False
    assert _has_valid_workspace_id("") is False
    assert _has_valid_workspace_id("   ") is False
    assert _has_valid_workspace_id("\t\n") is False
