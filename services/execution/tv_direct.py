"""TV and speaker remote control without Home Assistant (HA fallback, step 3).

``handle_media_transport`` drives every TV through HA: ``media_player.*``,
``remote.send_command``, ``webostv.command``, the androidtv services. With HA
down the remote page and "pause the TV" stop working, although Jarvis already
holds what it needs to reach most TVs itself: the device registry (IP, MAC,
integration), the LG pairing key and Samsung token the TV stage stores, and
mDNS for Cast devices.

``transport(entity_id, command, ...)`` finds the device and speaks its own
protocol:

* **Roku** - ECP over HTTP on port 8060 (no pairing at all).
* **LG webOS** - aiowebostv with the stored client key (no key: the TV would
  have to show its "Allow" prompt, which is reported rather than attempted).
* **Samsung Tizen** - samsungtvws with the stored token.
* **Google Cast** (Chromecast, Google TV, Cast speakers) - pychromecast media
  and volume control.
* **Music Assistant** players - ``players/cmd_*`` for queue commands.
* **Power on** - Wake-on-LAN to the registered MAC for any of them.

Nothing here ever guesses an IP: a device Jarvis has not seen is reported as
unreachable with the reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import Any

import aiohttp

log = logging.getLogger("execution.tv_direct")

ROKU_KEYS = {
    "home": "Home", "back": "Back", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "select": "Select", "enter": "Select", "ok": "Select", "info": "Info", "menu": "Info",
    "replay": "InstantReplay", "play": "Play", "pause": "Play", "resume": "Play",
    "next": "Fwd", "previous": "Rev", "volume_up": "VolumeUp", "volume_down": "VolumeDown",
    "mute": "VolumeMute", "volume_mute": "VolumeMute", "power_off": "PowerOff", "power_on": "PowerOn",
}
SAMSUNG_KEYS = {
    "home": "KEY_HOME", "back": "KEY_RETURN", "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT",
    "right": "KEY_RIGHT", "select": "KEY_ENTER", "enter": "KEY_ENTER", "ok": "KEY_ENTER", "info": "KEY_INFO",
    "menu": "KEY_MENU", "play": "KEY_PLAY", "resume": "KEY_PLAY", "pause": "KEY_PAUSE", "stop": "KEY_STOP",
    "next": "KEY_FF", "previous": "KEY_REWIND", "volume_up": "KEY_VOLUP", "volume_down": "KEY_VOLDOWN",
    "mute": "KEY_MUTE", "volume_mute": "KEY_MUTE", "power_off": "KEY_POWER",
}
WEBOS_BUTTONS = {
    "home": "HOME", "back": "BACK", "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "select": "ENTER", "enter": "ENTER", "ok": "ENTER", "info": "INFO", "menu": "MENU",
}
#: Commands a webOS TV takes as API calls rather than as a remote button.
WEBOS_CALLS = {
    "play": "play", "resume": "play", "pause": "pause", "stop": "stop", "next": "fast_forward",
    "previous": "rewind", "volume_up": "volume_up", "volume_down": "volume_down", "power_off": "power_off",
}
MA_COMMANDS = {
    "play": "players/cmd_play", "resume": "players/cmd_play", "pause": "players/cmd_pause",
    "stop": "players/cmd_stop", "next": "players/cmd_next", "previous": "players/cmd_previous",
    "power_off": "players/cmd_power", "power_on": "players/cmd_power",
}


def _ok(message: str, route: str) -> dict[str, Any]:
    return {"ok": True, "message": message, "route": route}


def _fail(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message, "route": "none"}


def platform_of(record: dict[str, Any] | None) -> str:
    """roku / webos / samsung / cast / android_tv / unknown, from the registry row."""
    text = " ".join(str((record or {}).get(k) or "") for k in ("integration", "metadata", "hostname")).lower()
    for needle, platform in (
        ("roku", "roku"), ("webos", "webos"), ("lg", "webos"), ("samsung", "samsung"), ("tizen", "samsung"),
        ("cast", "cast"), ("chromecast", "cast"), ("androidtv", "android_tv"), ("android_tv", "android_tv"),
    ):
        if re.search(rf"\b{needle}", text):
            return platform
    return "unknown"


async def locate(entity_id: str) -> dict[str, Any]:
    """Everything Jarvis knows about reaching this device without HA."""
    from services.execution import device_registry
    from services.execution.handlers import tv_stage

    record: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        record = await device_registry.get_device(entity_id) or {}
    platform = platform_of(record)
    webos = (tv_stage._webos_store().get(entity_id) or {}) if platform in ("webos", "unknown") else {}
    samsung_ip = tv_stage._samsung_hosts().get(entity_id, "") if platform in ("samsung", "unknown") else ""
    if platform == "unknown":
        platform = "webos" if webos.get("client_key") else "samsung" if samsung_ip else "unknown"
    ip = (
        webos.get("host") if platform == "webos" else samsung_ip if platform == "samsung" else ""
    ) or (record.get("ip") if not record.get("ip_stale") else "") or ""
    return {
        "entity_id": entity_id,
        "platform": platform,
        "ip": ip,
        "mac": record.get("mac") or "",
        "name": record.get("friendly_name") or entity_id.split(".", 1)[-1].replace("_", " "),
        "webos_key": webos.get("client_key") or "",
    }


def wake_on_lan(mac: str) -> bool:
    try:
        from wakeonlan import send_magic_packet  # pyright: ignore[reportMissingImports]

        send_magic_packet(mac)
        return True
    except Exception as e:
        log.warning(f"[tv_direct] Wake-on-LAN to {mac} failed: {e}")
        return False


async def _roku(dev: dict[str, Any], command: str) -> dict[str, Any]:
    key = ROKU_KEYS.get(command)
    if not key:
        return _fail(f"A Roku has no direct key for '{command}'.")
    try:
        async with aiohttp.ClientSession() as s, s.post(
            f"http://{dev['ip']}:8060/keypress/{key}", timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            if resp.status >= 400:
                return _fail(f"The Roku answered HTTP {resp.status} to {key}.")
    except Exception as e:
        return _fail(f"The Roku at {dev['ip']} did not answer ({type(e).__name__}).")
    return _ok(f"Sent {key} to {dev['name']} directly.", "roku_ecp")


async def _webos(dev: dict[str, Any], command: str, volume_level: float | None, muted: bool | None) -> dict[str, Any]:
    if not dev["webos_key"]:
        return _fail(
            f"{dev['name']} has never been paired with Jarvis directly, so it cannot be controlled while "
            "Home Assistant is down (casting a game to it once pairs it)."
        )
    from aiowebostv import WebOsClient  # pyright: ignore[reportMissingImports]

    client = WebOsClient(dev["ip"], client_key=dev["webos_key"], connect_timeout=5)
    try:
        await asyncio.wait_for(client.connect(), 10)
        if command in WEBOS_BUTTONS:
            await client.button(WEBOS_BUTTONS[command])
        elif command == "volume_set" and volume_level is not None:
            await client.set_volume(round(volume_level * 100))
        elif command in ("mute", "volume_mute"):
            await client.set_mute(True if muted is None else bool(muted))
        elif command in WEBOS_CALLS:
            await getattr(client, WEBOS_CALLS[command])()
        else:
            return _fail(f"An LG TV has no direct command for '{command}'.")
    except Exception as e:
        return _fail(f"{dev['name']} did not take the command directly ({type(e).__name__}: {e}).")
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
    return _ok(f"Sent {command} to {dev['name']} directly.", "webos_direct")


def _samsung_sync(dev: dict[str, Any], key: str) -> None:
    from samsungtvws import SamsungTVWS  # pyright: ignore[reportMissingImports]

    from services.execution.handlers import tv_stage

    token_file = str(tv_stage.TOKEN_DIR / f"samsung_{re.sub(r'[^0-9A-Za-z.]', '_', dev['entity_id'])}.token")
    tv = SamsungTVWS(host=dev["ip"], port=8002, token_file=token_file, name="Jarvis", timeout=8)
    try:
        tv.send_key(key)
    finally:
        with contextlib.suppress(Exception):
            tv.close()


async def _samsung(dev: dict[str, Any], command: str) -> dict[str, Any]:
    key = SAMSUNG_KEYS.get(command)
    if not key:
        return _fail(f"A Samsung TV has no direct key for '{command}'.")
    try:
        await asyncio.wait_for(asyncio.to_thread(_samsung_sync, dev, key), 15)
    except Exception as e:
        return _fail(f"{dev['name']} did not take {key} directly ({type(e).__name__}).")
    return _ok(f"Sent {key} to {dev['name']} directly.", "samsung_direct")


def _cast_sync(cast_dev: dict[str, Any], command: str, volume_level: float | None, muted: bool | None) -> str:
    import pychromecast  # pyright: ignore[reportMissingImports]

    cc = pychromecast.get_chromecast_from_host(
        (cast_dev["host"], int(cast_dev.get("port") or 8009), None, None, None), timeout=8
    )
    try:
        cc.wait(timeout=8)
        mc = cc.media_controller
        if command in ("play", "resume"):
            mc.play()
        elif command == "pause":
            mc.pause()
        elif command == "stop":
            mc.stop()
        elif command == "next":
            mc.queue_next()
        elif command == "previous":
            mc.queue_prev()
        elif command == "volume_up":
            cc.volume_up()
        elif command == "volume_down":
            cc.volume_down()
        elif command == "volume_set" and volume_level is not None:
            cc.set_volume(max(0.0, min(1.0, float(volume_level))))
        elif command in ("mute", "volume_mute"):
            cc.set_volume_muted(True if muted is None else bool(muted))
        elif command == "power_off":
            cc.quit_app()
        else:
            return f"A Cast device has no direct command for '{command}'."
        return ""
    finally:
        with contextlib.suppress(Exception):
            cc.disconnect(timeout=2)


async def _cast(dev: dict[str, Any], command: str, volume_level: float | None, muted: bool | None) -> dict[str, Any]:
    from services.execution.direct_announce import find_cast
    from services.execution.handlers import tv_stage

    cast_dev = find_cast(await tv_stage.discover_cast_devices(), dev["entity_id"])
    if cast_dev is None:
        return _fail(f"No Cast device named like {dev['name']} answered on the network.")
    try:
        problem = await asyncio.wait_for(asyncio.to_thread(_cast_sync, cast_dev, command, volume_level, muted), 20)
    except Exception as e:
        return _fail(f"{dev['name']} did not take the command over Cast ({type(e).__name__}).")
    return _fail(problem) if problem else _ok(f"Sent {command} to {cast_dev.get('name')} over Cast.", "cast_direct")


async def _music_assistant(entity_id: str, command: str, ctx: Any) -> dict[str, Any] | None:
    """Queue commands for a speaker MA knows; None when MA cannot take this one."""
    from services.execution.handlers import mass_client

    ma_command = MA_COMMANDS.get(command)
    url, token = getattr(ctx, "mass_url", None), getattr(ctx, "mass_token", None)
    if not (ma_command and url and token):
        return None
    try:
        player = mass_client.match_player(await mass_client.get_players(url, token), entity_id)
        if player is None:
            return None
        args: dict[str, Any] = {"player_id": player["player_id"]}
        if ma_command == "players/cmd_power":
            args["powered"] = command == "power_on"
        await mass_client.ma_command(url, token, ma_command, args)
    except mass_client.MACommandError as e:
        return _fail(f"Music Assistant did not take {command}: {e}")
    return _ok(f"Sent {command} to {player.get('display_name') or entity_id} through Music Assistant.", "mass_direct")


async def transport(
    entity_id: str, command: str, ctx: Any, *, volume_level: float | None = None, muted: bool | None = None
) -> dict[str, Any]:
    """Run one transport/remote command on ``entity_id`` without HA. {"ok", "message", "route"}."""
    command = command.lower()
    dev = await locate(entity_id)
    log.info(f"[tv_direct] {command} -> {entity_id} ({dev['platform']}, ip={dev['ip'] or '?'})")

    if command == "power_on" and dev["platform"] != "roku":
        if dev["mac"] and wake_on_lan(dev["mac"]):
            return _ok(f"Sent Wake-on-LAN to {dev['name']}.", "wake_on_lan")
        if dev["platform"] == "unknown":
            ma = await _music_assistant(entity_id, command, ctx)
            if ma is not None:
                return ma
        return _fail(f"Jarvis has no MAC address for {dev['name']}, so it cannot be woken without Home Assistant.")

    if dev["platform"] == "roku" and dev["ip"]:
        return await _roku(dev, command)
    if dev["platform"] == "webos" and dev["ip"]:
        return await _webos(dev, command, volume_level, muted)
    if dev["platform"] == "samsung" and dev["ip"]:
        return await _samsung(dev, command)

    # Speakers and anything else: Music Assistant knows queue commands for its
    # players, and Cast reaches Chromecasts / Google TVs (and Android TVs with
    # Cast built in; their ADB route needs keys Jarvis does not hold).
    ma = await _music_assistant(entity_id, command, ctx)
    if ma is not None and ma["ok"]:
        return ma
    cast = await _cast(dev, command, volume_level, muted)
    if cast["ok"] or ma is None:
        return cast
    return _fail(f"{ma['message']}; {cast['message']}")
