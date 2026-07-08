"""Pipeline module.

Provides pipeline-level functions for command execution flow.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("app.logic.pipeline")


async def _handle_single_command(  # pyright: ignore[reportUnusedFunction]
    command: str,
    user_creds: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Handle a single command through the execution pipeline.

    This is the internal pipeline step that routes commands to the appropriate
    handler (media, HA service, etc.).
    """
    from app.logic.media_ops import handle_media_command

    try:
        result = await handle_media_command(command, user_creds=user_creds, **kwargs)
        return result
    except Exception as e:
        log.error(f"Pipeline error handling command '{command}': {e}")
        return {"status": "FAILURE", "detail": str(e)}
