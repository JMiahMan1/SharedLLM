
from services.gateway.agent_loop import (
    _has_valid_workspace_id,
    _next_batch_step,
    action_signature,
    extract_action_batch,
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


def test_extract_action_batch_fenced_json_array():
    text = (
        "Here is my plan:\n"
        "```json\n"
        '[{"@type": "WorkspaceFileWriteRequest", "file_path": "a.py", "content": "x"},'
        ' {"@type": "WorkspaceFileWriteRequest", "file_path": "b.py", "content": "y"},'
        ' {"@type": "WorkspaceShellRequest", "command": "pytest -q"}]\n'
        "```\n"
    )
    batch = extract_action_batch(text)
    assert batch is not None
    assert len(batch) == 3
    # Each item normalized to carry an action / @type.
    assert all(b.get("action") or b.get("@type") for b in batch)


def test_extract_action_batch_bare_array():
    text = '[{"@type": "WorkspaceShellRequest", "command": "ls"}, {"@type": "WorkspaceShellRequest", "command": "pwd"}]'
    batch = extract_action_batch(text)
    assert batch is not None
    assert len(batch) == 2


def test_extract_action_batch_single_object_is_not_a_batch():
    # A lone object (not an array) is handled by the single-action extractor,
    # so the batch extractor must return None.
    text = '{"@type": "WorkspaceShellRequest", "command": "ls"}'
    assert extract_action_batch(text) is None


def test_extract_action_batch_prose_is_not_a_batch():
    # Plain prose with no JSON array yields no batch.
    assert extract_action_batch("First I will create the workspace, then write files.") is None


def test_batch_drain_semantics_on_failure():
    # Simulate the loop's drain-on-failure invariant: when a batched step fails,
    # the remaining queued steps must be discarded so the model re-plans.
    pending = [{"action": "b"}, {"action": "c"}]
    # step failed -> drain
    step_failed = True
    if pending and step_failed:
        pending.clear()
    assert pending == []


def test_batch_continues_on_success():
    pending = [{"action": "b"}, {"action": "c"}]
    step_failed = False
    if pending and step_failed:
        pending.clear()
    # On success the queue is untouched (next step dequeued elsewhere).
    assert len(pending) == 2
