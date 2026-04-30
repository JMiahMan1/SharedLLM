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
    ha_url: Optional[str] = None
    ha_token: Optional[str] = None


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


# ─── Personal Data (Calendar / Notes) ──────────────────────────────────────────

class CalendarRequest(BaseModel):
    user_context: UserContext
    action: Literal["list", "read", "add", "delete", "update"]
    query: Optional[str] = None
    summary: Optional[str] = None
    start_time: Optional[str] = None
    calendar_name: Optional[str] = None


class NoteRequest(BaseModel):
    user_context: UserContext
    action: Literal["create", "append", "read", "delete", "check_off"]
    title: str
    content: Optional[str] = None
    category: Optional[str] = "General"
    item: Optional[str] = None # For check_off


# ─── Timers / Alarms ────────────────────────────────────────────────────────────

class TimerRequest(BaseModel):
    user_context: UserContext
    action: Literal["add", "list", "delete", "pause", "resume"]
    type: Literal["timer", "alarm"] = "timer"
    query: Optional[str] = None
    title: Optional[str] = None
    duration_str: Optional[str] = None
    time_str: Optional[str] = None
    recurrence: Optional[str] = None
    target_device: Optional[str] = None
