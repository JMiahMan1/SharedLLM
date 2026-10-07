# services/rag/schemas.py
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """A hybrid dense-vector + BM25 search over one RAG collection.

    ``query`` is bounded because ``_bm25_search`` hands the *entire* query to
    FTS5 as one quoted phrase, and FTS5's cost for a phrase grows superlinearly
    with its length. Measured against the production index (4,184 rows) inside
    a container with a 6 GiB cgroup limit:

    ==================  ========  =========
    query length        wall time  memory Δ
    ==================  ========  =========
    500 chars             0.7 s      +42 MiB
    2,000 chars           1.9 s      +77 MiB
    4,000 chars           5.5 s     +231 MiB
    8,000 chars          13.0 s     +890 MiB
    40,000 chars        125.4 s    +4,487 MiB
    ==================  ========  =========

    The 40,000-char case allocates 4.5 GiB in roughly two seconds, then pins the
    process at its memory ceiling for two minutes until the kernel OOM-kills it
    (observed as 300+ restarts of ``sharedllm_rag`` in 24 hours, killing
    whatever search happened to be running).

    Over-long queries are refused with a 422 rather than truncated: a silently
    shortened query would return confident results about the wrong half of the
    caller's text, which is exactly the failure mode AGENTS.md forbids.
    """

    MAX_QUERY_CHARS: ClassVar[int] = 2000

    query: str = Field(
        ...,
        max_length=MAX_QUERY_CHARS,
        description=(
            "Search text. At most 2000 characters: FTS5 evaluates the query as a "
            "single phrase and cost grows superlinearly, so a longer query is "
            "refused instead of being answered from a truncated version."
        ),
    )
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
