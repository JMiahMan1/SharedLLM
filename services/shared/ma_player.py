"""Canonical Music Assistant player detection.

Multiple handlers reimplemented slight variants of this check (integration,
source, active_queue, app_id, mass_player_type, mass_ entity prefix). Keep one
truth here so media status, ABS, media play, and Roku sibling lookup agree.
"""

from __future__ import annotations

from typing import Any, Mapping


def is_music_assistant_player(
    attrs: Mapping[str, Any] | None,
    entity_id: str = "",
    *,
    require_active_queue: bool = False,
) -> bool:
    """True when HA attributes (and optional entity id) indicate an MA player.

    require_active_queue: callers that must only target an actively queued
    MA player (e.g. absolute playback routing) can demand a non-None
    active_queue. Idle MA players remain valid for status/resume/sibling
    discovery.
    """
    if not attrs:
        attrs = {}

    integration = str(attrs.get("integration") or "")
    source = str(attrs.get("source") or "").lower()
    active_queue = attrs.get("active_queue")

    looks_ma = (
        integration == "music_assistant"
        or "music assistant" in source
        or active_queue is not None
        or attrs.get("app_id") == "music_assistant"
        or bool(attrs.get("mass_player_type"))
        or (entity_id or "").startswith("media_player.mass_")
    )
    if not looks_ma:
        return False
    if require_active_queue and active_queue is None:
        return False
    return True
