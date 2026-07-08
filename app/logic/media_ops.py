"""Media operations module.

Handles media command routing and execution for the SharedLLM monolith.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("app.logic.media_ops")


async def handle_media_command(
    command: str,
    user_creds: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Route and execute a media command.

    Args:
        command: The media command to execute (e.g., 'play', 'pause', 'stop', 'volume_up').
        user_creds: User credentials dict with ha_url, ha_token, etc.
        **kwargs: Additional parameters (device_name, media_content_id, etc.)

    Returns:
        Dict with 'status' and optionally 'data' or 'detail'.
    """
    from app.domains.media.commands import handle_media_command as _handle  # pyright: ignore[reportMissingImports]

    return await _handle(command, user_creds=user_creds, **kwargs)
