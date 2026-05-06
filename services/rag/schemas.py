# services/rag/schemas.py
from typing import Optional, List, Dict, Any
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
    metadata: Dict[str, Any]
    score: Optional[float] = None

class SearchResponse(BaseModel):
    results: List[SearchResultItem]

class IngestRequest(BaseModel):
    user_id: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    collection_name: str = "nextcloud"

class UserFact(BaseModel):
    id: Optional[str] = None
    content: str
    user_id: str
    category: str = "preference" # e.g., 'preference', 'routine', 'entity_mapping'
    extracted_at: float
    confidence: float = 1.0
