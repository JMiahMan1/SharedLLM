"""Schemas for native Echo-to-Echo Drop In."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from services.execution.schemas import BaseRequest


class DropInRequest(BaseRequest):
    """Start, end, or inspect a Drop In between Echo devices.

    ``target_room`` is what the person called the room (or ``room_of:<person>``
    for "where <person> is"). ``target_user_id`` resolves that person's room
    through the hub's Bluetooth room detection. ``target_device`` names one Echo
    directly. ``source_satellite`` is the Echo that heard the request; it defaults
    to ``user_context.voice_satellite``.
    """

    action: Literal["start", "end", "status"] = Field(
        "start", description="start a Drop In, end the active one, or list active Drop Ins."
    )
    target_room: str | None = Field(
        None, description="Room or area name, alias, floor, or 'room_of:<person>'."
    )
    target_user_id: str | None = Field(
        None, description="Person whose current room (hub Bluetooth presence) to drop in on."
    )
    target_device: str | None = Field(
        None, description="Explicit Echo node or friendly name to drop in on."
    )
    source_satellite: str | None = Field(
        None, description="assist_satellite.* entity that heard the request; defaults to the voice satellite."
    )
