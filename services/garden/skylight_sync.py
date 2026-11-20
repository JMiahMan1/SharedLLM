"""Opt-in Skylight chore mirror for Jarvis Garden (Phase 2).

When a plant or location has ``skylight_sync_enabled = true`` and a
``skylight_chore_id`` is set, care actions logged through
``POST /plants/{id}/care`` are mirrored to Skylight as chore completions
via Execution's ``/api/integrations/skylight/chores/{chore_id}/complete``.
Opt-in per plant/location. Best-effort: never fails the care-log request.
"""
from __future__ import annotations

import logging

from sqlmodel import Session, select

from services import config
from services.garden.models import Plant, Location

log = logging.getLogger("garden.skylight_sync")


def should_sync(obj: Plant | Location) -> bool:
    """True if this plant/location has Skylight sync enabled."""
    return bool(getattr(obj, "skylight_sync_enabled", False))


def mirror_care_log(
    session: Session,
    plant_id: str | None = None,
    location_id: str | None = None,
    action: str = "water",
    performed_by: str | None = None,
) -> None:
    """Mirror a care action to Skylight if the plant/location has opt-in sync.

    Calls Execution's ``POST /api/integrations/skylight/chores/{chore_id}/complete``.
    Degrades gracefully (one log line) when Skylight is unconfigured,
    unreachable, or the plant/location has no Skylight chore id.
    """
    target = None
    if plant_id:
        target = session.get(Plant, plant_id)
    elif location_id:
        target = session.get(Location, location_id)
    if target is None or not should_sync(target):
        return

    chore_id = getattr(target, "skylight_chore_id", None)
    if not chore_id:
        log.info(
            "[Garden] Skylight sync: %s %s has sync enabled but no skylight_chore_id; skipping mirror of %s",
            target.__class__.__name__,
            target.id if hasattr(target, "id") else target,
            action,
        )
        return

    # Skylight is unconfigured if EXECUTION_SVC_URL or INTERNAL_SECRET is blank.
    exec_url = config.EXECUTION_SVC_URL
    secret = config.INTERNAL_SECRET
    if not exec_url or not secret:
        log.info(
            "[Garden] Skylight sync: EXECUTION_SVC_URL or INTERNAL_SECRET not configured; cannot mirror %s for %s %s",
            action, target.__class__.__name__, target.id,
        )
        return

    import aiohttp
    import asyncio

    async def _post_complete():
        headers = {"X-Internal-Secret": secret}
        params = {"user": performed_by or ""}
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10.0)
            ) as client:
                async with client.post(
                    f"{exec_url.rstrip('/')}/api/integrations/skylight/chores/{chore_id}/complete",
                    headers=headers,
                    params=params,
                ) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        log.warning(
                            "[Garden] Skylight complete %s for %s %s failed: HTTP %s %s",
                            chore_id, target.__class__.__name__, target.id,
                            resp.status, body[:200],
                        )
                    else:
                        log.info(
                            "[Garden] Skylight sync: completed chore %s for %s %s (action=%s, by=%s)",
                            chore_id, target.__class__.__name__, target.id,
                            action, performed_by or "unknown",
                        )
        except Exception as e:
            log.warning(
                "[Garden] Skylight sync: could not complete chore %s for %s %s: %s",
                chore_id, target.__class__.__name__, target.id, e,
            )

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside a sync FastAPI handler; schedule the coroutine.
            asyncio.ensure_future(_post_complete())
        else:
            asyncio.run(_post_complete())
    except Exception as e:
        log.warning("[Garden] Skylight sync: could not schedule chore completion: %s", e)
