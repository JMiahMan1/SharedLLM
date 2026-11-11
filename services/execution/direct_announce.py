"""Play an announcement on a speaker without Home Assistant.

The normal announce flow renders Kokoro TTS locally (no HA needed) and then
hands the audio to HA: play_media, music_assistant.play_announcement, the TV
integrations, Piper. With HA down every one of those fails, so the audio that
Jarvis already made went nowhere.

Two routes reach a speaker directly, tried in order:

1. **Music Assistant** ``players/play_announcement``: MA is its own server and
   ducks/resumes whatever the speaker was playing, exactly as it does when HA
   relays the same command.
2. **Google Cast**: a Cast speaker/TV found on mDNS by name is told to play
   the audio URL with pychromecast (the same discovery the TV stage uses).

The speaker is matched by the HA entity's name, since that is what callers
know; HA's own entity ids are slugs of the device names on both MA and Cast.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

log = logging.getLogger("execution.direct_announce")

CAST_PLAY_TIMEOUT_S = 10.0


def _cast_keys(entity_id: str) -> set[str]:
    from services.execution.handlers import tv_stage

    object_id = entity_id.split(".", 1)[-1]
    return tv_stage._name_keys(object_id.replace("_", " "))


def find_cast(devices: list[dict[str, Any]], entity_id: str) -> dict[str, Any] | None:
    from services.execution.handlers import tv_stage

    wanted = _cast_keys(entity_id)
    for dev in devices:
        if tv_stage._name_keys(dev.get("name", "")) & wanted and dev.get("host"):
            return dev
    return None


def _cast_play_sync(dev: dict[str, Any], url: str, volume: float | None) -> tuple[bool, str]:
    import pychromecast  # pyright: ignore[reportMissingImports]

    cc = None
    try:
        cc = pychromecast.get_chromecast_from_host(
            (dev["host"], int(dev.get("port") or 8009), None, None, None), timeout=CAST_PLAY_TIMEOUT_S
        )
        cc.wait(timeout=CAST_PLAY_TIMEOUT_S)
        if volume is not None:
            with contextlib.suppress(Exception):
                cc.set_volume(max(0.0, min(1.0, float(volume))))
        mc = cc.media_controller
        mc.play_media(url, "audio/wav")
        mc.block_until_active(timeout=CAST_PLAY_TIMEOUT_S)
        deadline = time.time() + CAST_PLAY_TIMEOUT_S
        while time.time() < deadline:
            state = getattr(mc.status, "player_state", "") or ""
            if state in ("PLAYING", "BUFFERING"):
                return True, f"Playing on {cc.name or dev.get('name') or 'the speaker'}"
            time.sleep(0.3)
        return False, "the Cast device did not start playing"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if cc is not None:
            with contextlib.suppress(Exception):
                cc.disconnect(timeout=2)


async def announce_direct(entity_id: str, media_url: str, volume: float | None, ctx: Any) -> dict[str, Any]:
    """Play ``media_url`` on the speaker behind ``entity_id``; {"ok", "route", "error"}."""
    from services.execution.handlers import mass_client, tv_stage

    errors: list[str] = []
    mass_url, mass_token = getattr(ctx, "mass_url", None), getattr(ctx, "mass_token", None)
    if mass_url and mass_token:
        try:
            player = mass_client.match_player(await mass_client.get_players(mass_url, mass_token), entity_id)
            if player:
                level = None if volume is None else round(float(volume) * 100)
                await mass_client.play_announcement(mass_url, mass_token, player["player_id"], media_url, level)
                name = player.get("display_name") or player.get("name") or entity_id
                log.info(f"[announce/direct] {entity_id} -> Music Assistant player {player['player_id']}")
                return {"ok": True, "route": "mass_direct", "target": name}
            errors.append("no Music Assistant player matches it")
        except mass_client.MACommandError as e:
            errors.append(f"Music Assistant: {e}")
    else:
        errors.append("no Music Assistant login")

    dev = find_cast(await tv_stage.discover_cast_devices(), entity_id)
    if dev:
        ok, detail = await asyncio.to_thread(_cast_play_sync, dev, media_url, volume)
        if ok:
            log.info(f"[announce/direct] {entity_id} -> Cast {dev.get('name')} at {dev.get('host')}")
            return {"ok": True, "route": "cast_direct", "target": dev.get("name") or entity_id}
        errors.append(f"Cast: {detail}")
    else:
        errors.append("no Cast device with that name answered")
    return {"ok": False, "route": "none", "error": "; ".join(errors)}
