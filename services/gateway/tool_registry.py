"""
OpenAI/Ollama-compatible tool registry for SharedLLM's agent tool surface.

This module exposes SharedLLM capabilities — `gh`, `git`, workspace file writes,
and the alpaca Stable Diffusion image tools (generations / edits / model listing)
— as OpenAI ``tools`` schemas so external clients (OpenAI SDK, Ollama tool-calling,
OpenWebUI) can drive them.

It also provides :func:`resolve_tool_call`, which maps a model-emitted tool call to
the concrete execution-service request, and :func:`get_tool_schemas` for discovery
(via the gateway's ``GET /v1/tools`` endpoint).

Services targeted:
    * execution        -> SharedLLM execution service (gh, git, ...)
    * workspace_runtime -> workspace file APIs
    * alpaca_sd        -> alpaca Stable Diffusion backend (port 8081)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Tool name constants (OpenAI tool names must be letters/numbers/underscores).
TOOL_GH = "sharedllm_gh"
TOOL_GIT = "sharedllm_git"
TOOL_WRITE_FILE = "sharedllm_write_file"
TOOL_IMAGE_GENERATE = "sharedllm_image_generate"
TOOL_IMAGE_EDIT = "sharedllm_image_edit"
TOOL_LIST_IMAGE_MODELS = "sharedllm_list_image_models"
TOOL_RAVEN_MISSION = "sharedllm_raven_mission"

# Service identifiers used by the resolver / proxy layer.
SVC_EXECUTION = "execution"
SVC_WORKSPACE = "workspace_runtime"
SVC_ALPACA_SD = "alpaca_sd"
SVC_GATEWAY = "gateway"


_GH_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_GH,
        "description": (
            "Run a GitHub CLI (gh) command inside a workspace. Use for creating "
            "repositories, opening PRs/issues, and other GitHub operations that Raven "
            "is allowed to perform. Args are the gh subcommand and flags WITHOUT the "
            "leading 'gh' (e.g. ['repo','create','my-repo','--private'])."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "gh subcommand + arguments (without the leading 'gh').",
                },
                "workspace_id": {"type": "string", "description": "Target workspace id."},
                "cwd": {"type": "string", "description": "Working dir relative to workspace root.", "default": "."},
                "timeout": {"type": "integer", "description": "Command timeout in seconds.", "default": 120},
            },
            "required": ["args"],
        },
    },
}

_GIT_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_GIT,
        "description": (
            "Perform a Git operation on a workspace (status, add, commit, pull, push, "
            "log, branch, checkout, reset, clean). push requires admin context."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "add", "commit", "pull", "push", "log", "fetch", "reset", "branch", "checkout", "clean", "show"],
                },
                "workspace_id": {"type": "string", "description": "Target workspace id."},
                "path": {"type": "string", "description": "File path for 'add'.", "default": "."},
                "commit_message": {"type": "string", "description": "Required for 'commit'."},
                "branch": {"type": "string", "description": "Branch for pull/push.", "default": "microservices"},
            },
            "required": ["action"],
        },
    },
}

_WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_WRITE_FILE,
        "description": "Write or patch a file inside a workspace. Use to create or edit source files.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Target workspace id."},
                "relative_path": {"type": "string", "description": "File path relative to the workspace root."},
                "content": {"type": "string", "description": "Full new file content."},
            },
            "required": ["workspace_id", "relative_path", "content"],
        },
    },
}

_IMAGE_GENERATE_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_IMAGE_GENERATE,
        "description": "Generate an image with the alpaca Stable Diffusion backend from a text prompt.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Text prompt describing the image to generate."},
                "model": {"type": "string", "description": "SD model name (optional; loads default if omitted)."},
                "size": {"type": "string", "description": "Image size, e.g. '512x512'.", "default": "512x512"},
                "n": {"type": "integer", "description": "Number of images to generate.", "default": 1},
            },
            "required": ["prompt"],
        },
    },
}

_IMAGE_EDIT_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_IMAGE_EDIT,
        "description": "Edit an existing image with the alpaca Stable Diffusion backend using a text prompt.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Editing instruction."},
                "image": {"type": "string", "description": "Base64 or URL of the image to edit."},
                "model": {"type": "string", "description": "SD model name (optional)."},
            },
            "required": ["prompt", "image"],
        },
    },
}

_LIST_IMAGE_MODELS_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_LIST_IMAGE_MODELS,
        "description": "List the Stable Diffusion models available on the alpaca image backend.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_RAVEN_MISSION_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_RAVEN_MISSION,
        "description": (
            "Dispatch a background Raven mission — an autonomous agent that plans, "
            "writes code, creates its own workspace, builds, tests, and can push to "
            "GitHub. Use this for complex multi-step engineering tasks such as "
            "'build a 3D game', 'create a service and deploy it', or 'implement an app'. "
            "The mission runs asynchronously in the Raven queue and returns a mission id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mission": {
                    "type": "string",
                    "description": "The full task/mission description for Raven to execute.",
                },
                "workspace_id": {
                    "type": "string",
                    "description": "Optional existing workspace id to run the mission in.",
                },
            },
            "required": ["mission"],
        },
    },
}


def get_tool_schemas() -> list[dict]:
    """Return the OpenAI ``tools`` schemas for all SharedLLM tools."""
    return [
        _GH_TOOL,
        _GIT_TOOL,
        _WRITE_FILE_TOOL,
        _IMAGE_GENERATE_TOOL,
        _IMAGE_EDIT_TOOL,
        _LIST_IMAGE_MODELS_TOOL,
        _RAVEN_MISSION_TOOL,
    ]


@dataclass
class ResolvedToolCall:
    """A tool call resolved to a concrete service request."""

    method: str
    service: str
    path: str
    json: dict
    requires_workspace: bool = False


def resolve_tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    workspace_id: str | None = None,
    user_context: dict[str, Any] | None = None,
) -> ResolvedToolCall:
    """Map a model-emitted tool call to the concrete execution-service request.

    ``workspace_id``/``user_context`` supplied by the proxy override (or fill in)
    values from the model's ``arguments`` so external clients don't need to know
    the internal credential plumbing.
    """
    uc: dict[str, Any] = dict(user_context or {"user": "default", "is_admin": True})
    ws = workspace_id or arguments.get("workspace_id")

    if name == TOOL_GH:
        return ResolvedToolCall(
            method="POST",
            service=SVC_EXECUTION,
            path="/execute/gh",
            json={
                "user_context": uc,
                "workspace_id": ws,
                "args": list(arguments.get("args", [])),
                "cwd": arguments.get("cwd", "."),
                "timeout": int(arguments.get("timeout", 120)),
            },
            requires_workspace=True,
        )

    if name == TOOL_GIT:
        return ResolvedToolCall(
            method="POST",
            service=SVC_EXECUTION,
            path="/execute/git",
            json={
                "user_context": uc,
                "workspace_id": ws,
                "action": arguments.get("action", "status"),
                "path": arguments.get("path", "."),
                "commit_message": arguments.get("commit_message"),
                "branch": arguments.get("branch", "microservices"),
            },
            requires_workspace=True,
        )

    if name == TOOL_WRITE_FILE:
        return ResolvedToolCall(
            method="POST",
            service=SVC_WORKSPACE,
            path="/files/write",
            json={
                "workspace_id": ws,
                "relative_path": arguments.get("relative_path"),
                "content": arguments.get("content"),
                "user_context": uc,
            },
            requires_workspace=True,
        )

    if name == TOOL_IMAGE_GENERATE:
        return ResolvedToolCall(
            method="POST",
            service=SVC_ALPACA_SD,
            path="/v1/images/generations",
            json={
                "prompt": arguments.get("prompt"),
                "model": arguments.get("model"),
                "size": arguments.get("size", "512x512"),
                "n": int(arguments.get("n", 1)),
            },
        )

    if name == TOOL_IMAGE_EDIT:
        return ResolvedToolCall(
            method="POST",
            service=SVC_ALPACA_SD,
            path="/v1/images/edits",
            json={
                "prompt": arguments.get("prompt"),
                "image": arguments.get("image"),
                "model": arguments.get("model"),
            },
        )

    if name == TOOL_LIST_IMAGE_MODELS:
        return ResolvedToolCall(
            method="GET",
            service=SVC_ALPACA_SD,
            path="/v1/images/models",
            json={},
        )

    if name == TOOL_RAVEN_MISSION:
        return ResolvedToolCall(
            method="POST",
            service=SVC_GATEWAY,
            path="/api/raven/missions",
            json={
                "query": arguments.get("mission", ""),
                "workspace_id": arguments.get("workspace_id"),
            },
        )

    raise ValueError(f"Unknown SharedLLM tool: {name}")
