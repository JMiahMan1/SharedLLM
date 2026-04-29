# services/rag/schemas.py
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

class SearchRequest(BaseModel):
    query: str
    user_id: str = Field(..., description="The user to filter results for")
    k: int = 5
    collection_name: str = "nextcloud" # e.g., 'nextcloud' or 'home_assistant'

class SearchResultItem(BaseModel):
    content: str
    metadata: Dict[str, Any]

class SearchResponse(BaseModel):
    results: List[SearchResultItem]

class IngestRequest(BaseModel):
    user_id: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    collection_name: str = "nextcloud"
