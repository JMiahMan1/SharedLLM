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
