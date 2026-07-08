# services/execution/schemas.py
"""
Pydantic schemas for all Execution Bridge endpoints.
Strict validation is the primary defense against malformed gateway payloads.
"""
from typing import Optional, Literal, Any, Dict, List
from pydantic import BaseModel, Field, model_validator, ConfigDict


class BaseRequest(BaseModel):
    workspace_id: Optional[str] = None
    model_config = {"extra": "ignore"}

    @model_validator(mode='before')
    @classmethod
    def handle_common_aliases(cls, data: Any) -> Any:
        """Automatically map common hallucinations to schema-correct fields."""
        if isinstance(data, dict):
            field_names = set(cls.model_fields.keys()) if hasattr(cls, 'model_fields') else set()

            # Map 'tool_name' or 'function_name' to 'action' (common LLM hallucination)
            if "action" not in data and "action" in field_names:
                if "tool_name" in data:
                    data["action"] = data["tool_name"]
                elif "function_name" in data:
                    data["action"] = data["function_name"]
                elif "command" in data:
                    data["action"] = data["command"]
                elif "operation" in data:
                    data["action"] = data["operation"]
                elif "message" in data:
                    data["action"] = "send"
                elif "text_to_voice" in data:
                    data["action"] = "send"
                elif "token" in data:
                    data["action"] = "messages"

            # Map 'target' to 'action' if action is missing and schema has action
            if "action" not in data and "action" in field_names and "target" in data:
                data["action"] = data["target"]

            # Map 'request' or 'parameters' to 'payload' (common LLM hallucination)
            if "payload" not in data and "payload" in field_names:
                if "request" in data:
                    data["payload"] = data["request"]
                elif "parameters" in data:
                    data["payload"] = data["parameters"]
                elif "input" in data:
                    data["payload"] = data["input"]

            # Map 'action' to 'payload' for schema-only actions (e.g. TalkRequest)
            if "payload" not in data and "payload" in field_names and "action" in data:
                action = data.get("action")
                if action == "send" or action == "messages":
                    # If we have message/text_to_voice/token, put it in payload
                    if "message" in data:
                        data["payload"] = data["message"]
                    elif "text_to_voice" in data:
                        data["payload"] = data["text_to_voice"]
                    elif "token" in data:
                        data["payload"] = data["token"]
                    elif "text" in data:
                        data["payload"] = data["text"]
                    elif "content" in data:
                        data["payload"] = data["content"]

            # Map 'git_status' to 'status', etc.
            if "action" in data and isinstance(data["action"], str) and data["action"].startswith("git_"):
                data["action"] = data["action"].replace("git_", "")

            # Map 'file_path', 'repository_path', or 'file' to 'path'
            if "path" not in data and "path" in field_names:
                if "file_path" in data:
                    data["path"] = data["file_path"]
                elif "repository_path" in data:
                    data["path"] = data["repository_path"]
                elif "file" in data:
                    data["path"] = data["file"]

            # Map 'message' to 'commit_message' or vice-versa
            if "commit_message" in field_names:
                if "message" in data and "commit_message" not in data:
                    data["commit_message"] = data["message"]
                if "commit_message" in data and "message" not in data:
                    data["message"] = data["commit_message"]

            # Map 'limit' to 'limit_lines' and 'offset' to 'offset_lines'
            if "limit" in data and "limit_lines" not in data:
                data["limit_lines"] = data["limit"]
            if "offset" in data and "offset_lines" not in data:
                data["offset_lines"] = data["offset"]

            # Map 'query' to 'search_query' or 'query_text' where applicable
            if "search_query" in field_names and "search_query" not in data and "query" in data:
                data["search_query"] = data["query"]
            if "query_text" in field_names and "query_text" not in data and "query" in data:
                data["query_text"] = data["query"]

            # Map 'response_format' to 'format' where applicable
            if "format" in field_names and "format" not in data and "response_format" in data:
                data["format"] = data["response_format"]

            # Normalize empty strings to None for optional fields only
            for field_name in field_names:
                field_info = cls.model_fields.get(field_name)
                if field_info is not None and not field_info.is_required():
                    if field_name in data and data[field_name] == "":
                        data[field_name] = None

        return data

class UserContext(BaseModel):
    """Resolved user credentials forwarded by the Gateway."""
    model_config = {"extra": "ignore"}
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
    git_url: Optional[str] = None
    git_user: Optional[str] = None
    api_key: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    audiobookshelf_pass: Optional[str] = None
    audiobookshelf_api_key: Optional[str] = None
    preferred_tts_voice: Optional[str] = None





class IdentityRequest(BaseRequest):
    user_context: UserContext
    action: Literal["list", "import_nextcloud", "discover", "create", "delete"]
    username: Optional[str] = None
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_admin: bool = False

class IdentityManageRequest(BaseRequest):
    """
    Extended identity management: user profile updates, device assignments,
    API key management, and credential rotation.
    """
    user_context: UserContext
    action: Literal["update_password", "update_user", "assign_device", "list_devices", "generate_key", "revoke_key", "list_keys", "get_profile"]
    username: Optional[str] = None
    display_name: Optional[str] = None
    category: Optional[str] = None
    is_admin: Optional[bool] = None

class ExecutionResult(BaseModel):
    model_config = {"extra": "ignore"}
    status: Literal["SUCCESS", "FAILURE", "PARTIAL"]
    message: str
    service: str
    detail: Optional[Dict[str, Any]] = None

class GitExecutionResult(ExecutionResult):
    """Specific result for Git operations."""
    pass

class DiagnosticRequest(BaseRequest):
    user_context: UserContext
    service: str = "execution"
    lines: int = 50


class LLMInfoRequest(BaseRequest):
    """Query Alpaca/Ollama for model and system information."""
    user_context: UserContext
    action: str = Field("list", description="Action: 'list' (available models), 'ps' (loaded models), 'version' (server version), 'show' (model details)")
    model: Optional[str] = Field(None, description="Model name for 'show' action (e.g., 'qwen3.6-35b-a3b:q4_k_m')")


# ─── Media / Music ──────────────────────────────────────────────────────────────

class MediaPlayRequest(BaseRequest):
    """Unified media play request supporting all content types and devices."""
    user_context: UserContext
    entity_id: Optional[str] = Field(None, description="HA media_player entity ID (optional if device_name provided)")
    device_name: Optional[str] = Field(None, description="Human-readable device name (e.g., 'Office TV', 'Master Bedroom speaker')")
    query: Optional[str] = Field(None, description="Search query: song/album/artist name, video title, podcast name, audiobook title, or URL")
    media_type: Optional[str] = Field(None, description="Content type: 'music', 'video', 'podcast', 'audiobook', 'radio', 'url', 'announcement'")
    media_content_id: Optional[str] = Field(None, description="Direct URL or media ID (bypasses search)")
    media_content_type: Optional[str] = Field(None, description="HA media_content_type hint (e.g., 'music', 'video', 'url', 'audio/wav')")
    enqueue: Optional[Literal["add", "next", "replace"]] = Field("replace", description="Queue behavior for Music Assistant")
    volume: Optional[float] = Field(None, ge=0.0, le=1.0, description="Set volume before playback")


class MediaTransportRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    command: Literal["pause", "resume", "stop", "next", "previous", "volume_up", "volume_down", "home", "power_off", "back", "play", "volume_set"]
    volume_level: Optional[float] = Field(None, ge=0.0, le=1.0)


class MediaStateSyncRequest(BaseRequest):
    """Sync request for local or remote media state."""
    user_context: UserContext
    entity_id: str = Field("local", description="Playback target (e.g. 'local' or HA entity_id)")
    state: str = Field("idle", description="Playback state (playing, paused, idle)")
    media_type: Optional[str] = None
    query: Optional[str] = None
    media_content_id: Optional[str] = None
    position: Optional[float] = 0.0
    duration: Optional[float] = 0.0
    volume_level: Optional[float] = None
    is_volume_muted: Optional[bool] = None
    media_title: Optional[str] = None
    media_artist: Optional[str] = None
    media_album: Optional[str] = None
    queue: Optional[list] = None



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

class LogbookRequest(BaseRequest):
    user_context: UserContext
    entity_id: str
    days: int = Field(1, ge=1, le=7)


# ─── Announcements ──────────────────────────────────────────────────────────────

class AnnouncementRequest(BaseRequest):
    user_context: UserContext
    entity_id: Optional[str] = Field(None, description="Exact HA entity ID (e.g., media_player.office_tv_chrome). If omitted, resolved from device_name.")
    device_name: Optional[str] = Field(None, description="Human-readable device name for entity resolution (e.g., 'Office TV')")
    message: str
    volume: Optional[float] = Field(0.6, ge=0.0, le=1.0)
    tts_engine: Optional[Literal["kokoro", "piper"]] = "kokoro"
    storybook: bool = False
    save_path: Optional[str] = Field(None, description="Optional path in Nextcloud to save the announcement audio")






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


# ─── Video Playback (YouTube via yt-dlp) ───────────────────────────────────────

class VideoPlayRequest(BaseRequest):
    """
    Plays video on a media player by extracting a direct MP4 stream URL via yt-dlp.
    Works on Cast, AndroidTV, and any device that supports video/mp4 playback.
    """
    user_context: UserContext
    entity_id: str
    query: str = Field(..., description="YouTube URL or search query (e.g., 'Brandon Lake live worship')")


# ─── Media Status ───────────────────────────────────────────────────────────────

class MediaStatusRequest(BaseRequest):
    """Query what is currently playing across media devices."""
    user_context: UserContext
    area: Optional[str] = Field(None, description="Filter by area (e.g., 'Office', 'Living Room')")
    entity_id: Optional[str] = Field(None, description="Specific entity to query")


class EntitySearchRequest(BaseRequest):
    """Search for Home Assistant entities by name, type, or area when entity_id is unknown."""
    user_context: UserContext
    query: str = Field(..., description="Search term (e.g., 'office tv', 'kitchen light', 'bedroom speaker')")
    domain: Optional[str] = Field(None, description="Filter by domain (e.g., 'media_player', 'light', 'switch')")
    area: Optional[str] = Field(None, description="Filter by area (e.g., 'Office', 'Kitchen', 'Master Bedroom')")
    state: Optional[str] = Field(None, description="Filter by state (e.g., 'on', 'off', 'playing', 'idle')")


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
    action: Literal["create", "append", "read", "delete", "check_off", "list", "sync_rag"]
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = "General"
    item: Optional[str] = None # For check_off
    storage: Optional[Literal["nextcloud", "local"]] = "nextcloud"
    directories: Optional[list[str]] = None # Custom Nextcloud directories to scan (recursive)
    path: Optional[str] = None # Specific file path for read/write operations


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
    path: str = Field(..., alias="file_path", description="Path relative to workspace root (e.g. 'services/gateway/main.py')")
    offset_lines: int = Field(0, ge=0, description="Start reading from this line number (1-indexed)")
    limit_lines: int = Field(1000, ge=1, le=5000, description="Max lines to read")
    summary_only: bool = Field(False, description="If true, returns only class/function signatures and docstrings (semantic map)")

    @model_validator(mode="before")
    @classmethod
    def pivot_file_read_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "file_path" in data and "path" not in data:
                data["path"] = data.pop("file_path")
        return data

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

class WorkspaceFileWriteRequest(BaseRequest):
    """
    Writes or overwrites a file in the local Git workspace.
    Requires the FULL file content in the 'content' field.
    """
    user_context: UserContext
    path: str = Field(..., alias="file_path", description="Path relative to workspace root")
    content: str
    commit_after: bool = False
    commit_message: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def pivot_file_write_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "file_path" in data and "path" not in data:
                data["path"] = data.pop("file_path")
        return data

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

class ReplacementChunk(BaseModel):
    model_config = {"extra": "ignore", "populate_by_name": True}
    old_text: str = Field(..., alias="target_content", description="The exact text to be replaced")
    new_text: str = Field(..., alias="replacement_content", description="The replacement text")

    @model_validator(mode="before")
    @classmethod
    def pivot_chunk_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Pivot 'patch' or 'content' to 'new_text' if old_text is provided or empty
            if "patch" in data and "new_text" not in data:
                data["new_text"] = data.pop("patch")
            if "content" in data and "new_text" not in data:
                data["new_text"] = data.pop("content")
            
            # Pivot 'target_content' or 'original' to 'old_text'
            if "target_content" in data and "old_text" not in data:
                data["old_text"] = data.pop("target_content")
            if "original" in data and "old_text" not in data:
                data["old_text"] = data.pop("original")
            if "search" in data and "old_text" not in data:
                data["old_text"] = data.pop("search")
            if "replace" in data and "new_text" not in data:
                data["new_text"] = data.pop("replace")

            # Fallback: if old_text is missing but we have a start_line hallucination
            if "old_text" not in data:
                # We can't easily get the old text from just a line number here without the file,
                # so we default to "" and hope the unified diff parser or fuzzy matcher handles it
                # OR we just let it fail if it's truly empty.
                # However, many agents use empty old_text for insertion.
                data["old_text"] = ""
        return data

class WorkspaceFilePatchRequest(BaseRequest):
    """
    Surgically patches a file in the local Git workspace.
    Use this for small fixes to avoid providing the full file content.
    """
    user_context: UserContext
    path: str = Field(..., alias="file_path", description="Path relative to workspace root")
    chunks: List[ReplacementChunk] = Field(..., alias="patch")
    commit_after: bool = False
    commit_message: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def pivot_file_patch_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Pivot 'file_path' to 'path'
            if "file_path" in data and "path" not in data:
                data["path"] = data.pop("file_path")
            
            # Pivot 'patch' or 'patches' to 'chunks'
            patch_data = data.get("patch") or data.get("patches")
            if patch_data and "chunks" not in data:
                if isinstance(patch_data, str):
                    # Robustly parse unified diff including context
                    old_lines = []
                    new_lines = []
                    for line in patch_data.splitlines():
                        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                            continue
                        if line.startswith("-"):
                            old_lines.append(line[1:])
                        elif line.startswith("+"):
                            new_lines.append(line[1:])
                        else:
                            # Context line - add to both
                            clean_line = line[1:] if line.startswith(" ") else line
                            old_lines.append(clean_line)
                            new_lines.append(clean_line)
                    
                    if old_lines or new_lines:
                        # Join and clean
                        old_text = "\n".join(old_lines).strip()
                        new_text = "\n".join(new_lines).strip()
                        if old_text or new_text:
                            data["chunks"] = [{"old_text": old_text, "new_text": new_text}]
                elif isinstance(patch_data, dict):
                    # Convert search:replace dict to chunks
                    new_chunks = []
                    for k, v in patch_data.items():
                        clean_k = k.lstrip("- ").strip()
                        clean_v = v.lstrip("+ ").strip()
                        new_chunks.append({"old_text": clean_k, "new_text": clean_v})
                    data["chunks"] = new_chunks
                elif isinstance(patch_data, list):
                    data["chunks"] = patch_data
                
                # Cleanup to avoid alias confusion
                data.pop("patch", None)
                data.pop("patches", None)
        return data

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

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

    @model_validator(mode="before")
    @classmethod
    def pivot_lint_path(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "relative_path" in data and "path" not in data:
                data["path"] = data.pop("relative_path")
            if "file_path" in data and "path" not in data:
                data["path"] = data.pop("file_path")
        return data

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
    """Performs a web search via SearXNG JSON API."""
    user_context: UserContext
    query: str
    category: Optional[str] = Field(None, description="Search category: general, images, videos, news, music, files, it, science, social_media")
    engines: Optional[str] = Field(None, description="Comma-separated engine list (e.g. 'google,bing,duckduckgo')")
    time_range: Optional[str] = Field(None, description="Time filter: day, week, month, year")
    safesearch: Optional[int] = Field(None, ge=0, le=2, description="Safe search level: 0=off, 1=moderate, 2=strict")
    language: Optional[str] = Field("en", description="Locale code for results (e.g. 'en', 'de', 'fr')")
    pageno: Optional[int] = Field(None, ge=1, description="Page number for pagination")
    max_results: Optional[int] = Field(None, description="Maximum number of results to return")

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
    Performs a Git lifecycle operation on a workspace.
    push requires is_admin=True in user_context.
    """
    user_context: UserContext
    workspace_id: Optional[str] = Field(None, description="Workspace ID (uses default if not specified)")
    action: Literal["status", "diff", "add", "commit", "pull", "push", "log", "fetch", "reset", "branch", "checkout", "clean", "show"]
    path: Optional[str] = Field(".", description="File path for 'add' action")
    commit_message: Optional[str] = Field(None, description="Required for 'commit' action")
    branch: Optional[str] = Field("microservices", description="Branch for pull/push")
    log_count: Optional[int] = Field(10, ge=1, le=50, description="Number of commits for 'log'")

    model_config = ConfigDict(extra='ignore')

    @model_validator(mode='before')
    @classmethod
    def pivot_git_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Pivot path hallucinations
            for alias in ["file_path", "filepath", "target_path", "target", "paths"]:
                if alias in data and "path" not in data:
                    val = data.pop(alias)
                    # If it is a list (like ["path"]), take the first one
                    data["path"] = val[0] if isinstance(val, list) and val else val
            # Pivot message hallucinations
            if "message" in data and "commit_message" not in data:
                data["commit_message"] = data["message"]
            elif "commit_message" in data and "message" not in data:
                data["message"] = data["commit_message"]
        return data


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

class TTSRequest(BaseRequest):
    """
    Converts text to speech using the local Kokoro engine or Edge-TTS.
    Set storybook=True for multi-speaker narration with dialogue detection.
    """
    user_context: UserContext
    text: str = Field(..., description="The text to convert to speech")
    voice: Optional[str] = Field("af_heart", description="Voice ID (e.g. af_heart, am_adam, en-US-GuyNeural)")
    storybook: bool = Field(False, description="Enable multi-speaker narration for stories/dialogue")

class StorageTextToAudioRequest(BaseRequest):
    """
    Converts a text file in Nextcloud storage to an audio file (narration).
    Supports Storybook mode for high-quality multi-speaker output.
    """
    user_context: UserContext
    input_path: str = Field(..., description="Path to the source text file in Nextcloud")
    output_path: Optional[str] = Field(None, description="Path where the audio file should be saved (default: same name with .wav)")
    voice: Optional[str] = Field("af_heart", description="Voice ID")
    storybook: bool = Field(True, description="Enable Storybook mode for better narration")


class ExecutionLogRequest(BaseRequest):
    """
    Queries the Execution service logs for recent activity.
    Use this to verify that a task was performed or troubleshoot failures.
    """
    user_context: UserContext
    service: Optional[str] = Field(None, description="Filter by handler (e.g., 'announce', 'media', 'light', 'ha_client')")
    lines: int = Field(50, ge=1, le=500, description="Number of recent log lines to retrieve")
    keyword: Optional[str] = Field(None, description="Filter logs containing this keyword (e.g., 'FAILED', 'OK', 'announce')")


class AudiobookshelfRequest(BaseRequest):
    """
    Interacts with Audiobookshelf (ABS) for searching, playing, and tracking audiobooks.
    """
    user_context: UserContext
    action: Literal["search", "play", "resume", "progress", "libraries", "list", "get_book", "last_played"]
    query: Optional[str] = Field(None, description="Search query or book title")
    book_id: Optional[str] = Field(None, description="ABS item ID for play/resume/progress")
    entity_id: Optional[str] = Field(None, description="Home Assistant media_player entity to play on")
    library_id: Optional[str] = Field(None, description="ABS library ID to browse")
    limit: int = Field(10, ge=1, le=50, description="Max results to return")


class DocumentBroadcastRequest(BaseRequest):
    """
    Reads a document from Nextcloud storage and broadcasts it as TTS
    to a Home Assistant media_player.
    """
    user_context: UserContext
    input_path: str = Field(..., description="Nextcloud path to the text file")
    entity_id: str = Field(..., description="HA media_player entity to broadcast to")
    summary: Optional[str] = Field(None, description="Pre-written summary (uses first 500 chars if omitted)")
    voice: Optional[str] = Field(None, description="TTS voice ID")


class NightModeRequest(BaseRequest):
    """
    Activates night mode: turns off lights, sets climate to sleep temperature,
    and optionally starts sleep sounds or an audiobook.
    """
    user_context: UserContext
    lights: Optional[Any] = Field("all", description="List of light entity_ids or 'all'")
    climate_entity: Optional[str] = Field(None, description="Climate entity to adjust")
    sleep_temp: float = Field(68.0, description="Target sleep temperature (F)")
    media_entity: Optional[str] = Field(None, description="Optional media_player for sleep sounds")
    media_query: Optional[str] = Field(None, description="Search query for sleep sounds/audiobook")


class NetworkDeviceScanRequest(BaseRequest):
    """
    Scans the local network for devices by probing known ports (Roku ECP, webOS,
    Samsung, Chromecast, ESPHome, etc.) and enriches results with device info
    (model, serial, MAC address, friendly name).
    
    Returns a list of all discovered devices with their IP, type, and metadata.
    Use this to find devices when you don't know their IP or entity_id.
    """
    user_context: UserContext
    subnet: Optional[str] = Field(None, description="Subnet to scan (e.g. '192.168.2.0/24'). Auto-detected from host network if omitted.")
    device_type: Optional[str] = Field(None, description="Filter by device type: 'roku', 'webos', 'samsung', 'cast', 'androidtv', 'esphome', 'all'")
    include_mac: Optional[bool] = Field(True, description="Include MAC address lookup from ARP cache")


class HAConfigRequest(BaseRequest):
    """
    Inspects Home Assistant integration configurations via WebSocket API.
    Use this to diagnose misconfigured integrations (e.g. wrong Ollama URL,
    incorrect entity IDs, disabled components).
    
    NOTE: This tool is primarily for Voice Assistant troubleshooting in HA.
    It should NOT be used for general chat queries or by OpenWebUI clients.
    Only use when the user explicitly asks to check HA integration settings
    or when diagnosing why a HA integration isn't working.
    """
    user_context: UserContext
    action: Literal["list_integrations", "get_integration", "get_entities", "get_config"] = Field(
        "list_integrations",
        description="Action to perform: list all integration domains, get a specific integration's config entries, list entities by domain, or get full HA config"
    )
    domain: Optional[str] = Field(None, description="Integration domain to inspect (e.g. 'ollama', 'webostv', 'roborock')")
    entity_domain: Optional[str] = Field(None, description="Entity domain to filter by (e.g. 'light', 'media_player', 'weather')")
    keyword: Optional[str] = Field(None, description="Search keyword to filter integrations or entities")


class ResolveStreamRequest(BaseRequest):
    """Request to resolve a media query (track name, video title, etc.) into a playable stream URL."""
    user_context: UserContext
    query: str

