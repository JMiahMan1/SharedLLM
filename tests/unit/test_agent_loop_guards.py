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


def _load_no_progress():
    from services.gateway.agent_loop import (
        detect_no_progress,
        normalize_shell_goal,
        outcome_digest,
    )
    return detect_no_progress, normalize_shell_goal, outcome_digest


def test_normalize_shell_goal_collapses_redirections():
    _, normalize_shell_goal, _ = _load_no_progress()
    a = normalize_shell_goal("SDL_VIDEODRIVER=dummy python main.py --selftest 2>&1")
    b = normalize_shell_goal(
        "SDL_VIDEODRIVER=dummy python main.py --selftest 2>/tmp/e; echo EXIT=$?; cat /tmp/e | tail -30"
    )
    assert a == b
    assert "2>&1" not in a
    assert "echo exit" not in a.lower()


def test_normalize_shell_goal_strips_env_and_truncates():
    _, normalize_shell_goal, _ = _load_no_progress()
    g = normalize_shell_goal("FOO=bar BAZ=1 pytest -q tests/")
    assert g.startswith("pytest -q tests/")
    assert "foo=bar" not in g


def test_outcome_digest_is_stable_tail():
    _, _, digest = _load_no_progress()
    d1 = digest({"message": "line1\nline2\nNameError: name 'IsKeyDown' is not defined\n  at run_selftest"})
    d2 = digest({"message": "line1\nline2\nNameError: name 'IsKeyDown' is not defined\n  at run_selftest"})
    assert d1 == d2
    assert "NameError" in d1


def test_detect_no_progress_true_same_goal_identical_output():
    detect, _, _ = _load_no_progress()
    outcomes = [
        ("python main.py --selftest", "NameError: IsKeyDown"),
        ("python main.py --selftest", "NameError: IsKeyDown"),
        ("python main.py --selftest", "NameError: IsKeyDown"),
        ("python main.py --selftest", "NameError: IsKeyDown"),
    ]
    assert detect(outcomes, window=4) is True


def test_detect_no_progress_false_when_distinct_goals():
    detect, _, _ = _load_no_progress()
    outcomes = [
        ("pytest -q", "fail a"),
        ("ruff check .", "fail b"),
        ("pytest -q", "fail a"),
        ("ruff check .", "fail b"),
    ]
    assert detect(outcomes, window=4) is False


def test_detect_no_progress_false_on_clean_pass():
    detect, _, _ = _load_no_progress()
    # identical EMPTY output = a clean repeated pass -> not a stuck loop
    outcomes = [
        ("ruff check .", ""),
        ("ruff check .", ""),
        ("ruff check .", ""),
        ("ruff check .", ""),
    ]
    assert detect(outcomes, window=4) is False


def test_detect_no_progress_false_below_window():
    detect, _, _ = _load_no_progress()
    outcomes = [
        ("python main.py --selftest", "NameError: IsKeyDown"),
        ("python main.py --selftest", "NameError: IsKeyDown"),
        ("python main.py --selftest", "NameError: IsKeyDown"),
    ]
    assert detect(outcomes, window=4) is False


def test_detect_no_progress_true_with_varied_command_strings():
    detect, normalize, _ = _load_no_progress()
    # Even though the literal commands differ, the normalized goals match.
    goals = [normalize("python main.py --selftest 2>&1"),
             normalize("python main.py --selftest 2>/tmp/e; echo EXIT=$?"),
             normalize("python main.py --selftest 2>&1"),
             normalize("python main.py --selftest 2>/tmp/e; echo EXIT=$?")]
    outcomes = [(g, "NameError: IsKeyDown") for g in goals]
    assert detect(outcomes, window=4) is True

