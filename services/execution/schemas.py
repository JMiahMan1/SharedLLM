# services/execution/schemas.py
"""
Pydantic schemas for all Execution Bridge endpoints.
Strict validation is the primary defense against malformed gateway payloads.
"""
from typing import Optional, Literal, Any, Dict, List
from pydantic import BaseModel, Field, model_validator


class BaseRequest(BaseModel):
    model_config = {"extra": "ignore"}

    @model_validator(mode='before')
    @classmethod
    def handle_common_aliases(cls, data: Any) -> Any:
        """Automatically map common hallucinations to schema-correct fields."""
        if isinstance(data, dict):
            # Map 'command' or 'operation' to 'action' (Common Git hallucination)
            if "action" not in data:
                if "command" in data:
                    data["action"] = data["command"]
                elif "operation" in data:
                    data["action"] = data["operation"]
            
            # Map 'git_status' to 'status', etc.
            if "action" in data and isinstance(data["action"], str) and data["action"].startswith("git_"):
                data["action"] = data["action"].replace("git_", "")

            # Map 'file_path', 'repository_path', or 'file' to 'path'
            if "path" not in data:
                if "file_path" in data:
                    data["path"] = data["file_path"]
                elif "repository_path" in data:
                    data["path"] = data["repository_path"]
                elif "file" in data:
                    data["path"] = data["file"]
            
            # Map 'message' to 'commit_message' or vice-versa
            if "message" in data and "commit_message" not in data:
                data["commit_message"] = data["message"]
            if "commit_message" in data and "message" not in data:
                data["message"] = data["commit_message"]

            # Map 'limit' to 'limit_lines' and 'offset' to 'offset_lines'
            if "limit" in data and "limit_lines" not in data:
                data["limit_lines"] = data["limit"]
            if "offset" in data and "offset_lines" not in data:
                data["offset_lines"] = data["offset"]
                
        return data

class UserContext(BaseModel):
    """Resolved user credentials forwarded by the Gateway."""
    user: str
    is_admin: bool = False
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    nextcloud_pass: Optional[str] = None
    github_token: Optional[str] = None
    gitlab_token: Optional[str] = None
    git_token: Optional[str] = None
    api_key: Optional[str] = None


class ExecutionResult(BaseModel):
    status: Literal["SUCCESS", "FAILURE", "PARTIAL"]
    message: str
    service: str
    detail: Optional[Dict[str, Any]] = None


# ─── Media / Music ──────────────────────────────────────────────────────────────

class MediaPlayRequest(BaseRequest):
    user_context: UserContext
    entity_id: str = Field(..., description="HA media_player entity ID")
    media_content_id: Optional[str] = None
    media_content_type: Optional[str] = "music"
    # Music Assistant fields
    query: Optional[str] = None
    enqueue: Optional[Literal["add", "next", "replace"]] = "replace"


class MediaTransportRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    command: Literal["pause", "resume", "stop", "next", "previous", "volume_up", "volume_down"]
    volume_level: Optional[float] = Field(None, ge=0.0, le=1.0)


# ─── Lights ─────────────────────────────────────────────────────────────────────

class LightControlRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    action: Literal["turn_on", "turn_off", "toggle"]
    brightness_pct: Optional[int] = Field(None, ge=0, le=100)
    color_temp: Optional[int] = None
    rgb_color: Optional[tuple[int, int, int]] = None


# ─── Generic HA Service Call ────────────────────────────────────────────────────

class HAServiceRequest(BaseRequest):
    user_context: UserContext
    domain: str          # e.g. "light", "switch", "media_player"
    service: str         # e.g. "turn_on", "play_media"
    entity_id: str
    service_data: Optional[Dict[str, Any]] = None

class ClimateRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    temperature: float

class SecurityRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    action: Literal["lock", "unlock", "open", "close", "status"]


# ─── Announcements ──────────────────────────────────────────────────────────────

class AnnouncementRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    message: str
    volume: Optional[float] = Field(0.6, ge=0.0, le=1.0)


# ─── TV / SmartPowerSync ────────────────────────────────────────────────────────

class TVCastRequest(BaseRequest):
    """
    Encapsulates the 'SmartPowerSync' pattern:
    power on the TV, wait for readiness, then cast.
    """
    user_context: UserContext
    media_player_entity_id: str
    media_content_id: str
    media_content_type: str = "url"
    power_on_wait_ms: int = Field(3000, ge=0, le=15000)


# ─── Personal Data (Calendar / Notes) ──────────────────────────────────────────

class CalendarRequest(BaseRequest):
    user_context: UserContext
    action: Literal["list", "read", "add", "delete", "update"]
    query: Optional[str] = None
    summary: Optional[str] = None
    start_time: Optional[str] = None
    calendar_name: Optional[str] = None


class NoteRequest(BaseRequest):
    user_context: UserContext
    action: Literal["create", "append", "read", "delete", "check_off"]
    title: str
    content: Optional[str] = None
    category: Optional[str] = "General"
    item: Optional[str] = None # For check_off


# ─── Timers / Alarms ────────────────────────────────────────────────────────────

class TimerRequest(BaseRequest):
    user_context: UserContext
    action: Literal["add", "list", "delete", "pause", "resume"]
    type: Literal["timer", "alarm"] = "timer"
    query: Optional[str] = None
    title: Optional[str] = None
    duration_str: Optional[str] = None
    time_str: Optional[str] = None
    recurrence: Optional[str] = None
    target_device: Optional[str] = None


class TalkRequest(BaseRequest):
    user_context: UserContext
    action: Literal["list", "open", "messages", "send", "send_voice"]
    token: Optional[str] = None
    target_user: Optional[str] = None
    message: Optional[str] = None
    limit: int = Field(50, ge=1, le=200)
    audio_base64: Optional[str] = None
    text_to_voice: Optional[str] = Field(None, description="If provided, converts this text to a voice message (TTS).")
    mime_type: Optional[str] = None
    file_name: Optional[str] = None
    caption: Optional[str] = None

# ─── File Operations (Workspace vs Storage) ───────────────────────────────────

class WorkspaceFileReadRequest(BaseRequest):
    """
    Reads a file from the local Git workspace (/workspace/SharedLLM).
    Use this for reading CODE, SCRIPTS, and CONFIG.
    """
    user_context: UserContext
    path: str = Field(..., description="Path relative to workspace root (e.g. 'services/gateway/main.py')")
    offset_lines: int = Field(0, ge=0, description="Start reading from this line number (1-indexed)")
    limit_lines: int = Field(1000, ge=1, le=5000, description="Max lines to read")
    summary_only: bool = Field(False, description="If true, returns only class/function signatures and docstrings (semantic map)")

class WorkspaceFileWriteRequest(BaseRequest):
    """
    Writes or overwrites a file in the local Git workspace.
    Requires the FULL file content in the 'content' field.
    """
    user_context: UserContext
    path: str = Field(..., description="Path relative to workspace root")
    content: str
    commit_after: bool = False
    commit_message: Optional[str] = None

class ReplacementChunk(BaseModel):
    old_text: str = Field(..., description="The exact text to be replaced")
    new_text: str = Field(..., description="The replacement text")

class WorkspaceFilePatchRequest(BaseRequest):
    """
    Surgically patches a file in the local Git workspace.
    Use this for small fixes to avoid providing the full file content.
    """
    user_context: UserContext
    path: str = Field(..., description="Path relative to workspace root")
    chunks: List[ReplacementChunk]
    commit_after: bool = False
    commit_message: Optional[str] = None

class WorkspaceShellRequest(BaseRequest):
    """
    Executes a shell command in the workspace root.
    Use this for advanced operations not covered by other tools.
    """
    user_context: UserContext
    command: Optional[str] = Field(None, description="The shell command to execute")
    commands: Optional[List[str]] = Field(None, description="A list of shell commands to execute (joined with &&)")
    cwd: Optional[str] = Field(".", description="Working directory relative to root")
    timeout: int = Field(60, ge=1, le=300, description="Command timeout in seconds")

class WorkspaceSearchRequest(BaseRequest):
    """
    Performs a codebase-wide search in the Git workspace using ripgrep or grep.
    Use this to find function definitions, variable usages, or specific patterns.
    """
    user_context: UserContext
    query: str = Field(..., description="The search pattern (regex supported)")
    path: str = Field(".", description="Search directory relative to root")
    include: Optional[str] = Field(None, description="Glob pattern to include (e.g. '*.py')")
    exclude: Optional[str] = Field(None, description="Glob pattern to exclude (e.g. '**/tests/**')")

class WorkspaceLintRequest(BaseRequest):
    """
    Lints a file in the local Git workspace.
    Automatically detects the linter based on file extension:
      .py  -> black (format check) + flake8
      .js/.ts/.jsx/.tsx -> eslint
      .json -> python -m json.tool
      .yaml/.yml -> yamllint
    Override with 'linter' to force a specific tool.
    """
    user_context: UserContext
    path: str = Field(..., description="Path relative to workspace root")
    linter: Optional[str] = Field(None, description="Force a specific linter (black, flake8, eslint, yamllint)")
    fix: bool = Field(False, description="If true, apply auto-fixes where possible (e.g. black --write)")

class StorageFileReadRequest(BaseRequest):
    """
    Reads a file from Nextcloud storage (Documents/Notes).
    Do NOT use this for code. Use WorkspaceFileReadRequest instead.
    """
    user_context: UserContext
    path: str = Field(..., description="Path within Nextcloud (e.g. '/Documents/memo.txt')")

class StorageFileWriteRequest(BaseRequest):
    """
    Writes a file to Nextcloud storage.
    """
    user_context: UserContext
    path: str = Field(..., description="Path within Nextcloud")
    content: str

class DiscoverySyncRequest(BaseRequest):
    """
    Triggers a synchronization of Home Assistant entities into the RAG database for discovery.
    """
    user_context: UserContext

# ─── Workspace / Code Orchestration ──────────────────────────────────────────

class WorkspaceFileAction(BaseRequest):
    """Orchestrates file writes and patches within a Git-backed workspace."""
    user_context: UserContext
    workspace_name: str
    path: str
    content: str
    is_patch: bool = False
    commit_after: bool = False
    commit_message: Optional[str] = None

class WorkspaceGitAction(BaseRequest):
    """Performs Git lifecycle operations (pull, commit, branch, status)."""
    user_context: UserContext
    workspace_name: str
    action: Literal["status", "pull", "commit", "branch", "push", "checkout"]
    branch_name: Optional[str] = None
    commit_message: Optional[str] = None

class WorkspaceSyncAction(BaseRequest):
    """Synchronizes workspace files with Nextcloud or other storage providers."""
    user_context: UserContext
    workspace_name: str
    path: Optional[str] = None  # None means sync full workspace
    direction: Literal["upload", "download"] = "upload"

# ─── Browser / Web Agent ────────────────────────────────────────────────────────

class WebSearchRequest(BaseRequest):
    """Performs a web search via search.sumemail.com."""
    user_context: UserContext
    query: str

class WebReadRequest(BaseRequest):
    """Fetches a URL and returns the content as markdown."""
    user_context: UserContext
    url: str
    use_current_user_auth: bool = False


# ─── Ouroboros Autonomous Loop ───────────────────────────────────────────────

class DockerLogsRequest(BaseRequest):
    """
    Fetches recent log output from one or more Docker containers.
    If 'services' is provided, it fetches logs for each (prepending 'sharedllm_' if needed).
    """
    user_context: UserContext
    container_name: Optional[str] = Field(None, description="Exact Docker container name")
    services: Optional[List[str]] = Field(None, description="List of services to fetch logs for (e.g. ['gateway', 'rag'])")
    tail_lines: int = Field(200, ge=1, le=2000, description="Number of log lines to retrieve")
    grep_filter: Optional[str] = Field(None, description="Filter to lines containing this keyword")


class GitOperationRequest(BaseRequest):
    """
    Performs a Git lifecycle operation on the SharedLLM workspace.
    push requires is_admin=True in user_context.
    """
    user_context: UserContext
    action: Literal["status", "diff", "add", "commit", "pull", "push", "log", "fetch", "reset", "branch", "checkout", "clean", "show"]
    path: Optional[str] = Field(".", description="File path for 'add' action")
    commit_message: Optional[str] = Field(None, description="Required for 'commit' action")
    branch: Optional[str] = Field("microservices", description="Branch for pull/push")
    log_count: Optional[int] = Field(10, ge=1, le=50, description="Number of commits for 'log'")


class DeploymentRequest(BaseRequest):
    """
    Controls a SharedLLM Docker container via the host socket.
    Supports: restart, status, logs, list.
    """
    user_context: UserContext
    action: Literal["restart", "status", "logs", "list"]
    container_name: str = Field("sharedllm_gateway", description="Target container name")
    tail: int = Field(100, ge=1, le=1000, description="Lines to fetch for 'logs' action")


class DockerComposeRequest(BaseRequest):
    """
    Controls SharedLLM Docker containers via docker-compose (emulated via SDK).
    Supports: up, down, restart, logs.
    """
    user_context: UserContext
    action: Literal["up", "down", "restart", "logs"]
    services: Optional[List[str]] = Field(None, description="List of services to act upon (e.g. ['gateway', 'rag'])")
    containers: Optional[List[str]] = Field(None, description="Alias for services")


class VolumeInventoryRequest(BaseRequest):
    """
    Returns tracked Docker volume inventory and usage.
    Admin only.
    """
    user_context: UserContext


class CapabilityIndexRequest(BaseRequest):
    """
    Triggers the JIT Capability Discovery indexing script.
    Refreshes the RAG system's knowledge of available tools.
    """
    user_context: UserContext

class SystemLearningRequest(BaseRequest):
    """
    Persists a successful solution or architectural insight to the System Learnings RAG.
    This helps the agent 'remember' how to solve similar problems in the future.
    """
    user_context: UserContext
    topic: str = Field(..., description="Subject of the learning (e.g. 'Fixing 502 error in Gateway')")
    content: str = Field(..., description="Detailed description of the root cause and the fix applied.")
    tags: List[str] = Field(default_factory=list, description="Keywords for retrieval (e.g. ['gateway', 'bugfix'])")
