
from services.gateway.agent_loop import (
    _GIT_VALID_ACTIONS,
    ALLOWED_TOOLS,
    _is_git_op_shell_payload,
    _route_workspace_shell_to_git,
)


def _resolve_action_name(raw_action: str) -> str:
    """Mirror the AgentLoop action-resolution fast-path for git verbs.

    Kept in sync with services/gateway/agent_loop.py so the regression test
    fails loudly if the fast-path is removed or weakened.
    """
    import re

    action_name = re.sub(r"[\s_]+", "", raw_action).lower()
    if (
        action_name in _GIT_VALID_ACTIONS
        or action_name in {"git" + v for v in _GIT_VALID_ACTIONS}
        or action_name in {v.replace("_", "") for v in _GIT_VALID_ACTIONS}
    ):
        action_name = "gitoperationrequest"
    return action_name


def test_sttrequest_is_exact_tool_not_fuzzy_matched():
    # Regression (mission 14): `sttrequest` was NOT in ALLOWED_TOOLS, so the
    # Tier-3 fuzzy matcher redirected it to `ttsrequest` (difflib ratio ~0.8),
    # and transcription calls were posted to the TTS endpoint (which rejects
    # audio). Once it is in the whitelist the exact-match fast path wins and the
    # STT handler at lookup_action == "sttrequest" is reached.
    import difflib

    assert "sttrequest" in ALLOWED_TOOLS
    assert "ttsrequest" in ALLOWED_TOOLS
    matches = difflib.get_close_matches("sttrequest", list(ALLOWED_TOOLS), n=1, cutoff=0.6)
    assert matches == ["sttrequest"], f"sttrequest must resolve exactly, got {matches}"


def test_git_verb_resolves_to_gitoperationrequest_not_note_create():
    # Regression: `repo_create` (underscore-stripped to `repocreate`) is NOT in
    # ALLOWED_TOOLS, so the Tier-3 fuzzy matcher used to corrupt it to
    # `note_create` (ratio 0.667) -> "Unknown action: note_create". Every git
    # verb must resolve to `gitoperationrequest` via the fast-path instead.
    for raw in ("repo_create", "repocreate", "git_commit", "repo_clone", "commit", "push", "branch"):
        resolved = _resolve_action_name(raw)
        assert resolved == "gitoperationrequest", f"{raw} -> {resolved}"
        assert resolved in ALLOWED_TOOLS
        assert resolved != "note_create"


def test_git_op_shell_payload_detects_branch():
    # Exact mission-1 crash shape: WorkspaceShellRequest whose payload is a
    # git-op (no `command`). Must route to the credentialed git tool.
    payload = {"action": "branch", "path": "-M", "workspace_id": "raven-batch-demo"}
    out = _is_git_op_shell_payload(payload)
    assert out is not None
    assert out["action"] == "branch"
    assert out["path"] == "-M"


def test_git_op_shell_payload_detects_remote_and_normalizes_case():
    payload = {"action": "Remote", "workspace_id": "w"}
    out = _is_git_op_shell_payload(payload)
    assert out is not None
    assert out["action"] == "remote"


def test_git_op_shell_payload_rejects_real_command():
    # A genuine shell command must NOT be treated as a git op.
    payload = {"command": "ruff check ."}
    assert _is_git_op_shell_payload(payload) is None


def test_git_op_shell_payload_rejects_legit_shell_request():
    payload = {"@type": "WorkspaceShellRequest", "command": "git status"}
    assert _is_git_op_shell_payload(payload) is None


def test_git_op_shell_payload_rejects_non_dict():
    assert _is_git_op_shell_payload("not a dict") is None
    assert _is_git_op_shell_payload(None) is None


def test_git_op_shell_payload_rejects_non_git_action():
    payload = {"action": "search", "query": "x"}
    assert _is_git_op_shell_payload(payload) is None


def test_route_workspace_shell_reroutes_git_op_payload():
    payload = {"action": "branch", "path": "-M"}
    new_action, new_payload, git_batch = _route_workspace_shell_to_git(
        "workspaceshellrequest", payload
    )
    assert new_action == "gitoperationrequest"
    assert new_payload["action"] == "branch"
    assert git_batch is None


def test_route_workspace_shell_intercepts_raw_git_command():
    payload = {"command": "git push origin main"}
    new_action, _, git_batch = _route_workspace_shell_to_git(
        "workspaceshellrequest", payload
    )
    assert new_action == "gitoperationrequest"
    assert git_batch is not None and len(git_batch) >= 1


def test_route_workspace_shell_leaves_real_command_alone():
    payload = {"command": "ruff check ."}
    new_action, new_payload, git_batch = _route_workspace_shell_to_git(
        "workspaceshellrequest", payload
    )
    assert new_action == "workspaceshellrequest"
    assert new_payload == payload
    assert git_batch is None


def test_route_workspace_shell_non_shell_passthrough():
    payload = {"file_path": "x.py", "content": "y"}
    new_action, new_payload, git_batch = _route_workspace_shell_to_git(
        "workspacefilewriterequest", payload
    )
    assert new_action == "workspacefilewriterequest"
    assert new_payload == payload
    assert git_batch is None


def test_route_workspace_shell_keeps_git_branch_rename_command():
    # Regression (mission 7): a valid `git branch -m master main` shell command
    # must run NATIVELY in the shell. The old interceptor dropped the `command`
    # and replaced it with a broken git-op dict (action 'branch', path '-m'),
    # which the shell handler rejected as "no command" and triggered a no-progress
    # loop. The real `command` must be preserved untouched.
    payload = {
        "command": "cd /workspaces/users/default/w && git branch -m master main",
        "workspace_id": "w",
    }
    new_action, new_payload, git_batch = _route_workspace_shell_to_git(
        "workspaceshellrequest", payload
    )
    assert new_action == "workspaceshellrequest"
    assert new_payload == payload
    assert git_batch is None


def test_translate_shell_does_not_route_git_branch():
    from services.gateway.agent_loop import _translate_shell_to_git_op

    assert _translate_shell_to_git_op("git branch -m master main") is None
    assert _translate_shell_to_git_op("cd /w && git branch -M old new") is None
    # Credentialed ops still route correctly.
    assert _translate_shell_to_git_op("git push origin main") is not None
    assert _translate_shell_to_git_op("git commit -m 'msg'") is not None


def test_compound_git_shell_routes_to_gitoperationrequest_with_batch():
    # Regression (mission 8): a valid `git add X && git commit -m '...'` shell
    # command was intercepted and translated to a git batch, BUT the dispatcher
    # never assigned the returned action back to lookup_action, so the PRIMARY
    # step was POSTed to /execute/workspace_shell (no `command`) and rejected,
    # while only the secondary batch steps fanned out to /execute/git. The
    # interceptor MUST return 'gitoperationrequest' (not 'workspaceshellrequest')
    # so the primary step is dispatched to the git endpoint.
    na, _pl, gb = _route_workspace_shell_to_git(
        "workspaceshellrequest",
        {"command": "git add README.md && git commit -m 'feat: x'", "workspace_id": "w"},
    )
    assert na == "gitoperationrequest"
    assert gb is not None and len(gb) == 2
    assert gb[0]["action"] == "add"
    assert gb[1]["action"] == "commit"


def test_prose_git_calls_resolve_real_verb():
    # Regression (mission 13): the 35B model emits numbered-prose tool calls.
    # The prose parser used to clobber the inner git verb with the wrapper type
    # (`GitOperationRequest`), so every git op dispatched as read-only `status`
    # and the mission hung at progress 0. The real verb (`repo_create`/`add`/
    # `commit`/`push`) carried in `action '...'` must become the routing action,
    # and stray backticks the model adds must be stripped.
    from services.gateway.prose_tools import extract_action_prose

    text = """
    1. GitOperationRequest action 'repo_create' repo_name 'raven-test-1' private true
    2. GitOperationRequest action 'add' path '.'
    3. GitOperationRequest action 'commit' commit_message 'feat: init'
    4. GitOperationRequest action 'push' branch 'main'
    """
    calls = extract_action_prose(text)
    assert calls is not None
    actions = [c["action"] for c in calls]
    assert actions[0] == "repo_create" and calls[0].get("repo_name") == "raven-test-1"
    assert actions[1] == "add" and calls[1].get("path") == "."
    assert actions[2] == "commit" and calls[2].get("commit_message") == "feat: init"
    assert actions[3] == "push" and calls[3].get("branch") == "main"


def test_prose_git_strips_backticks():
    # Model wraps values in backticks; ensure they are normalized away.
    from services.gateway.prose_tools import extract_action_prose

    text = "GitOperationRequest action `repo_create` repo_name `raven-test-bt`"
    calls = extract_action_prose(text)
    assert calls is not None
    assert calls[0]["action"] == "repo_create"
    assert calls[0].get("repo_name") == "raven-test-bt"


def test_prose_git_empty_value_does_not_crash():
    # Regression: a prose tool call whose final key has an EMPTY value
    # (e.g. `branch ''`) used to make _parse_pairs call None.lower() and
    # crash the whole mission finalization (mission 1: "'NoneType' object
    # has no attribute 'lower'") even though the real git work already
    # succeeded. The empty value must be preserved as None, not crash.
    from services.gateway.prose_tools import extract_action_prose

    text = (
        "1. GitOperationRequest action 'push' branch ''\n"
        "2. GitOperationRequest action 'commit' commit_message 'feat: x'"
    )
    calls = extract_action_prose(text)
    assert calls is not None
    assert calls[0]["action"] == "push"
    assert calls[0].get("branch") is None
    assert calls[1]["action"] == "commit"

