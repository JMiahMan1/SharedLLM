"""Weather events: one stream every service can read, hooks, and rule actions.

Anything that happens to the household's weather is published once, here, as
an event, and reaches other services three ways:

1. **The Redis stream ``weather:events``** (capped at ``STREAM_MAXLEN``). Any
   service with Redis reads it with ``XREAD``/``XREADGROUP`` (or
   ``services.common.weather_client.read_events``); nothing has to be
   registered, so a new service just starts reading.
2. **Hooks** (the ``weather_hooks`` setting): a JSON list of

       {"id": "barn", "events": ["alert.started"], "service": "geo",
        "path": "/api/hooks/weather", "method": "POST",
        "filter": {"kinds": ["rain"], "rules": ["hay"]}}

   Each matching event is POSTed (or PUT/GET) as ``{"event", "data", "ts"}``
   with ``X-Internal-Secret``. ``service`` must be one this deployment knows
   (``known_services``: every ``*_SVC_URL`` in ``services/config.py``), so a
   hook can never reach an arbitrary host and a service added to the config
   later is a valid target with no change here.
3. **Rule actions**: a weather reminder may also run a Home Assistant service
   (close the barn door when rain is coming) or call a service directly.

Events (``EVENTS``):

- ``forecast.updated``: the home forecast was refreshed from Open-Meteo.
- ``condition.changed``: the current condition at home changed (clear -> rain).
- ``alert.started``: a reminder rule began a new episode (the moment its notice goes out).
- ``day.completed``: a local day finished; its weather and every tracked metric.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import aiohttp

log = logging.getLogger("execution.weather_bus")

STREAM = "weather:events"
STREAM_MAXLEN = 2000
EVENTS = ("forecast.updated", "condition.changed", "alert.started", "day.completed")
SETTING_HOOKS = "weather_hooks"
_PATH = re.compile(r"^/[A-Za-z0-9._~/%-]{0,200}$")
_HA_SERVICE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_TIMEOUT = aiohttp.ClientTimeout(total=10)
_METHODS = {"GET", "POST", "PUT"}


def known_services() -> dict[str, str]:
    """Internal services a hook or action may call, by name.

    Read from ``services.config`` each time: every ``<NAME>_SVC_URL`` (and the
    control plane) that has a value, so a future service is a valid target as
    soon as its URL is configured.
    """
    from services import config

    out = {}
    for attr in dir(config):
        if attr.endswith("_SVC_URL"):
            url = getattr(config, attr, None)
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                out[attr[: -len("_SVC_URL")].lower()] = url.rstrip("/")
    cp = getattr(config, "CONTROL_PLANE_URL", None)
    if isinstance(cp, str) and cp.startswith(("http://", "https://")):
        out["control_plane"] = cp.rstrip("/")
    return out


def _clean_target(item: dict) -> dict | None:
    service = str(item.get("service") or "").strip().lower()
    path = str(item.get("path") or "").strip()
    method = str(item.get("method") or "POST").strip().upper()
    if not service or not _PATH.match(path) or ".." in path or method not in _METHODS:
        return None
    return {"service": service, "path": path, "method": method}


def parse_hooks(raw: str | None) -> list[dict]:
    """The ``weather_hooks`` setting as clean hooks; malformed ones are dropped."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        log.warning(f"[weather] {SETTING_HOOKS} is not valid JSON; no weather hooks")
        return []
    out = []
    for i, item in enumerate(data if isinstance(data, list) else []):
        if not isinstance(item, dict):
            continue
        target = _clean_target(item)
        events = [e for e in (item.get("events") or []) if e in EVENTS]
        if not target or not events:
            log.warning(f"[weather] ignoring hook {item!r}")
            continue
        flt = item.get("filter") if isinstance(item.get("filter"), dict) else {}
        out.append({
            "id": str(item.get("id") or f"hook{i}")[:40],
            "events": events,
            **target,
            "filter": {
                "kinds": [str(k) for k in flt.get("kinds") or []],
                "rules": [str(r) for r in flt.get("rules") or []],
            },
            "enabled": item.get("enabled", True) is not False,
        })
    return out


def parse_actions(raw: Any) -> list[dict]:
    """A rule's ``actions`` as clean actions (``ha_service`` or ``call``)."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "ha_service":
            service = str(item.get("service") or "").strip().lower()
            entity = str(item.get("entity_id") or "").strip()
            if not _HA_SERVICE.match(service) or (entity and "." not in entity):
                continue
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            out.append({"type": "ha_service", "service": service, "entity_id": entity, "data": data})
        elif kind == "call":
            target = _clean_target(item)
            if target:
                out.append({"type": "call", **target})
    return out


def matches(hook: dict, event: str, data: dict) -> bool:
    if not hook.get("enabled", True) or event not in hook["events"]:
        return False
    flt = hook.get("filter") or {}
    if flt.get("kinds") and data.get("kind") not in flt["kinds"]:
        return False
    return not (flt.get("rules") and data.get("rule") not in flt["rules"])


async def call_service(target: dict, body: dict, services: dict[str, str] | None = None) -> bool:
    """One call to a known internal service; False (logged) on any failure."""
    from services.common.http import get_client
    from services.config import INTERNAL_SECRET

    base = (services if services is not None else known_services()).get(target["service"])
    if not base:
        log.warning(f"[weather] unknown service {target['service']!r}; known: {sorted(known_services())}")
        return False
    kw: dict[str, Any] = {"headers": {"X-Internal-Secret": INTERNAL_SECRET}, "timeout": _TIMEOUT}
    if target["method"] == "GET":
        kw["params"] = {"event": body.get("event", "")}
    else:
        kw["json"] = body
    try:
        async with get_client().request(target["method"], f"{base}{target['path']}", **kw) as resp:
            if resp.status < 300:
                return True
            log.warning(f"[weather] {target['service']}{target['path']} answered HTTP {resp.status}")
    except Exception as e:
        log.warning(f"[weather] {target['service']}{target['path']} failed: {type(e).__name__}: {e}")
    return False


async def publish(event: str, data: dict, *, redis=None, hooks: list[dict] | None = None) -> str | None:
    """Add one event to the stream and deliver it to matching hooks.

    Returns the stream id (None when Redis refused; the hooks still run).
    """
    from services.execution.weather import _r

    ts = time.time()
    r = redis or await _r()
    stream_id = None
    try:
        stream_id = await r.xadd(STREAM, {"type": event, "data": json.dumps(data, default=str), "ts": str(ts)},
                                 maxlen=STREAM_MAXLEN, approximate=True)
    except Exception as e:
        log.warning(f"[weather] could not publish {event}: {type(e).__name__}: {e}")
    if hooks is None:
        from services.execution.weather import settings

        hooks = parse_hooks((await settings()).get(SETTING_HOOKS))
    body = {"event": event, "data": data, "ts": ts, "id": stream_id}
    for hook in hooks:
        if matches(hook, event, data):
            await call_service(hook, body)
    return stream_id


async def run_actions(actions: list[dict], event: dict) -> int:
    """Run a rule's actions for one ``alert.started`` event; how many succeeded."""
    done = 0
    for action in actions:
        if action["type"] == "call":
            done += await call_service(action, {"event": "alert.started", "data": event, "ts": time.time()})
            continue
        from services.execution import ha_client
        from services.execution.main import resolve_first_user

        creds = await resolve_first_user() or {}
        if not creds.get("ha_url") or not creds.get("ha_token"):
            log.warning("[weather] no Home Assistant login for the system account; action skipped")
            continue
        domain, service = action["service"].split(".", 1)
        result = await ha_client.call_service(creds["ha_url"], creds["ha_token"], domain, service,
                                              action["entity_id"], service_data=action.get("data") or None)
        if (result or {}).get("ok"):
            done += 1
            log.info(f"[weather] {event.get('rule')}: ran {action['service']} {action['entity_id']}")
        else:
            log.warning(f"[weather] action {action['service']} failed: {(result or {}).get('error')}")
    return done


async def read(after: str = "-", count: int = 100, *, redis=None) -> list[dict]:
    """Events after ``after`` (a stream id; "-" from the start), oldest first."""
    from services.execution.weather import _r

    r = redis or await _r()
    start = "-" if after in ("", "-", "0") else f"({after}"
    rows = await r.xrange(STREAM, min=start, max="+", count=max(1, min(count, 500)))
    out = []
    for sid, fields in rows:
        try:
            data = json.loads(fields.get("data") or "{}")
        except ValueError:
            data = {}
        out.append({"id": sid, "type": fields.get("type"), "data": data, "ts": float(fields.get("ts") or 0)})
    return out
