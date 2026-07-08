"""
Schemas for Household Intercom System (Section 3.16).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class IntercomSessionRequest(BaseModel):
    """Start a two-way intercom session."""
    caller_user_id: str
    target_user_id: str | None = None
    target_room: str | None = None
    target_entity_ids: list[str] | None = None
    session_type: str = "twoway"  # "twoway", "broadcast", "announcement"


class IntercomBroadcastRequest(BaseModel):
    """One-way broadcast to devices."""
    message: str
    target_entity_ids: list[str] | None = None
    target_rooms: list[str] | None = None
    volume: float | None = None
    tts_engine: str | None = None
    voice: str | None = None


class IntercomAnnouncementRequest(BaseModel):
    """TV/Smart speaker announcement (one-way)."""
    message: str
    target_devices: list[str] | None = None
    overlay_text: str | None = None


class IntercomSession(BaseModel):
    session_id: str
    caller_user_id: str
    target_user_id: str | None = None
    target_room: str | None = None
    target_entity_ids: list[str] = Field(default_factory=list)
    session_type: str
    status: str  # "active", "ended", "failed"
    started_at: str
    ended_at: str | None = None
    room_name: str | None = None
    tokens: dict | None = None


class IntercomResponse(BaseModel):
    status: str
    message: str
    session: IntercomSession | None = None
    detail: dict | None = None
