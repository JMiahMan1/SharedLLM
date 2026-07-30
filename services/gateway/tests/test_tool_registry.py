"""Unit tests for the SharedLLM OpenAI/Ollama tool registry."""
from services.gateway.tool_registry import (
    TOOL_GH,
    TOOL_GIT,
    TOOL_IMAGE_EDIT,
    TOOL_IMAGE_GENERATE,
    TOOL_LIST_IMAGE_MODELS,
    TOOL_RAVEN_MISSION,
    TOOL_WEBSCRAPER,
    TOOL_WRITE_FILE,
    ResolvedToolCall,
    get_tool_schemas,
    resolve_tool_call,
)


def test_get_tool_schemas_returns_all_seven_tools():
    schemas = get_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        TOOL_GH,
        TOOL_GIT,
        TOOL_WRITE_FILE,
        TOOL_IMAGE_GENERATE,
        TOOL_IMAGE_EDIT,
        TOOL_LIST_IMAGE_MODELS,
        TOOL_RAVEN_MISSION,
        TOOL_WEBSCRAPER,
        "workspaceportexposerequest",
    }
    # Every tool must declare JSON-schema parameters.
    for s in schemas:
        assert s["type"] == "function"
        assert "parameters" in s["function"]


def test_resolve_gh_builds_execution_request():
    r = resolve_tool_call(
        TOOL_GH,
        {"args": ["repo", "create", "x", "--private"], "workspace_id": "ws1"},
        user_context={"user": "default", "is_admin": True},
    )
    assert isinstance(r, ResolvedToolCall)
    assert r.method == "POST"
    assert r.service == "execution"
    assert r.path == "/execute/gh"
    assert r.requires_workspace is True
    assert r.json["args"] == ["repo", "create", "x", "--private"]
    assert r.json["workspace_id"] == "ws1"
    assert r.json["user_context"]["user"] == "default"


def test_resolve_git_uses_defaults():
    r = resolve_tool_call(TOOL_GIT, {"action": "commit", "commit_message": "fix"}, workspace_id="ws2")
    assert r.service == "execution"
    assert r.path == "/execute/git"
    assert r.json["action"] == "commit"
    assert r.json["commit_message"] == "fix"
    assert r.json["branch"] == "microservices"


def test_resolve_write_file_targets_workspace_runtime():
    r = resolve_tool_call(
        TOOL_WRITE_FILE,
        {"workspace_id": "ws3", "relative_path": "a.py", "content": "print(1)"},
    )
    assert r.service == "workspace_runtime"
    assert r.path == "/files/write"
    assert r.json["relative_path"] == "a.py"
    assert r.json["content"] == "print(1)"


def test_resolve_image_generate_targets_alpaca_sd():
    r = resolve_tool_call(TOOL_IMAGE_GENERATE, {"prompt": "a cat", "size": "768x768", "n": 2})
    assert r.service == "alpaca_sd"
    assert r.path == "/v1/images/generations"
    assert r.json["prompt"] == "a cat"
    assert r.json["n"] == 2
    assert r.requires_workspace is False


def test_resolve_list_image_models_is_get():
    r = resolve_tool_call(TOOL_LIST_IMAGE_MODELS, {})
    assert r.method == "GET"
    assert r.path == "/v1/images/models"


def test_resolve_unknown_tool_raises():
    import pytest
    with pytest.raises(ValueError):
        resolve_tool_call("not_a_tool", {})


def test_workspace_id_override_takes_precedence():
    r = resolve_tool_call(TOOL_GH, {"args": ["status"], "workspace_id": "from_args"}, workspace_id="override")
    assert r.json["workspace_id"] == "override"


def test_resolve_raven_mission_targets_gateway():
    r = resolve_tool_call(
        TOOL_RAVEN_MISSION,
        {"mission": "build a 3D game", "workspace_id": "ws9"},
    )
    assert isinstance(r, ResolvedToolCall)
    assert r.method == "POST"
    assert r.service == "gateway"
    assert r.path == "/api/raven/missions"
    assert r.json["query"] == "build a 3D game"
    assert r.json["workspace_id"] == "ws9"
    assert r.requires_workspace is False


def test_resolve_web_scraper_targets_execution():
    r = resolve_tool_call(
        TOOL_WEBSCRAPER,
        {"query": "RTX 5090", "urls": ["ebay", "amazon"], "browser_engine": "camoufox"},
        user_context={"user": "default", "is_admin": True},
    )
    assert isinstance(r, ResolvedToolCall)
    assert r.method == "POST"
    assert r.service == "execution"
    assert r.path == "/execute/web_scraper"
    assert r.json["query"] == "RTX 5090"
    assert r.json["urls"] == ["ebay", "amazon"]
    assert r.json["browser_engine"] == "camoufox"
    assert r.json["headless"] is True
    assert r.json["mobile"] is False
    assert r.requires_workspace is False


def test_resolve_web_scraper_uses_defaults():
    r = resolve_tool_call(TOOL_WEBSCRAPER, {"query": "gaming laptop"})
    assert r.json["query"] == "gaming laptop"
    assert r.json["urls"] == ["ebay", "amazon", "newegg"]
    assert r.json["browser_engine"] is None
    assert r.json["headless"] is True
    assert r.json["mobile"] is False
