"""Opt-in Skylight chore mirror for Jarvis Garden (Phase 2).

When a plant or location has ``skylight_sync_enabled = true`` and a
``skylight_chore_id`` is set, care actions logged through
``POST /plants/{id}/care`` are mirrored to Skylight as chore completions
via Execution's ``/api/integrations/skylight/chores/{chore_id}/complete``.
Opt-in per plant/location. Best-effort: never fails the care-log request.
"""
from __future__ import annotations

import asyncio
import logging
import threading

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
    """Mirror a *watering* to Skylight if the plant/location opted in.

    Only ``water``: the chore on the chart is the watering, so completing it for
    a mist, a rotation or a pest check would tick off a job nobody did. The id
    and kind are read here, while the row is still attached, and only primitives
    travel into the async work -- the old code closed over the ORM object and hit
    ``DetachedInstanceError`` once the request's session ended.
    """
    if action != "water":
        return

    target = None
    if plant_id:
        target = session.get(Plant, plant_id)
    elif location_id:
        target = session.get(Location, location_id)
    if target is None or not should_sync(target):
        return

    target_kind = target.__class__.__name__
    target_id = target.id
    chore_id = getattr(target, "skylight_chore_id", None)
    if not chore_id:
        log.info(
            "[Garden] Skylight sync: %s %s has sync enabled but no skylight_chore_id; skipping mirror of %s",
            target_kind, target_id, action,
        )
        return

    # Skylight is unconfigured if EXECUTION_SVC_URL or INTERNAL_SECRET is blank.
    exec_url = config.EXECUTION_SVC_URL
    secret = config.INTERNAL_SECRET
    if not exec_url or not secret:
        log.info(
            "[Garden] Skylight sync: EXECUTION_SVC_URL or INTERNAL_SECRET not configured; cannot mirror %s for %s %s",
            action, target_kind, target_id,
        )
        return

    import aiohttp

    async def _post_complete() -> None:
        headers = {"X-Internal-Secret": secret}
        # origin=garden: Execution must not report this completion back to
        # Garden's hook, which would log the same watering a second time.
        params = {"user": performed_by or "", "origin": "garden"}
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
                            chore_id, target_kind, target_id, resp.status, body[:200],
                        )
                    else:
                        log.info(
                            "[Garden] Skylight sync: completed chore %s for %s %s (action=%s, by=%s)",
                            chore_id, target_kind, target_id, action, performed_by or "unknown",
                        )
        except Exception as e:
            log.warning(
                "[Garden] Skylight sync: could not complete chore %s for %s %s: %s",
                chore_id, target_kind, target_id, e,
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # A running loop that is not ours to block on: let it finish in the
        # background (this is the async-handler case).
        loop.create_task(_post_complete())
        return

    # A plain ``def`` FastAPI handler runs in a worker thread with no loop, and
    # ``asyncio.get_event_loop()`` there made a loop that never ran the task --
    # which is why the mirror silently never happened for POST /plants/{id}/care.
    def _run() -> None:
        try:
            asyncio.run(_post_complete())
        except Exception as e:  # pragma: no cover - background best effort
            log.warning("[Garden] Skylight sync: background completion failed: %s", e)

    threading.Thread(target=_run, name=f"skylight-mirror-{target_id}", daemon=True).start()
