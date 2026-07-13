# services/rag/schemas.py
from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str
    user_id: str = Field(..., description="The user to filter results for")
    k: int = 5
    collection_name: str = "nextcloud"
    alpha: float = Field(0.5, description="Weighting for BM25 (0.0) vs Dense Vector (1.0)")
    use_rrf: bool = Field(True, description="Enable Reciprocal Rank Fusion for hybrid results")

class SearchResultItem(BaseModel):
    content: str
    metadata: dict[str, Any]
    score: float | None = None

class SearchResponse(BaseModel):
    results: list[SearchResultItem]

class IngestRequest(BaseModel):
    user_id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection_name: str = "nextcloud"

class UserFact(BaseModel):
    id: str | None = None
    content: str
    user_id: str
    category: str = "preference" # e.g., 'preference', 'routine', 'entity_mapping'
    extracted_at: float
    confidence: float = 1.0


# ─── Section 6: New relational-vector collection schemas ─────────────────────

class MissionRecord(BaseModel):
    mission_id: str
    task_description: str
    final_status: str = "UNKNOWN"  # SUCCESS, FAILURE, ABORTED
    error_summary: str = ""
    steps: list[dict] = Field(default_factory=list)
    user_id: str = "default"
    created_at: float | None = None


class ConversationUtterance(BaseModel):
    utterance_id: str | None = None
    speaker: str = "unknown"
    text_content: str
    room_id: str = "unknown"
    user_id: str = "default"
    timestamp: int | None = None


class NetworkContainer(BaseModel):
    container_name: str
    ip_address: str = ""
    exposed_ports: list[str] = Field(default_factory=list)
    discovered_services: list[str] = Field(default_factory=list)
    network_name: str = ""
    user_id: str = "default"


class TelemetryAlert(BaseModel):
    alert_id: str | None = None
    entity_id: str
    alert_type: str = "generic"
    severity: str = "info"
    content: str = ""
    user_id: str = "default"
    created_at: float | None = None
