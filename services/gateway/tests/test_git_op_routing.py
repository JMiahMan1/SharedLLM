
from services.gateway.agent_loop import (
    _is_git_op_shell_payload,
    _route_workspace_shell_to_git,
)


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

