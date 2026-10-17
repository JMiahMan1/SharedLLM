"""The gateway's phone notices (docs/PUSH.md, Senders).

Jobs that finish while nobody is looking (a song, an improved picture), Raven
missions that finish or wait for an admin, and a newly published app build.
Every notice goes out through ``services.common.notify``; the family safety
buttons live in ``family_safety``.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import aiohttp

from services.common import notify
from services.common.http import get_client
from services.gateway.config import ALPACA_AUDIO_URL, REDIS_URL

log = logging.getLogger("gateway.notices")

_TIMEOUT = aiohttp.ClientTimeout(total=20)


@asynccontextmanager
async def _redis(r=None):
    """``r`` when the caller has a connection (the worker), else a short one."""
    if r is not None:
        yield r
        return
    import redis.asyncio as redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


# --------------------------------------------------------------------- songs
#
# A song is written on the audio server and takes minutes. The gateway keeps
# each queued song (and who asked) in PENDING_SONGS_KEY and the worker asks
# the audio server about them every SONG_POLL_SECONDS, so the notice arrives
# even when the page that started it is long closed.

PENDING_SONGS_KEY = "jobs:songs:pending"
SONG_POLL_SECONDS = 20
#: A song still unfinished after this is forgotten (the audio server is gone
#: or lost it); the page tells the person what happened.
SONG_GIVE_UP_S = 2 * 3600
#: How long the audio server keeps a finished song.
SONG_KEEP_MINUTES = 30


async def song_started(job_id: str, user: str, prompt: str, r=None) -> None:
    entry = json.dumps({"user": user, "prompt": prompt[:120], "at": time.time()})
    try:
        async with _redis(r) as conn:
            await conn.hset(PENDING_SONGS_KEY, job_id, entry)
    except Exception as e:  # the song still plays on the page; only the notice is lost
        log.warning(f"[songs] could not remember {job_id}: {type(e).__name__}: {e}")


async def song_forgotten(job_id: str, r=None) -> None:
    try:
        async with _redis(r) as conn:
            await conn.hdel(PENDING_SONGS_KEY, job_id)
    except Exception as e:
        log.warning(f"[songs] could not forget {job_id}: {type(e).__name__}: {e}")


def song_notice(user: str, job_id: str, prompt: str) -> dict[str, Any]:
    about = f'"{prompt[:60]}"' if prompt else "Your song"
    return notify.notice(
        user, "jobs", "Your song is ready",
        f"{about} is ready to play. It expires in {SONG_KEEP_MINUTES} minutes, so save or send it before then.",
        url="/family?tab=create", tag=f"song:{job_id}", once=f"song:{job_id}",
        actions=[notify.view_action("/family?tab=create")],
    )


async def check_songs_once(r, client=None) -> int:
    """Ask the audio server about every pending song; notify the finished
    ones and drop those that failed, were cancelled or are too old."""
    if not ALPACA_AUDIO_URL:
        return 0
    client = client or get_client()
    sent = 0
    for job_id, raw in (await r.hgetall(PENDING_SONGS_KEY) or {}).items():
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            await song_forgotten(job_id, r)
            continue
        try:
            async with client.get(f"{ALPACA_AUDIO_URL}/api/music/jobs/{job_id}", timeout=_TIMEOUT) as resp:
                status = resp.status
                job = await resp.json(content_type=None) if status == 200 else None
        except Exception as e:  # the audio server restarting: ask again next pass
            log.debug(f"[songs] {job_id}: {type(e).__name__}: {e}")
            continue
        state = job.get("status") if isinstance(job, dict) else None
        fresh = time.time() - float(entry.get("at", 0)) < SONG_GIVE_UP_S
        if state == "done":
            if not await notify.send(song_notice(entry.get("user", ""), job_id, entry.get("prompt", "")), client=client):
                continue  # telemetry restarting: tried again, the once key stops a repeat
            sent += 1
        elif fresh and (state in ("queued", "running") or status not in (200, 404)):
            continue  # still being written, or the audio server is having a moment
        # Done, failed, cancelled, gone, or given up on.
        await song_forgotten(job_id, r)
    return sent


# --------------------------------------------------------------- find phone


async def find_phone(user: str) -> bool:
    """Ring the person's phone: the watch's Find my phone button.

    Priority 5 on ``family_safety`` because this is how someone finds a lost
    phone; the Android app answers it with the alarm stream and a Stop action.
    """
    return await notify.send(notify.notice(
        user, "family_safety", "Find my phone", "Your phone is ringing.",
        priority=5, tag="find_phone", kind="find_phone", data={"find_phone": True},
    ))


# ------------------------------------------------------------------ pictures


async def picture_ready(user: str) -> bool:
    """Family > Create's "Make it way better" pass came back with a picture."""
    return await notify.send(notify.notice(
        user, "jobs", "Your picture is ready", "Jarvis finished improving your drawing.",
        url="/family?tab=create", tag="picture", actions=[notify.view_action("/family?tab=create")],
    ))


# -------------------------------------------------------------------- raven


def _summary(result: str, limit: int = 160) -> str:
    text = " ".join(str(result or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def mission_url(mission: dict) -> str:
    workspace = mission.get("workspace_id")
    return f"/workspaces?select={workspace}" if workspace else "/lab?tab=missions"


async def mission_finished(user: str, mission_id: Any, status: str, result: str, workspace_id: str | None) -> bool:
    """Tell the person who started a mission how it ended."""
    if not user:
        return False
    url = mission_url({"workspace_id": workspace_id})
    title = f"Mission #{mission_id} finished" if status == "completed" else f"Mission #{mission_id} failed"
    return await notify.send(notify.notice(
        user, "raven", title, _summary(result) or status, url=url, tag=f"mission:{mission_id}",
        once=f"mission:{mission_id}:{status}", actions=[notify.view_action(url)],
    ))


async def mission_needs_approval(mission: dict) -> bool:
    """A proposed fix waits in Raven Ops: every admin can approve or reject it
    from the notification (callbacks ``raven_approve`` / ``raven_reject``)."""
    mission_id = mission.get("id")
    target = mission.get("target_container") or "a service"
    params = {"mission_id": mission_id}
    return await notify.send(notify.notice(
        "@admins", "raven", f"Raven wants to fix {target}",
        _summary(mission.get("error_summary") or mission.get("proposed_mission") or ""),
        url="/admin/ops", priority=4, tag=f"mission:{mission_id}", once=f"mission:{mission_id}:approval",
        actions=[
            notify.callback_action("Approve", "raven_approve", params),
            notify.callback_action("Reject", "raven_reject", params),
            notify.view_action("/admin/ops"),
        ],
    ))


# -------------------------------------------------------------- app updates

ANNOUNCED_APK_KEY = "app_updates:announced"


def _human_notes(notes: str | None) -> bool:
    """False for the placeholder notes publishers write when nobody wrote any:
    the default, and CI's "Android build <sha>", which means nothing to a reader."""
    return bool(notes) and notes != "Jarvis OS update" and not notes.startswith("Android build ")


async def app_update_published(version_code: int, version_name: str | None, notes: str, r=None) -> int:
    """Tell everyone once about a new APK; returns how many were told."""
    try:
        async with _redis(r) as conn:
            if not await conn.sadd(ANNOUNCED_APK_KEY, str(version_code)):
                return 0  # this build was already announced
    except Exception as e:  # the build is published either way
        log.warning(f"[app-updates] could not record build {version_code}: {type(e).__name__}: {e}")
        return 0
    # The version name only changes on a release bump, so every build in
    # between shares it. The build number is what tells two of them apart and
    # what Settings shows ("Install build 308").
    label = f"{version_name} (build {version_code})" if version_name else f"build {version_code}"
    body = _summary(notes) if _human_notes(notes) else "Open Settings to install it."
    return await notify.send_many(
        await notify.family(),
        lambda user: notify.notice(
            user, "app_updates", f"Jarvis app {label} is available", body, url="/settings",
            tag="app_update", once=f"apk:{version_code}", actions=[notify.view_action("/settings", "Update")]),
    )
