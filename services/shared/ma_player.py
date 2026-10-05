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


def ma_player_id(
    attrs: Mapping[str, Any] | None,
    entity_id: str = "",
) -> str | None:
    """Return the Music Assistant player id for an MA player, when known.

    Newer HA MA integrations expose the player id directly as
    `mass_player_id`. When that is absent, a player's own queue id
    (`active_queue`) is still evidence: for a single player,
    queue_id == player_id (docs/MEDIA_UPSTREAM_API_NOTES.md, player_queues/get).
    Returns None for non-MA players or when neither attribute is present.
    """
    if not is_music_assistant_player(attrs, entity_id):
        return None
    attrs = attrs or {}
    value = attrs.get("mass_player_id") or attrs.get("active_queue")
    return str(value) if value is not None else None
