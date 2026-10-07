"""A paired watch's glance line: who is driving home, and how long they are out.

Every couple of minutes, for each ESPHome device that has an owner and a
``set_glance`` action (the Jarvis watch), find family members the owner may
see who are on the move and ask geo for their drive time home (OSRM, subject
to sharing consent), e.g. "Michele · home 18m". The line is pushed only when
it changes -- each push wakes the watch's radio -- and cleared once nobody is
driving.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import aiohttp

log = logging.getLogger("execution.watch_glance")

INTERVAL_S = 120
#: m/s: moving fast enough to be driving.
MOVING_MPS = 4.0
#: A fix older than this is not "on the move now".
FRESH_S = 300
GLANCE_ACTION = "set_glance"
MAX_LEN = 60

_last_pushed: dict[str, str] = {}


def glance_line(etas: list[tuple[str, dict]]) -> str:
    """'Michele · home 18m' (two people at most) from (name, eta) pairs."""
    parts = []
    for name, eta in etas[:2]:
        if eta.get("arrived"):
            continue
        mins = max(1, round(float(eta.get("duration_s", 0)) / 60))
        when = f"{mins}m" if mins < 60 else f"{mins // 60}h{mins % 60:02d}"
        parts.append(f"{name.title()} · {(eta.get('to') or 'home').lower()} {when}")
    return "  ".join(parts)[:MAX_LEN]


async def _json(session, method: str, url: str, **kw):
    async with session.request(method, url, timeout=aiohttp.ClientTimeout(total=10), **kw) as resp:
        return resp.status, await resp.json(content_type=None)


async def line_for(owner: str, session) -> str:
    """The glance line for one owner, from everyone's latest position."""
    from services.config import GEO_SVC_URL, IDENTITY_SVC_URL, INTERNAL_SECRET

    hdr = {"X-Internal-Secret": INTERNAL_SECRET}
    status, locations = await _json(session, "GET", f"{IDENTITY_SVC_URL.rstrip('/')}/api/users/location/all", headers=hdr)
    if status != 200 or not isinstance(locations, dict):
        return ""
    now = time.time()
    etas = []
    for person, loc in sorted(locations.items()):
        if person.lower() == owner.lower() or not isinstance(loc, dict):
            continue
        stamp = float(loc.get("updated_at") or loc.get("timestamp") or 0)
        if now - stamp > FRESH_S or float(loc.get("speed") or 0) < MOVING_MPS:
            continue
        # As the owner: someone who does not share with them is a 404 here.
        status, eta = await _json(session, "GET", f"{GEO_SVC_URL.rstrip('/')}/people/{person}/eta",
                                  headers=hdr, params={"to": "home", "viewer": owner, "is_admin": "false"})
        if status == 200 and isinstance(eta, dict):
            etas.append((person, eta))
    return glance_line(etas)


async def push_glances() -> None:
    from services.execution import esphome_client

    devices = await esphome_client._device_list()
    async with aiohttp.ClientSession() as session:
        for d in devices:
            owner = (d.get("owner") or "").strip()
            name = d.get("name")
            if not owner or not name or not d.get("host"):
                continue
            try:
                line = await line_for(owner, session)
            except Exception as e:
                log.info(f"[glance] no line for {owner}: {e}")
                continue
            if _last_pushed.get(name) == line:
                continue

            async def op(client, line=line):
                _entities, services = await client.list_entities_services()
                if not any(s.name == GLANCE_ACTION for s in services):
                    return False
                await esphome_client._respond(client, services, GLANCE_ACTION, {"line": line})
                return True

            try:
                if await esphome_client._with_connection({**d, "port": d.get("port") or 6053}, op):
                    _last_pushed[name] = line
                    log.info(f"[glance] {name}: {line or '(cleared)'}")
            except Exception as e:
                # Asleep (deep sleep between wakes) or out of range: next round.
                log.debug(f"[glance] {name} unreachable: {e}")


async def glance_loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        with contextlib.suppress(Exception):
            await push_glances()
