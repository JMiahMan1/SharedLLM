# services/execution/schemas.py
"""
Pydantic schemas for all Execution Bridge endpoints.
Strict validation is the primary defense against malformed gateway payloads.
"""
from typing import Optional, Literal, Any, Dict
from pydantic import BaseModel, Field


# ─── Base ───────────────────────────────────────────────────────────────────────

class UserContext(BaseModel):
    """Resolved user credentials forwarded by the Gateway."""
    user: str
    ha_url: str
    ha_token: str


class ExecutionResult(BaseModel):
    status: Literal["SUCCESS", "FAILURE", "PARTIAL"]
    message: str
    service: str
    detail: Optional[Dict[str, Any]] = None


# ─── Media / Music ──────────────────────────────────────────────────────────────

class MediaPlayRequest(BaseModel):
    user_context: UserContext
    entity_id: str = Field(..., description="HA media_player entity ID")
    media_content_id: Optional[str] = None
    media_content_type: Optional[str] = "music"
    # Music Assistant fields
    query: Optional[str] = None
    enqueue: Optional[Literal["add", "next", "replace"]] = "replace"


class MediaTransportRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    command: Literal["pause", "resume", "stop", "next", "previous", "volume_up", "volume_down"]
    volume_level: Optional[float] = Field(None, ge=0.0, le=1.0)


# ─── Lights ─────────────────────────────────────────────────────────────────────

class LightControlRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    action: Literal["turn_on", "turn_off", "toggle"]
    brightness_pct: Optional[int] = Field(None, ge=0, le=100)
    color_temp: Optional[int] = None
    rgb_color: Optional[tuple[int, int, int]] = None


# ─── Generic HA Service Call ────────────────────────────────────────────────────

class HAServiceRequest(BaseModel):
    user_context: UserContext
    domain: str          # e.g. "light", "switch", "media_player"
    service: str         # e.g. "turn_on", "play_media"
    entity_id: str
    service_data: Optional[Dict[str, Any]] = None


# ─── Announcements ──────────────────────────────────────────────────────────────

class AnnouncementRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    message: str
    volume: Optional[float] = Field(0.6, ge=0.0, le=1.0)


# ─── TV / SmartPowerSync ────────────────────────────────────────────────────────

class TVCastRequest(BaseModel):
    """
    Encapsulates the 'SmartPowerSync' pattern:
    power on the TV, wait for readiness, then cast.
    """
    user_context: UserContext
    media_player_entity_id: str
    media_content_id: str
    media_content_type: str = "url"
    power_on_wait_ms: int = Field(3000, ge=0, le=15000)
