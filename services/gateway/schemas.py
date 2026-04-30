# services/gateway/schemas.py
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel

class ChatRequest(BaseModel):
    query: str
    voice_id: Optional[str] = None
    device_id: Optional[str] = None
    rag_user: Optional[str] = None
    model: Optional[str] = None
    stream: bool = False

class ChatResponse(BaseModel):
    status: Literal["SUCCESS", "FAILURE"]
    message: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    execution_result: Optional[Dict[str, Any]] = None

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
    context: Optional[list[int]] = None
    options: Optional[Dict[str, Any]] = None
