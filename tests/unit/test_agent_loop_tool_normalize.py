"""Unit tests for agent_loop._normalize_tool - the tool-call schema normalizer.

These guard against the recurring "Neither 'command' nor 'commands' provided"
class of bugs by proving the normalizer extracts the command from every shape
Raven (or an OpenAI-style client) might emit.
"""


def _load():
    from services.gateway.agent_loop import _normalize_tool
    return _normalize_tool


def test_top_level_at_type_command():
    norm = _load()({"@type": "WorkspaceShellRequest", "command": "ruff check ."})
    assert norm["action"] == "WorkspaceShellRequest"
    assert norm["command"] == "ruff check ."


def test_openai_function_arguments_string():
    obj = {
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "WorkspaceShellRequest",
                    "arguments": '{"command": "git status"}',
                },
            }
        ]
    }
    norm = _load()(obj)
    assert norm["action"] == "WorkspaceShellRequest"
    assert norm["command"] == "git status"


def test_type_plus_params_command():
    obj = {
        "tool_calls": [
            {
                "id": "call_1",
                "type": "workspaceshellrequest",
                "params": {"command": "gh repo create x --private"},
            }
        ]
    }
    norm = _load()(obj)
    assert norm["action"] == "workspaceshellrequest"
    assert norm["command"] == "gh repo create x --private"


def test_deeply_nested_command():
    obj = {"tool": "WorkspaceShellRequest", "payload": {"data": {"command": "ls -la"}}}
    norm = _load()(obj)
    assert norm["action"] == "WorkspaceShellRequest"
    assert norm["command"] == "ls -la"


def test_file_write_path_normalization():
    obj = {"@type": "WorkspaceFileWriteRequest", "path": "game.py", "content": "x=1"}
    norm = _load()(obj)
    assert norm["action"] == "WorkspaceFileWriteRequest"
    assert norm["file_path"] == "game.py"
    assert norm["content"] == "x=1"


def test_openai_function_as_list():
    """Regression: some clients emit "function": [...] (the tool_calls array
    shape). The normalizer must take the first element instead of calling
    .get() on a list and raising "'list' object has no attribute 'get'."""
    obj = {
        "function": [
            {"name": "WorkspaceShellRequest", "arguments": '{"command": "git push"}'}
        ]
    }
    norm = _load()(obj)
    assert norm is not None
    assert norm["action"] == "WorkspaceShellRequest"
    assert norm["command"] == "git push"


def test_extract_action_json_array_of_calls():
    """Regression: a bare JSON array of tool calls must normalize to a dict
    (the first call), never crash on a list."""
    from services.gateway.agent_loop import extract_action_json

    text = '[{"function": {"name": "WorkspaceShellRequest", "arguments": "{\\"command\\": \\"ls\\"}"}}]'
    norm = extract_action_json(text)
    assert isinstance(norm, dict)
    assert norm["action"] == "WorkspaceShellRequest"
    assert norm["command"] == "ls"


def test_extract_action_json_unescaped_newline_in_payload():
    """Regression: a model that leaks a raw newline inside a JSON string value
    must still parse after control-char repair."""
    from services.gateway.agent_loop import extract_action_json

    text = '{"action": "WorkspaceFileWriteRequest", "file_path": "game.py", "content": "def f():\n    return 1\n"}'
    norm = extract_action_json(text)
    assert isinstance(norm, dict)
    assert norm["action"] == "WorkspaceFileWriteRequest"
    assert "def f()" in norm["content"]


def test_repair_json_control_chars_preserves_escapes():
    """The repair must not double-escape already-escaped sequences or touch
    structural whitespace outside of string values."""
    from services.gateway.agent_loop import _repair_json_control_chars

    src = '{"a": "line1\\nline2", "b": 1}'
    out = _repair_json_control_chars(src)
    assert out == src  # already-valid JSON is returned unchanged


def test_repair_json_control_chars_escapes_raw_newline():
    from services.gateway.agent_loop import _repair_json_control_chars

    src = '{"a": "line1\nline2"}'
    out = _repair_json_control_chars(src)
    assert out == '{"a": "line1\\nline2"}'


def test_extract_action_json_no_infinite_recursion_on_unparseable():
    """Regression: an unparseable model output that still contains a quoted
    string previously triggered unbounded recursion in extract_action_json.

    The control-char repair path used ``re.sub``, which returns a *new* string
    object (different identity) on every match even when the replacement text
    is identical. The old guard ``repaired is not text`` was therefore always
    True, so a persistently-unparseable payload (common with the local 35B
    model) recursed forever and raised ``RecursionError`` — killing the entire
    mission job before any tool action (e.g. a git commit/push) could run.

    The fix compares by CONTENT (``!=``) and bounds the recursion depth, so an
    unparseable payload now degrades to ``None`` (the caller re-prompts)
    instead of crashing the mission.
    """
    from services.gateway.agent_loop import extract_action_json

    # Unterminated JSON (missing closing braces) that still contains a quoted
    # string — the exact shape that sent the old code into infinite recursion.
    text = '{"action": "WorkspaceShellRequest", "payload": {"command": "git push"'
    norm = extract_action_json(text)
    assert norm is None


def _translate():
    from services.gateway.agent_loop import _translate_shell_to_git_op
    return _translate_shell_to_git_op


def test_translate_git_status():
    assert _translate()("git status") == {"action": "status"}


def test_translate_git_add_path():
    assert _translate()("git add game.py") == {"action": "add", "path": "game.py"}
    assert _translate()("git add .") == {"action": "add", "path": "."}


def test_translate_git_commit_message():
    assert _translate()('git commit -m "feat: add game"') == {
        "action": "commit",
        "commit_message": "feat: add game",
    }


def test_translate_git_push_branch():
    assert _translate()("git push origin main") == {"action": "push", "branch": "main"}
    assert _translate()("git push -u origin HEAD:main") == {"action": "push", "branch": "main"}
    assert _translate()("git push") == {"action": "push", "branch": "microservices"}


def test_translate_git_pull_branch():
    assert _translate()("git pull origin dev") == {"action": "pull", "branch": "dev"}


def test_translate_git_log_count():
    assert _translate()("git log --oneline -5") == {"action": "log", "log_count": 5}
    assert _translate()("git log") == {"action": "log", "log_count": 10}


def test_translate_git_fetch_branch_checkout_init():
    assert _translate()("git fetch") == {"action": "fetch"}
    assert _translate()("git branch feature-x") == {"action": "branch", "path": "feature-x"}
    assert _translate()("git checkout main") == {"action": "checkout", "path": "main"}
    assert _translate()("git init") == {"action": "init"}


def test_translate_git_remote_add():
    assert _translate()("git remote add origin https://github.com/u/r.git") == {
        "action": "remote_add",
        "remote_name": "origin",
        "repo_url": "https://github.com/u/r.git",
    }


def test_translate_gh_repo_create():
    assert _translate()("gh repo create my-repo --private") == {
        "action": "repo_create",
        "repo_name": "my-repo",
        "private": True,
        "description": None,
    }
    assert _translate()('gh repo create my-repo --description "My project"') == {
        "action": "repo_create",
        "repo_name": "my-repo",
        "private": False,
        "description": "My project",
    }


def test_translate_non_git_passthrough():
    # Commands that are neither git nor gh must NOT be intercepted.
    assert _translate()("ruff check .") is None
    assert _translate()("npm test") is None
    assert _translate()("ls -la") is None
    assert _translate()("echo hello") is None


def test_translate_unknown_git_subcommand_passthrough():
    # An unsupported git subcommand is left to the shell rather than mis-routed.
    assert _translate()("git mv a b") is None
