"""Music assistant operations module.

Provides music_assistant_ops for use by scripts that need to interact
with the Music Assistant integration.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

log = logging.getLogger("app.logic.music_assistant_ops")


async def play_media(
    entity_id: str,
    media_content_id: str,
    media_content_type: str = "music",
    enqueue: str = "replace",
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Play media via a Music Assistant player.
    Delegates to the Home Assistant play_media service.
    """
    from app.domains.shared import execute_ha_service

    return await execute_ha_service(
        domain="media_player",
        service="play_media",
        entity_id=entity_id,
        service_data={
            "media_content_id": media_content_id,
            "media_content_type": media_content_type,
            "enqueue": enqueue,
            **kwargs,
        },
    )


async def get_media_player_info(entity_id: str) -> Dict[str, Any]:
    """Get information about a Music Assistant media player."""
    from app.domains.shared import execute_ha_service

    return await execute_ha_service(
        domain="homeassistant",
        service="get_relevant_entities",
        service_data={"entity_id": entity_id},
    )


def clean_query(query: str) -> str:
    """Clean and normalize a media search query."""
    if not query:
        return ""
    return query.strip().lower()
