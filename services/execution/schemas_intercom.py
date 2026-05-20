"""
Schemas for Household Intercom System (Section 3.16).
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class IntercomSessionRequest(BaseModel):
    """Start a two-way intercom session."""
    caller_user_id: str
    target_user_id: Optional[str] = None
    target_room: Optional[str] = None
    target_entity_ids: Optional[List[str]] = None
    session_type: str = "twoway"  # "twoway", "broadcast", "announcement"


class IntercomBroadcastRequest(BaseModel):
    """One-way broadcast to devices."""
    message: str
    target_entity_ids: Optional[List[str]] = None
    target_rooms: Optional[List[str]] = None
    volume: Optional[float] = None
    tts_engine: Optional[str] = None
    voice: Optional[str] = None


class IntercomAnnouncementRequest(BaseModel):
    """TV/Smart speaker announcement (one-way)."""
    message: str
    target_devices: Optional[List[str]] = None
    overlay_text: Optional[str] = None


class IntercomSession(BaseModel):
    session_id: str
    caller_user_id: str
    target_user_id: Optional[str] = None
    target_room: Optional[str] = None
    target_entity_ids: List[str] = Field(default_factory=list)
    session_type: str
    status: str  # "active", "ended", "failed"
    started_at: str
    ended_at: Optional[str] = None
    room_name: Optional[str] = None
    tokens: Optional[dict] = None


class IntercomResponse(BaseModel):
    status: str
    message: str
    session: Optional[IntercomSession] = None
    detail: Optional[dict] = None
