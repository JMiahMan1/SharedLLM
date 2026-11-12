"""The household's time zone: one resolver, one documented order.

Every service that turns an instant into "today", "7 AM" or "quiet hours"
asks this module, so one setting governs Jarvis and the hub. The order
(docs/CONFIG.md, "Time zone"):

1. The household time zone setting (Identity global setting ``timezone``),
   set by an admin in Settings > Household time zone.
2. The ``TZ`` environment variable from ``.env`` (the older ``TIMEZONE``
   variable is read as an alias when ``TZ`` is unset).
3. Derived from the household's home location (the ``weather_location``
   setting, or Home Assistant's ``zone.home`` when the caller passes it), only
   when the admin has turned on ``timezone_from_location``. Offline lookup
   with ``timezonefinder``.
4. Otherwise :class:`HouseholdTimeZoneError`, whose message names all three.

There is deliberately no guessed default (no UTC): a wrong zone moves every
schedule, sunrise, energy period and reminder by hours without a word.

Per-user and per-device zones (a phone's reported zone, a person's own
``timezone``) are resolved by their callers; when one is missing they fall
back to this zone and should say so.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("household_tz")

SETTING_ZONE = "timezone"
SETTING_DERIVE = "timezone_from_location"
SETTING_LOCATION = "weather_location"

SOURCE_SETTING = "household setting"
SOURCE_ENV = "TZ in .env"
SOURCE_LOCATION = "home location"

HOW_TO_SET = (
    "Set the household time zone in Settings > Household time zone, "
    "or TZ in the server's .env (e.g. TZ=America/Denver), "
    "or turn on 'Derive from home location' with a home location set "
    "(Admin > Weather reminders or Home Assistant's zone.home). See docs/CONFIG.md, 'Time zone'."
)


class HouseholdTimeZoneError(RuntimeError):
    """No usable household time zone; the message says how to set one."""


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def zone_from_location(lat: float, lon: float) -> str | None:
    """The IANA zone at a point, offline (timezonefinder)."""
    try:
        from timezonefinder import TimezoneFinder
    except ImportError as exc:
        raise HouseholdTimeZoneError(
            "'Derive from home location' is on, but this service has no timezonefinder "
            "package to look the zone up. " + HOW_TO_SET
        ) from exc
    return TimezoneFinder().timezone_at(lat=float(lat), lng=float(lon))


def _parse_location(raw: Any) -> tuple[float, float] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            return None
    if not isinstance(raw, Mapping):
        return None
    try:
        lat, lon = float(raw.get("latitude")), float(raw.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def resolve(
    settings: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    home: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """``(zone, source)`` by the documented order, or HouseholdTimeZoneError.

    ``settings`` are Identity's global settings (None when the caller could not
    read them); ``home`` is an optional ``{"latitude", "longitude"}`` for the
    home location when the caller knows it (e.g. Home Assistant's zone.home).
    """
    settings = settings or {}
    env = os.environ if env is None else env
    problems: list[str] = []

    zone = str(settings.get(SETTING_ZONE) or "").strip()
    if zone:
        if valid_zone(zone):
            return zone, SOURCE_SETTING
        problems.append(f"the household setting {zone!r} is not a valid IANA time zone")

    zone = str(env.get("TZ") or env.get("TIMEZONE") or "").strip()
    if zone:
        if valid_zone(zone):
            return zone, SOURCE_ENV
        problems.append(f"TZ {zone!r} is not a valid IANA time zone")

    if _truthy(settings.get(SETTING_DERIVE)):
        point = _parse_location(settings.get(SETTING_LOCATION)) or _parse_location(home)
        if point is None:
            problems.append("'Derive from home location' is on but no home location is set")
        else:
            zone = zone_from_location(*point) or ""
            if zone and valid_zone(zone):
                return zone, SOURCE_LOCATION
            problems.append(f"no time zone is known at the home location {point}")

    detail = ("; ".join(problems) + ". ") if problems else ""
    raise HouseholdTimeZoneError(f"No household time zone is configured: {detail}{HOW_TO_SET}")


# --- Per-process cache --------------------------------------------------------
#
# Services resolve once at startup (refresh) and read the result synchronously
# afterwards (current). An admin's change applies on the next refresh, which
# services run on restart; the gateway also refreshes when the setting is saved.

_current: tuple[str, str] | None = None
_logged: set[str] = set()


def _remember(result: tuple[str, str], service: str | None) -> tuple[str, str]:
    global _current
    _current = result
    key = f"{service}:{result}"
    if service and key not in _logged:
        _logged.add(key)
        log.info("%s: time zone %s (from %s)", service, result[0], result[1])
    return result


def fetch_identity_settings(identity_url: str, internal_secret: str, timeout: float = 5.0) -> dict[str, str]:
    """Identity's global settings (blocking; stdlib only, so every image can use it)."""
    req = urllib.request.Request(
        f"{identity_url.rstrip('/')}/api/settings",
        headers={"X-Internal-Secret": internal_secret or ""},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (internal URL)
        rows = json.loads(resp.read().decode() or "[]")
    return {str(r.get("key")): r.get("value") for r in rows if isinstance(r, dict)}


def refresh_sync(
    service: str,
    fetch: Callable[[], Mapping[str, Any]] | None = None,
    home: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Resolve now (settings from ``fetch`` when given), cache, and log the source once.

    Settings that cannot be read are reported, and resolution continues with
    the next step of the order (TZ), which the source in the log makes visible.
    """
    settings: Mapping[str, Any] | None = None
    if fetch is not None:
        try:
            settings = fetch()
        except Exception as exc:
            log.warning("%s: could not read the household time zone setting (%s); trying TZ", service, exc)
    return _remember(resolve(settings, home=home), service)


async def refresh(
    service: str,
    fetch: Callable[[], Awaitable[Mapping[str, Any]]] | None = None,
    home: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Async twin of :func:`refresh_sync`."""
    settings: Mapping[str, Any] | None = None
    if fetch is not None:
        try:
            settings = await fetch()
        except Exception as exc:
            log.warning("%s: could not read the household time zone setting (%s); trying TZ", service, exc)
    return _remember(resolve(settings, home=home), service)


def current() -> tuple[str, str]:
    """The last resolved ``(zone, source)``; resolves from TZ if never refreshed."""
    if _current is not None:
        return _current
    # Not refreshed in this process (a script, a test, a service that has no
    # settings access): resolve from the environment each time, uncached.
    return resolve(None)


def zone_name() -> str:
    return current()[0]


def zoneinfo() -> ZoneInfo:
    return ZoneInfo(zone_name())


def reset_cache() -> None:
    """Tests only: forget the cached zone."""
    global _current
    _current = None
    _logged.clear()


async def startup(service: str, *, required: bool = False) -> tuple[str, str] | None:
    """Resolve at service startup from Identity's settings and log the source once.

    ``required=False`` logs the descriptive error and returns None, so features
    that need the zone fail at use with the same message; ``required=True``
    raises it and the service does not start.
    """
    import asyncio

    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    def fetch() -> Mapping[str, Any]:
        return fetch_identity_settings(IDENTITY_SVC_URL, INTERNAL_SECRET or "")

    try:
        return await asyncio.to_thread(refresh_sync, service, fetch)
    except HouseholdTimeZoneError as exc:
        if required:
            raise
        log.error("%s: %s", service, exc)
        return None
