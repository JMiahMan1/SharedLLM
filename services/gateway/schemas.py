# services/gateway/schemas.py
from typing import Optional, Dict, Any, Literal, List
from pydantic import BaseModel, Field, field_validator

class ChatRequest(BaseModel):
    query: str
    voice_id: Optional[str] = None
    device_id: Optional[str] = None
    rag_user: Optional[str] = None
    model: Optional[str] = None
    stream: bool = False
    api_key: Optional[str] = None
    client: Optional[str] = "chat" # chat, voice, home_assistant
    source: Optional[str] = None

class ChatResponse(BaseModel):
    status: Literal["SUCCESS", "FAILURE"]
    message: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    llm_bypassed: bool = False
    execution_result: Optional[Dict[str, Any]] = None

class SemanticRoute(BaseModel):
    name: str
    description: str
    examples: List[str]
    confidence_threshold: float = 0.85

class IntentClassificationResponse(BaseModel):
    intent: str
    confidence: float
    llm_bypassed: bool
    route_name: Optional[str] = None

class ResolvedCredentials(BaseModel):
    user: str
    github_token: Optional[str] = None
    gitlab_token: Optional[str] = None
    git_token: Optional[str] = None
    nextcloud_url: Optional[str] = None
    nextcloud_user: Optional[str] = None
    nextcloud_pass: Optional[str] = None
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None
    audiobookshelf_url: Optional[str] = None
    audiobookshelf_user: Optional[str] = None
    audiobookshelf_pass: Optional[str] = None
    openai_key: Optional[str] = None
    api_key: Optional[str] = None
    mass_url: Optional[str] = None
    mass_token: Optional[str] = None
    is_admin: bool = False

    @field_validator(
        "github_token", "nextcloud_url", "nextcloud_user", "nextcloud_pass",
        "ha_url", "ha_token", "audiobookshelf_url", "audiobookshelf_user", "audiobookshelf_pass",
        "openai_key", "mass_url", "mass_token", mode="before"
    )
    @classmethod
    def coerce_empty_to_none(cls, v: Any) -> Optional[str]:
        if isinstance(v, str) and not v.strip():
            return None
        return v

class OllamaPullRequest(BaseModel):
    model: Optional[str] = None
    name: Optional[str] = None
    stream: bool = False
    insecure: bool = False

class OllamaGenerateRequest(BaseModel):
    model: str
    prompt: str
    stream: bool = False
    system: Optional[str] = None
    template: Optional[str] = None
    context: Optional[List[int]] = None
    options: Optional[Dict[str, Any]] = None

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
    chunks: List[PatchChunk] = Field(..., alias="patch")
    commit_after: bool = False
    commit_message: Optional[str] = None

    model_config = {"populate_by_name": True}

class WorkspaceShellRequest(BaseModel):
    command: str

class GitOperationRequest(BaseModel):
    workspace_id: Optional[str] = Field(None, description="Workspace ID (uses default if not specified)")
    action: Literal["status", "diff", "add", "commit", "pull", "push", "log", "fetch", "reset", "branch", "checkout", "clean", "show"]
    path: Optional[str] = "."
    message: Optional[str] = None
    branch: Optional[str] = "microservices"
    log_count: Optional[int] = 10

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
    volume: Optional[float] = 0.6
    tts_engine: Optional[Literal["kokoro", "piper"]] = "kokoro"
    storybook: bool = False
    save_path: Optional[str] = None

class HAServiceRequest(BaseModel):
    domain: str
    service: str
    entity_id: str
    service_data: Optional[Dict[str, Any]] = {}

class WorkspaceBootstrapRequest(BaseModel):
    workspace_id: Optional[str] = None
    local_path: Optional[str] = None
    repo_url: Optional[str] = None
    branch: Optional[str] = None
    create_if_missing: bool = True

class ContextSearchRequest(BaseModel):
    """Search RAG collections for context when initial retrieval is insufficient."""
    query: str = Field(..., description="Natural language search query")
    collection_name: str = Field("system_capabilities", description="Target collection: ha_entities, nextcloud_files, system_capabilities, system_learnings")
    k: int = Field(5, description="Number of results to return")

class HAConfigRequest(BaseModel):
    """Inspect Home Assistant integration configurations via WebSocket API."""
    action: str = Field("list_integrations", description="Action: list_integrations, get_integration, get_entities, get_config")
    domain: Optional[str] = Field(None, description="Integration domain (e.g. 'ollama', 'webostv')")
    entity_domain: Optional[str] = Field(None, description="Entity domain filter (e.g. 'light', 'media_player')")
    keyword: Optional[str] = Field(None, description="Search keyword to filter results")
