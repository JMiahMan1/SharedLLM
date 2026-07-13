# services/gateway/schemas.py
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    query: str
    voice_id: str | None = None
    device_id: str | None = None
    rag_user: str | None = None
    model: str | None = None
    stream: bool = False
    api_key: str | None = None
    client: str | None = "chat" # chat, voice, home_assistant
    source: str | None = None

class ChatResponse(BaseModel):
    status: Literal["SUCCESS", "FAILURE"]
    message: str
    intent: str | None = None
    confidence: float | None = None
    llm_bypassed: bool = False
    execution_result: dict[str, Any] | None = None

class SemanticRoute(BaseModel):
    name: str
    description: str
    examples: list[str]
    confidence_threshold: float = 0.85

class IntentClassificationResponse(BaseModel):
    intent: str
    confidence: float
    llm_bypassed: bool
    route_name: str | None = None

class ResolvedCredentials(BaseModel):
    user: str
    github_token: str | None = None
    gitlab_token: str | None = None
    git_token: str | None = None
    nextcloud_url: str | None = None
    nextcloud_user: str | None = None
    nextcloud_pass: str | None = None
    ha_url: str | None = None
    ha_token: str | None = None
    audiobookshelf_url: str | None = None
    audiobookshelf_user: str | None = None
    audiobookshelf_pass: str | None = None
    openai_key: str | None = None
    api_key: str | None = None
    mass_url: str | None = None
    mass_token: str | None = None
    is_admin: bool = False

    @field_validator(
        "github_token", "nextcloud_url", "nextcloud_user", "nextcloud_pass",
        "ha_url", "ha_token", "audiobookshelf_url", "audiobookshelf_user", "audiobookshelf_pass",
        "openai_key", "mass_url", "mass_token", mode="before"
    )
    @classmethod
    def coerce_empty_to_none(cls, v: Any) -> str | None:
        if isinstance(v, str) and not v.strip():
            return None
        return v

class OllamaPullRequest(BaseModel):
    model: str | None = None
    name: str | None = None
    stream: bool = False
    insecure: bool = False

class OllamaGenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False
    system: str | None = None
    template: str | None = None
    context: list[int] | None = None
    options: dict[str, Any] | None = None

class StorageListRequest(BaseModel):
    path: str = "/"
    recursive: bool = False

class StorageIndexRequest(BaseModel):
    path: str = "/"
    recursive: bool = True
    force: bool = False

class StorageStatusRequest(BaseModel):
    """
    Checks the current indexing status and file counts from the storage/RAG backend.
    Requires no parameters.
    """
    pass

class WorkspaceFileReadRequest(BaseModel):
    path: str

class WorkspaceFileWriteRequest(BaseModel):
    path: str
    content: str

class PatchChunk(BaseModel):
    old_text: str = Field(..., alias="target_content", description="The exact text to find and replace")
    new_text: str = Field(..., alias="replacement_content", description="The new text to replace it with")

    model_config = {"populate_by_name": True}

class WorkspaceFilePatchRequest(BaseModel):
    path: str = Field(..., alias="file_path")
    chunks: list[PatchChunk] = Field(..., alias="patch")
    commit_after: bool = False
    commit_message: str | None = None

    model_config = {"populate_by_name": True}

class WorkspaceShellRequest(BaseModel):
    command: str

class GitOperationRequest(BaseModel):
    workspace_id: str | None = Field(None, description="Workspace ID (uses default if not specified)")
    action: Literal["status", "diff", "add", "commit", "pull", "push", "log", "fetch", "reset", "branch", "checkout", "clean", "show", "init", "remote_add", "repo_create"]
    path: str | None = "."
    message: str | None = None
    branch: str | None = "microservices"
    log_count: int | None = 10
    remote_name: str | None = None
    repo_url: str | None = None
    repo_name: str | None = None
    private: bool = False
    description: str | None = None
    source_path: str | None = None

class ControlPlaneRequest(BaseModel):
    service_name: str
    action: Literal["restart", "status"]

class SystemLearningRequest(BaseModel):
    key: str
    content: str
    category: str = "general"

class AnnouncementRequest(BaseModel):
    entity_id: str
    message: str
    volume: float | None = 0.6
    tts_engine: Literal["kokoro", "piper"] | None = "kokoro"
    storybook: bool = False
    save_path: str | None = None

class HAServiceRequest(BaseModel):
    domain: str
    service: str
    entity_id: str
    service_data: dict[str, Any] | None = {}

class WorkspaceBootstrapRequest(BaseModel):
    workspace_id: str | None = None
    local_path: str | None = None
    repo_url: str | None = None
    branch: str | None = None
    create_if_missing: bool = True

class ContextSearchRequest(BaseModel):
    """Search RAG collections for context when initial retrieval is insufficient."""
    query: str = Field(..., description="Natural language search query")
    collection_name: str = Field("system_capabilities", description="Target collection: ha_entities, nextcloud_files, system_capabilities, system_learnings")
    k: int = Field(5, description="Number of results to return")

class HAConfigRequest(BaseModel):
    """Inspect Home Assistant integration configurations via WebSocket API."""
    action: str = Field("list_integrations", description="Action: list_integrations, get_integration, get_entities, get_config")
    domain: str | None = Field(None, description="Integration domain (e.g. 'ollama', 'webostv')")
    entity_domain: str | None = Field(None, description="Entity domain filter (e.g. 'light', 'media_player')")
    keyword: str | None = Field(None, description="Search keyword to filter results")
