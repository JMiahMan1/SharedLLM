
from services.gateway.agent_loop import (
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
