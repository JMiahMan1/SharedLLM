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

import re
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
TOOL_WEBSCRAPER = "sharedllm_web_scraper"
TOOL_OCR = "sharedllm_ocr"
TOOL_WORKSPACE_EXPOSE_PORT = "workspaceportexposerequest"

# Service identifiers used by the resolver / proxy layer.
SVC_EXECUTION = "execution"
SVC_WORKSPACE = "workspace_runtime"
SVC_ALPACA_SD = "alpaca_sd"
SVC_GATEWAY = "gateway"
SVC_RAG = "rag"
SVC_STORAGE = "storage"
SVC_CONTROL_PLANE = "control_plane"


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


_WEBSCRAPER_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_WEBSCRAPER,
        "description": (
            "Scrape product prices and details from e-commerce sites. "
            "Supports eBay, Amazon, Newegg, AliExpress, Google Shopping, or custom URLs. "
            "Uses Playwright or Camoufox browser for anti-bot evasion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query to look for products.",
                },
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URL sources to scrape. Valid named sources: ebay, amazon, newegg, aliexpress, google_shopping. Or full custom URLs.",
                    "default": ["ebay", "amazon", "newegg"],
                },
                "browser_engine": {
                    "type": "string",
                    "enum": ["playwright", "camoufox"],
                    "description": "Browser engine: playwright or camoufox (default: camoufox).",
                    "default": "camoufox",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Run browser headless.",
                    "default": True,
                },
                "mobile": {
                    "type": "boolean",
                    "description": "Use mobile viewport (bypasses some captchas).",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    },
}


_WORKSPACE_EXPOSE_PORT_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_WORKSPACE_EXPOSE_PORT,
        "description": "Expose a port running inside the workspace sandbox container so it is accessible on the host IP.",
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {
                    "type": "string",
                    "description": "Target workspace ID.",
                },
                "container_port": {
                    "type": "integer",
                    "description": "The port number running inside the container to expose.",
                },
                "host_port": {
                    "type": "integer",
                    "description": "Optional host port to bind to. If omitted or 0, an available host port is auto-assigned.",
                },
            },
            "required": ["container_port"],
        },
    },
}

_OCR_TOOL = {
    "type": "function",
    "function": {
        "name": TOOL_OCR,
        "description": (
            "Extract all visible text from an image inside a workspace using the "
            "vision OCR model (qwen2.5-vl). Returns structured text fields "
            "(full_text, headline, subtext, badge)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string", "description": "Target workspace id."},
                "image_path": {"type": "string", "description": "Path to the image inside the workspace (e.g. 'sign_original.jpg')."},
                "task": {"type": "string", "description": "OCR task type: 'general', 'document', or 'price_scrape'.", "default": "general"},
            },
            "required": ["workspace_id", "image_path"],
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
        _WEBSCRAPER_TOOL,
        _OCR_TOOL,
        _WORKSPACE_EXPOSE_PORT_TOOL,
        *get_raven_tool_schemas(),
    ]


# ---------------------------------------------------------------------------
# Full Raven action surface.
#
# Every entry mirrors one Raven AgentLoop action (same tool name Raven itself
# emits) so external OpenAI/Ollama chat clients — and the training transcripts
# they produce — map 1:1 onto Raven's native tool calls.
# Entry: (tool_name, service, method, path, requires_workspace, description,
#         payload_hint)
# ---------------------------------------------------------------------------

_RAVEN_TOOL_TABLE: tuple[tuple, ...] = (
    ("LightControlRequest", SVC_EXECUTION, "POST", "/execute/light", False,
     "Control Home Assistant lights (on/off/brightness/color).",
     "payload fields: action, entity_id, brightness, color, transition."),
    ("MediaPlayRequest", SVC_EXECUTION, "POST", "/execute/media/play", False,
     "Play media via Music Assistant / HA (playlists, favorites, stream).",
     "payload fields: action, entity_id/player, media_id, playlist, query."),
    ("MediaTransportRequest", SVC_EXECUTION, "POST", "/execute/media/transport", False,
     "Media transport controls: play, pause, stop, next, previous, volume.",
     "payload fields: action, entity_id/player, volume_level."),
    ("MediaStatusRequest", SVC_EXECUTION, "POST", "/execute/media/status", False,
     "Query current media player status/state.",
     "payload fields: entity_id/player."),
    ("VideoPlayRequest", SVC_EXECUTION, "POST", "/execute/video/play", False,
     "Play a video file or stream on a target device.",
     "payload fields: action, path/url, entity_id/player."),
    ("TVCastRequest", SVC_EXECUTION, "POST", "/execute/tv_cast", False,
     "Cast media or a URL to a TV/Chromecast device.",
     "payload fields: action, url, entity_id/device."),
    ("ClimateRequest", SVC_EXECUTION, "POST", "/execute/climate", False,
     "Control thermostats and climate entities (temperature, HVAC mode).",
     "payload fields: action, entity_id, temperature, hvac_mode."),
    ("SecurityRequest", SVC_EXECUTION, "POST", "/execute/security", False,
     "Arm/disarm alarm panels and query security state.",
     "payload fields: action, entity_id, code."),
    ("AnnouncementRequest", SVC_EXECUTION, "POST", "/execute/announce", False,
     "Broadcast a text announcement to speakers/displays.",
     "payload fields: message, entity_id/target, volume."),
    ("HAServiceRequest", SVC_EXECUTION, "POST", "/execute/ha_service", False,
     "Call any Home Assistant service directly (domain.service + data).",
     "payload fields: domain, service, entity_id, service_data."),
    ("CalendarRequest", SVC_EXECUTION, "POST", "/execute/calendar", False,
     "Calendar CRUD: list, create, update, delete events.",
     "payload fields: action, title, start, end, event_id, calendar."),
    ("NoteRequest", SVC_EXECUTION, "POST", "/execute/note", False,
     "Notes: create, read, append, delete, list.",
     "payload fields: action, title, content, note_id."),
    ("TimerRequest", SVC_EXECUTION, "POST", "/execute/timer", False,
     "Timers: create, list, cancel.",
     "payload fields: action, duration, label, timer_id."),
    ("TalkRequest", SVC_EXECUTION, "POST", "/execute/talk", False,
     "Conversations/voice: list conversations, read messages, send.",
     "payload fields: action, conversation_id, message."),
    ("WebSearchRequest", SVC_EXECUTION, "POST", "/execute/web_search", False,
     "Web search for current/external information.",
     "payload fields: query, max_results."),
    ("WebReadRequest", SVC_EXECUTION, "POST", "/execute/web_read", False,
     "Fetch and extract readable text from a URL.",
     "payload fields: url."),
    ("CodeSearchRequest", SVC_EXECUTION, "POST", "/execute/code_search", False,
     "Semantic search over indexed workspace code.",
     "payload fields: query, workspace_id, max_results."),
    ("DockerLogsRequest", SVC_EXECUTION, "POST", "/execute/docker_logs", False,
     "Fetch logs from a Docker container.",
     "payload fields: container/service, tail/lines."),
    ("DockerComposeRequest", SVC_EXECUTION, "POST", "/execute/docker", False,
     "Docker/compose operations on SharedLLM services.",
     "payload fields: action, service."),
    ("DeploymentRequest", SVC_EXECUTION, "POST", "/execute/deploy", False,
     "Deploy/restart SharedLLM services.",
     "payload fields: action, service."),
    ("CapabilityIndexRequest", SVC_EXECUTION, "POST", "/execute/index_capabilities", False,
     "Re-index device/capability catalog after environment changes.",
     "payload fields: (none required)."),
    ("VolumeInventoryRequest", SVC_EXECUTION, "POST", "/execute/volumes", False,
     "Inventory Docker volumes and disk usage.",
     "payload fields: (none required)."),
    ("WorkspaceFileReadRequest", SVC_EXECUTION, "POST", "/execute/workspace_file_read", True,
     "Read a file (text, PDF text, binary info) from a workspace.",
     "payload fields: file_path/relative_path."),
    ("WorkspaceFileWriteRequest", SVC_EXECUTION, "POST", "/execute/workspace_file_write", True,
     "Write a full file into a workspace.",
     "payload fields: relative_path/file_path, content."),
    ("WorkspaceFilePatchRequest", SVC_EXECUTION, "POST", "/execute/workspace_file_patch", True,
     "Surgically patch a workspace file with old_text/new_text chunks.",
     "payload fields: relative_path, chunks:[{old_text,new_text}]."),
    ("WorkspaceLintRequest", SVC_EXECUTION, "POST", "/execute/workspace_lint", True,
     "Lint/typecheck a workspace file or tree.",
     "payload fields: relative_path, language."),
    ("WorkspaceSearchRequest", SVC_EXECUTION, "POST", "/execute/workspace_search", True,
     "Grep/regex search across workspace files.",
     "payload fields: pattern, path, include."),
    ("WorkspaceShellRequest", SVC_EXECUTION, "POST", "/execute/workspace_shell", True,
     "Run a shell command (or command list) in the workspace sandbox.",
     "payload fields: command or commands, cwd, timeout."),
    ("WorkspaceCreateRequest", SVC_WORKSPACE, "POST", "/workspaces", False,
     "Create a new workspace (user or system scope).",
     "payload fields: id, local_path, scope, display_name."),
    ("WorkspaceSettingsUpdateRequest", SVC_WORKSPACE, "PATCH", "/workspaces/{workspace_id}", True,
     "Update workspace settings (repo_url, branch, display_name...).",
     "payload fields: any settings keys; workspace_id fills the path."),
    ("WorkspaceBootstrapRequest", SVC_WORKSPACE, "POST", "/workspaces/bootstrap", True,
     "Clone/pull a workspace's git repo and prime its directory.",
     "payload fields: workspace_id, repo_url, branch."),
    ("StorageFileReadRequest", SVC_EXECUTION, "POST", "/execute/storage_file_read", False,
     "Read a file from SharedLLM storage volumes.",
     "payload fields: path/volume, file_path."),
    ("StorageFileWriteRequest", SVC_EXECUTION, "POST", "/execute/storage_file_write", False,
     "Write a file into SharedLLM storage volumes.",
     "payload fields: path/volume, file_path, content."),
    ("StorageListRequest", SVC_EXECUTION, "POST", "/execute/storage_list", False,
     "List resources in SharedLLM storage.",
     "payload fields: path/volume, pattern."),
    ("SystemLearningRequest", SVC_EXECUTION, "POST", "/execute/learning", False,
     "Teach Raven: ingest/list/validate lessons learned.",
     "payload fields: action (ingest/list/validate/get/status), lesson, lesson_id."),
    ("RedisInspectRequest", SVC_EXECUTION, "POST", "/execute/redis", False,
     "Read-only Redis inspection: ping, get, keys, ttl.",
     "payload fields: operation (ping/get/keys/ttl), key, pattern."),
    ("DiscoverySyncRequest", SVC_EXECUTION, "POST", "/execute/discovery_sync", False,
     "Sync Home Assistant entities/devices into SharedLLM discovery.",
     "payload fields: (none required)."),
    ("IdentityRequest", SVC_EXECUTION, "POST", "/execute/identity", False,
     "Identity lookup: resolve users and credentials context.",
     "payload fields: action, user."),
    ("IdentityManageRequest", SVC_EXECUTION, "POST", "/execute/identity/manage", False,
     "Admin identity management (users, passwords, API keys).",
     "payload fields: action, user, password."),
    ("AudiobookshelfRequest", SVC_EXECUTION, "POST", "/execute/audiobookshelf", False,
     "Audiobookshelf: libraries, search, status.",
     "payload fields: action, query, library."),
    ("LLMInfoRequest", SVC_EXECUTION, "POST", "/execute/llm/info", False,
     "LLM backend info: loaded models, VRAM, Ollama state.",
     "payload fields: (none required)."),
    ("ContextSearchRequest", SVC_RAG, "POST", "/rag/search", False,
     "Semantic RAG search over indexed knowledge.",
     "payload fields: query, collection, top_k."),
    ("HAConfigRequest", SVC_EXECUTION, "POST", "/execute/ha_config", False,
     "Read Home Assistant configuration snapshot.",
     "payload fields: (none required)."),
    ("EntitySearchRequest", SVC_EXECUTION, "POST", "/execute/entity_search", False,
     "Search known HA entities by name/domain.",
     "payload fields: query, domain."),
    ("LogbookRequest", SVC_EXECUTION, "POST", "/execute/ha_logbook", False,
     "Query the Home Assistant logbook.",
     "payload fields: entity_id, start, end."),
    ("ExecutionLogRequest", SVC_EXECUTION, "POST", "/execute/logs", False,
     "Fetch SharedLLM service/container logs.",
     "payload fields: service/container, tail/lines."),
    ("DocumentBroadcastRequest", SVC_EXECUTION, "POST", "/execute/composite/broadcast", False,
     "Composite broadcast: announcement + displays + lights scene.",
     "payload fields: message, targets."),
    ("NightModeRequest", SVC_EXECUTION, "POST", "/execute/composite/night_mode", False,
     "Run the night-mode composite scene.",
     "payload fields: (none required, or options)."),
    ("TTSRequest", SVC_EXECUTION, "POST", "/execute/tts", False,
     "Text-to-speech synthesis to an audio file.",
     "payload fields: text, voice, engine, output_path."),
    ("STTRequest", SVC_EXECUTION, "POST", "/execute/stt/transcribe_workspace", True,
     "Speech-to-text: transcribe an audio file in a workspace.",
     "payload fields: audio_path/file_path, language."),
    ("AudiobookRegenerateRequest", SVC_EXECUTION, "POST", "/execute/audiobook/regenerate", False,
     "Regenerate an audiobook chapter or full book via TTS pipeline.",
     "payload fields: workspace_id, chapter, voice."),
    ("StorageIndexRequest", SVC_STORAGE, "POST", "/index/full", False,
     "Full storage re-index into RAG.",
     "payload fields: path/volume."),
    ("StorageTextToAudioRequest", SVC_STORAGE, "POST", "/text_to_audio", False,
     "Storage TTS: synthesize stored text to audio.",
     "payload fields: text_path, voice."),
    ("NetworkDeviceScanRequest", SVC_EXECUTION, "POST", "/execute/network_scan", False,
     "Scan the LAN for devices.",
     "payload fields: subnet."),
    ("ImageEditRequest", SVC_EXECUTION, "POST", "/execute/image_edit", False,
     "Edit an image via the execution image pipeline.",
     "payload fields: prompt, image, model."),
    ("ControlPlaneRequest", SVC_CONTROL_PLANE, "POST", "/api/restart/{service_name}", False,
     "Restart a SharedLLM service via the control plane.",
     "payload fields: service_name (fills the path)."),
)


def get_raven_tool_schemas() -> list[dict]:
    """Build OpenAI ``tools`` schemas for the full Raven action surface."""
    schemas: list[dict] = []
    for name, _svc, _method, _path, requires_ws, desc, hint in _RAVEN_TOOL_TABLE:
        props: dict = {
            "payload": {
                "type": "object",
                "description": f"Raven-style fields for {name}. {hint}",
            }
        }
        required: list[str] = ["payload"]
        if requires_ws:
            props["workspace_id"] = {
                "type": "string",
                "description": "Target workspace id.",
            }
            required.append("workspace_id")
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": f"[Raven] {desc} Pass arguments as `payload` using Raven's native field names. {hint}",
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return schemas


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

    if name == TOOL_WEBSCRAPER:
        return ResolvedToolCall(
            method="POST",
            service=SVC_EXECUTION,
            path="/execute/web_scraper",
            json={
                "user_context": uc,
                "query": arguments.get("query", ""),
                "urls": list(arguments.get("urls", ["ebay", "amazon", "newegg"])),
                "browser_engine": arguments.get("browser_engine"),
                "headless": bool(arguments.get("headless", True)),
                "mobile": bool(arguments.get("mobile", False)),
            },
        )

    if name == TOOL_OCR:
        return ResolvedToolCall(
            method="POST",
            service=SVC_EXECUTION,
            path="/execute/ocr",
            json={
                "user_context": uc,
                "workspace_id": ws,
                "image_path": arguments.get("image_path"),
                "task": arguments.get("task", "general"),
            },
            requires_workspace=True,
        )

    if name in (TOOL_WORKSPACE_EXPOSE_PORT, "workspace_expose_port", "expose_port", "port_expose"):
        return ResolvedToolCall(
            method="POST",
            service=SVC_WORKSPACE,
            path="/ports/expose",
            json={
                "workspace_id": ws,
                "container_port": int(arguments.get("container_port") or arguments.get("port") or 8000),
                "host_port": int(arguments.get("host_port")) if arguments.get("host_port") else None,
            },
            requires_workspace=True,
        )

    # Generic Raven-action branch: every entry in _RAVEN_TOOL_TABLE resolves
    # here. Raven-style `payload` fields pass straight through (execution
    # schemas ignore unknown extras), with user_context/workspace_id injected.
    norm_target = re.sub(r'[\s_]+', '', name).lower()
    norm_target_noreq = norm_target[:-7] if norm_target.endswith("request") else norm_target
    raven_entry = next(
        (
            e for e in _RAVEN_TOOL_TABLE
            if e[0] == name
            or re.sub(r'[\s_]+', '', e[0]).lower() == norm_target
            or (re.sub(r'[\s_]+', '', e[0]).lower()[:-7] if re.sub(r'[\s_]+', '', e[0]).lower().endswith("request") else re.sub(r'[\s_]+', '', e[0]).lower()) == norm_target_noreq
        ),
        None,
    )
    if raven_entry is not None:
        _, service, method, path, requires_ws, _, _ = raven_entry
        payload = arguments.get("payload")
        if not isinstance(payload, dict):
            # Tolerate models that inline Raven fields at the top level.
            payload = {k: v for k, v in arguments.items() if k != "workspace_id"}
        body: dict[str, Any] = {"user_context": uc}
        if ws:
            body["workspace_id"] = ws
        body.update(payload)
        # Substitute {placeholders} in the path from payload/arguments.
        for key in ("workspace_id", "service_name"):
            if f"{{{key}}}" in path:
                val = ws if key == "workspace_id" else payload.get(key) or arguments.get(key)
                if val is None:
                    raise ValueError(f"Tool {name} requires '{key}' for path {path}")
                path = path.replace(f"{{{key}}}", str(val))
        if requires_ws and not ws:
            raise ValueError(f"Tool {name} requires workspace_id")
        return ResolvedToolCall(
            method=method,
            service=service,
            path=path,
            json=body,
            requires_workspace=requires_ws,
        )

    raise ValueError(f"Unknown SharedLLM tool: {name}")
