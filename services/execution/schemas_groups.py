"""
Device & Light Grouping System (Section 3.14)

Media Groups: Logical groups of media devices for announcements, alarms, etc.
Light Clusters: Named collections of lights treated as a single unit.
Light Patterns: Named color sequences applied across light cluster members.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MediaGroupRequest(BaseModel):
    action: Literal["create", "delete", "list", "add_member", "remove_member"]
    group_id: str
    group_name: Optional[str] = None
    member_entity_ids: Optional[list[str]] = None
    scope: Literal["user", "system"] = "user"


class LightClusterRequest(BaseModel):
    action: Literal["create", "delete", "list", "add_member", "remove_member"]
    cluster_id: str
    cluster_name: Optional[str] = None
    member_entity_ids: Optional[list[str]] = None
    room: Optional[str] = None
    scope: Literal["user", "system", "room"] = "room"


class LightPatternStep(BaseModel):
    positions: list[int] = Field(default_factory=list)
    rgb: list[int] = Field(default=[255, 255, 255])
    brightness_pct: int = Field(default=100, ge=0, le=100)


class LightPatternRequest(BaseModel):
    action: Literal["create", "delete", "list", "update"]
    pattern_id: str
    pattern_name: Optional[str] = None
    cluster_id: Optional[str] = None
    steps: Optional[list[LightPatternStep]] = None
    loop: bool = False
    transition_ms: int = Field(default=500, ge=0)


SYSTEM_DEFAULT_PATTERNS = [
    {
        "pattern_id": "sunset",
        "pattern_name": "Sunset",
        "steps": [
            {"positions": [0, 1], "rgb": [255, 140, 0], "brightness_pct": 80},
            {"positions": [2, 3], "rgb": [255, 80, 0], "brightness_pct": 60},
            {"positions": [4, 5], "rgb": [200, 40, 0], "brightness_pct": 40},
        ],
    },
    {
        "pattern_id": "christmas",
        "pattern_name": "Christmas",
        "steps": [
            {"positions": [0, 2, 4], "rgb": [255, 0, 0], "brightness_pct": 100},
            {"positions": [1, 3, 5], "rgb": [0, 170, 0], "brightness_pct": 100},
        ],
    },
    {
        "pattern_id": "ocean",
        "pattern_name": "Ocean",
        "steps": [
            {"positions": [0, 1], "rgb": [0, 0, 139], "brightness_pct": 80},
            {"positions": [2, 3], "rgb": [0, 255, 255], "brightness_pct": 90},
            {"positions": [4, 5], "rgb": [0, 128, 128], "brightness_pct": 70},
        ],
    },
    {
        "pattern_id": "daylight",
        "pattern_name": "Daylight",
        "steps": [
            {"positions": [], "rgb": [255, 255, 255], "brightness_pct": 100},
        ],
    },
    {
        "pattern_id": "night_mode",
        "pattern_name": "Night Mode",
        "steps": [
            {"positions": [], "rgb": [255, 0, 0], "brightness_pct": 10},
        ],
    },
    {
        "pattern_id": "party",
        "pattern_name": "Party",
        "steps": [
            {"positions": [], "rgb": [255, 0, 0], "brightness_pct": 100},
        ],
        "loop": True,
    },
]
