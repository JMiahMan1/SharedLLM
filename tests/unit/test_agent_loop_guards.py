"""Unit tests for the harness guard helpers in agent_loop.

These encode the OpenCode-orchestrator / Hermes lessons and run fast in CI
(they need no model or downstream service):
  * the runtime (not the model) decides when work is "done",
  * files written without lint/test must be verified before finishing,
  * the same failing step must not be retried forever (stagnation escalation).
"""


def _load():
    from services.gateway.agent_loop import (
        action_signature,
        detect_repetitive_failure,
        is_verification_action,
        pending_verification,
    )
    return action_signature, detect_repetitive_failure, is_verification_action, pending_verification


def test_action_signature_distinguishes_files_and_commands():
    action_signature, _, _, _ = _load()
    a = action_signature("WorkspaceFileWriteRequest", {"file_path": "game.py", "content": "x=1"})
    b = action_signature("WorkspaceFileWriteRequest", {"file_path": "readme.md"})
    c = action_signature("WorkspaceShellRequest", {"command": "ruff check ."})
    assert a != b
    assert a != c
    assert "game.py" in a
    assert "ruff check ." in c


def test_action_signature_handles_missing_payload():
    action_signature, _, _, _ = _load()
    assert action_signature("LightControlRequest", None).startswith("lightcontrolrequest::")
    assert action_signature("LightControlRequest", {}).endswith("''")


def test_detect_repetitive_failure_true_when_same_step_fails():
    _, detect, _, _ = _load()
    recent = [
        ("workspacefilewriterequest::'game.py'", False),
        ("workspacefilewriterequest::'game.py'", False),
        ("workspacefilewriterequest::'game.py'", False),
    ]
    assert detect(recent, window=3) is True


def test_detect_repetitive_failure_false_when_different_steps():
    _, detect, _, _ = _load()
    recent = [
        ("workspacefilewriterequest::'game.py'", False),
        ("workspaceshellrequest::'ls'", False),
        ("workspacefilewriterequest::'game.py'", False),
    ]
    assert detect(recent, window=3) is False


def test_detect_repetitive_failure_false_when_eventually_succeeds():
    _, detect, _, _ = _load()
    recent = [
        ("workspacefilewriterequest::'game.py'", False),
        ("workspacefilewriterequest::'game.py'", False),
        ("workspacefilewriterequest::'game.py'", True),
    ]
    assert detect(recent, window=3) is False


def test_detect_repetitive_failure_false_below_window():
    _, detect, _, _ = _load()
    recent = [
        ("x", False),
        ("x", False),
    ]
    assert detect(recent, window=3) is False


def test_is_verification_action_lint_tool():
    _, _, is_verify, _ = _load()
    assert is_verify("WorkspaceLintRequest", {"path": "game.py"}) is True


def test_is_verification_action_shell_test_command():
    _, _, is_verify, _ = _load()
    assert is_verify("WorkspaceShellRequest", {"command": "pytest -q"}) is True
    assert is_verify("WorkspaceShellRequest", {"command": "ruff check . && pytest"}) is True
    assert is_verify("WorkspaceShellRequest", {"command": "git push origin main"}) is False
    assert is_verify("WorkspaceShellRequest", {"command": "npm run build"}) is True


def test_is_verification_action_non_verify_tools():
    _, _, is_verify, _ = _load()
    assert is_verify("WorkspaceFileWriteRequest", {"file_path": "game.py"}) is False
    assert is_verify("GitOperationRequest", {"action": "push"}) is False


def test_pending_verification_reports_unverified_only():
    _, _, _, pending = _load()
    written = {"game.py", "readme.md"}
    verified = {"readme.md"}
    assert pending(written, verified) == ["game.py"]


def test_pending_verification_empty_when_all_verified():
    _, _, _, pending = _load()
    assert pending({"game.py"}, {"game.py", "other.py"}) == []
