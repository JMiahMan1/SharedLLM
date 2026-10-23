"""Record what people do inside Jarvis (music, games, pages, verses).

The per-person feed lives in telemetry (``tel:activity:<user>``) and a profile
reads it back; this module is the recorder other handlers call. Recording is
best effort by design: a missing entry is never worth failing the action that
produced it, so every failure is logged at debug and swallowed.
"""
import logging
from typing import Any

import aiohttp

from services import config

log = logging.getLogger("execution.activity")

_TIMEOUT = aiohttp.ClientTimeout(total=10.0)


async def record(
    user: str,
    feature: str,
    item: str = "",
    action: str = "view",
    duration_s: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one activity event for ``user``; never raises."""
    user = (user or "").strip()
    feature = (feature or "").strip()
    if not user or not feature:
        return
    try:
        from services.common.http import get_client

        client = get_client()
        payload = {
            "user": user,
            "feature": feature,
            "item": item,
            "action": action,
            "duration_s": duration_s,
            "detail": detail or {},
        }
        async with client.post(
            f"{config.TELEMETRY_SVC_URL.rstrip('/')}/api/telemetry/activity",
            json=payload,
            headers={"X-Internal-Secret": config.INTERNAL_SECRET},
            timeout=_TIMEOUT,
        ) as resp:
            if resp.status != 200:
                log.debug("activity record for %s failed: HTTP %s", user, resp.status)
    except Exception as e:  # noqa: BLE001 - best effort by design
        log.debug("activity record for %s failed: %s", user, e)
