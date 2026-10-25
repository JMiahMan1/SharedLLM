"""Admin alerts the gateway sends: services down or back, desktop GPU services
unreachable or back (category ``admin``, to ``@admins``; docs/PUSH.md).

Once a minute the background worker reads the gateway's own readiness probe
(``/health/ready``, the list the dashboard shows) and probes the desktop
services named in config. Admins hear about state changes only:

* A service is down after ``DOWN_AFTER_FAILURES`` failed checks in a row and
  back up on the first good one after that. Each service has its own tag, so
  "back up" replaces "down" on the phone.
* A desktop service is unreachable once no HTTP answer came for
  ``alert_desktop_down_minutes``. The desktop sleeps and wakes as a whole, so
  all of them share one notice (tag ``admin:desktop``) that is updated as the
  set of unreachable services changes and replaced by "back" when it empties.
* A watched HTTPS certificate (``alert_tls_hosts``) is checked every
  ``TLS_CHECK_SECONDS``: admins hear once when it is within
  ``alert_tls_days`` of expiring or no longer valid, and once more when a
  renewed certificate is being served (same tag per host).

State lives in Redis so a gateway restart neither repeats a notice nor loses
the "back up" that answers one. A notice telemetry did not accept stays in an
outbox and is sent on the next pass (a newer notice with the same tag wins).
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import aiohttp

from services.common import notify
from services.config import ALERT_DESKTOP_DOWN_MINUTES, ALERT_TLS_DAYS, ALERT_TLS_HOSTS
from services.gateway.config import (
    ALPACA_ARCADE_URL,
    ALPACA_AUDIO_URL,
    ALPACA_WEB_URL,
    INTERNAL_SECRET,
    TELEMETRY_SVC,
)
from services.gateway.service_probes import probe_http

log = logging.getLogger("gateway.admin_alerts")

CHECK_SECONDS = 60
DOWN_AFTER_FAILURES = 2
STATE_KEY = "admin:alerts:gateway"
DASHBOARD_URL = "/"
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=10)
TLS_CHECK_SECONDS = 6 * 3600
_TLS_TIMEOUT_SECONDS = 15


async def notify_admins(client: aiohttp.ClientSession, notice: dict[str, Any]) -> bool:
    """Send one admin notice through telemetry; True once telemetry accepted it.

    ``notice`` carries title, body, tag, priority and optional data/actions;
    the recipient and category are always ``@admins`` / ``admin``.
    """
    payload = {"kind": "admin", **notice, "user": "@admins", "category": "admin"}
    return await notify.send(payload, client=client, telemetry=TELEMETRY_SVC, secret=INTERNAL_SECRET)


def _open_dashboard() -> dict[str, Any]:
    return {
        "data": {"url": DASHBOARD_URL},
        "actions": [{"label": "Open", "type": "view", "url": DASHBOARD_URL}],
    }


def service_transitions(state: dict[str, Any], statuses: dict[str, str]) -> list[dict[str, Any]]:
    """Notices for services that went down or came back; updates ``state``.

    ``statuses`` maps a service to its readiness value ("OK", "UNREACHABLE",
    "ERROR (502)"). ``state`` holds ``fails`` (consecutive failures) and
    ``down`` (services admins were told are down).
    """
    fails: dict[str, int] = state.setdefault("fails", {})
    down: list[str] = state.setdefault("down", [])
    notices = []
    for name, status in sorted(statuses.items()):
        label = name.replace("_", " ")
        if status == "OK":
            fails.pop(name, None)
            if name in down:
                down.remove(name)
                notices.append({
                    "title": f"Service back up: {label}",
                    "body": f"{label} is answering its health check again.",
                    "priority": 2,
                    "tag": f"admin:service:{name}",
                    **_open_dashboard(),
                })
            continue
        fails[name] = fails.get(name, 0) + 1
        if fails[name] >= DOWN_AFTER_FAILURES and name not in down:
            down.append(name)
            notices.append({
                "title": f"Service down: {label}",
                "body": f"{label} failed {fails[name]} health checks in a row ({status}).",
                "priority": 4,
                "tag": f"admin:service:{name}",
                **_open_dashboard(),
            })
    return notices


def desktop_transitions(state: dict[str, Any], reachable: dict[str, bool], now: float,
                        down_minutes: int) -> list[dict[str, Any]]:
    """The desktop notice when the set of long-unreachable services changes.

    ``reachable`` maps a service label to whether it answered this pass.
    ``state`` holds ``since`` (label -> first unanswered check) and
    ``alerted`` (labels the current notice names).
    """
    since: dict[str, float] = state.setdefault("since", {})
    alerted: list[str] = state.setdefault("alerted", [])
    for label, ok in reachable.items():
        if ok:
            since.pop(label, None)
        else:
            since.setdefault(label, now)
    for label in [k for k in since if k not in reachable]:  # no longer configured
        since.pop(label)
    limit = down_minutes * 60
    confirmed = sorted(k for k, t in since.items() if now - t >= limit)
    if confirmed == sorted(alerted):
        return []
    back = sorted(set(alerted) - set(confirmed))
    state["alerted"] = confirmed
    if confirmed:
        body = f"No answer for {down_minutes}+ minutes: {', '.join(confirmed)}."
        if back:
            body += f" Back: {', '.join(back)}."
        return [{
            "title": "Desktop GPU service unreachable",
            "body": body,
            "priority": 3,
            "tag": "admin:desktop",
            **_open_dashboard(),
        }]
    return [{
        "title": "Desktop GPU services back",
        "body": f"Reachable again: {', '.join(back)}.",
        "priority": 2,
        "tag": "admin:desktop",
        **_open_dashboard(),
    }]


def desktop_targets(settings: dict[str, Any]) -> dict[str, str]:
    """Label -> URL for every desktop service configured; unset ones are skipped."""
    candidates = {
        "LLM proxy": settings.get("llm_local_url") or "",
        "Vision OCR": settings.get("vision_ocr_proxy_url") or "",
        "Audio server": f"{ALPACA_AUDIO_URL}/health" if ALPACA_AUDIO_URL else "",
        "Alpaca dashboard": ALPACA_WEB_URL,
        "Arcade": ALPACA_ARCADE_URL,
    }
    targets: dict[str, str] = {}
    seen: set[str] = set()
    for label, url in candidates.items():
        url = str(url).strip().rstrip("/")
        if url and url not in seen:
            seen.add(url)
            targets[label] = url
    return targets


async def _answers(client: aiohttp.ClientSession, url: str) -> bool:
    """Any HTTP answer counts: the question is whether the machine is up."""
    try:
        async with client.get(url, timeout=_PROBE_TIMEOUT) as resp:
            return resp.status > 0
    except Exception:
        return False


def media_targets(settings: dict[str, Any]) -> dict[str, str]:
    """Service -> liveness URL for the self-hosted backends.

    Audiobookshelf has an explicit ``/ping``; Music Assistant answers on its
    bare origin, so the root is its liveness check; Nextcloud exposes
    ``/status.php``. Unset URLs are skipped.
    """
    abs_url = str(settings.get("audiobookshelf_url") or settings.get("abs_url") or "").strip().rstrip("/")
    ma_url = str(settings.get("mass_url") or settings.get("ma_url") or "").strip().rstrip("/")
    nextcloud_url = str(settings.get("nextcloud_url") or "").strip().rstrip("/")
    targets: dict[str, str] = {}
    if abs_url:
        targets["audiobookshelf"] = f"{abs_url}/ping"
    if ma_url:
        targets["music_assistant"] = ma_url
    if nextcloud_url:
        targets["nextcloud"] = f"{nextcloud_url}/status.php"
    return targets


def tls_hosts(settings: dict[str, Any]) -> list[str]:
    """The host names to watch, from ``alert_tls_hosts`` (comma-separated)."""
    raw = settings.get("alert_tls_hosts")
    raw = ALERT_TLS_HOSTS if raw is None else raw
    hosts = []
    for item in str(raw or "").split(","):
        host = item.strip().lower().removeprefix("https://").split("/")[0]
        if host and host not in hosts:
            hosts.append(host)
    return hosts


async def tls_status(host: str, *, now: float | None = None) -> dict[str, Any]:
    """What ``host``:443 serves: ``{"ok": True, "days": n}`` for a certificate
    the system trusts, else ``{"ok": False, "reason": ...}`` (expired, wrong
    name, untrusted, or no answer)."""
    now = time.time() if now is None else now
    context = ssl.create_default_context()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 443, ssl=context, server_hostname=host),
            timeout=_TLS_TIMEOUT_SECONDS,
        )
    except ssl.SSLCertVerificationError as e:
        return {"ok": False, "reason": e.verify_message or "certificate not valid"}
    except (OSError, TimeoutError, ssl.SSLError) as e:
        return {"ok": False, "reason": f"no TLS answer ({type(e).__name__})"}
    try:
        cert = writer.get_extra_info("peercert") or {}
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
    not_after = cert.get("notAfter")
    if not not_after:
        return {"ok": False, "reason": "certificate has no expiry date"}
    expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC).timestamp()
    return {"ok": True, "days": int((expires - now) // 86400)}


def tls_transitions(state: dict[str, Any], results: dict[str, dict[str, Any]], warn_days: int) -> list[dict[str, Any]]:
    """Notices for certificates that became a problem or were fixed; updates ``state``.

    A problem is a certificate within ``warn_days`` of expiring or not valid at
    all. ``state["warned"]`` holds the hosts admins were told about.
    """
    warned: list[str] = state.setdefault("warned", [])
    notices = []
    for host, result in sorted(results.items()):
        tag = f"admin:tls:{host}"
        problem = not result.get("ok") or int(result.get("days", 0)) < warn_days
        if problem and host not in warned:
            warned.append(host)
            if result.get("ok"):
                body = (f"The certificate for {host} expires in {result['days']} days and has not renewed. "
                        "Check certbot on the proxy.")
            else:
                body = f"{host} is not serving a valid certificate: {result.get('reason')}. Browsers and the app will refuse it."
            notices.append({"title": f"Certificate problem: {host}", "body": body, "priority": 4, "tag": tag,
                            **_open_dashboard()})
        elif not problem and host in warned:
            warned.remove(host)
            notices.append({"title": f"Certificate renewed: {host}",
                            "body": f"{host} serves a valid certificate again ({result['days']} days left).",
                            "priority": 2, "tag": tag, **_open_dashboard()})
    for host in [h for h in warned if h not in results]:  # no longer watched
        warned.remove(host)
    return notices


async def _readiness(client: aiohttp.ClientSession, gateway: str) -> dict[str, str] | None:
    try:
        async with client.get(f"{gateway}/health/ready", timeout=aiohttp.ClientTimeout(total=30)) as resp:
            body = await resp.json(content_type=None)
    except Exception as e:
        log.warning(f"[admin-alerts] readiness unavailable: {type(e).__name__}: {e}")
        return None
    services = body.get("services") if isinstance(body, dict) else None
    if not isinstance(services, dict):
        return None
    return {str(k): str(v) for k, v in services.items() if k != "gateway"}


def _queue(outbox: list[dict[str, Any]], notices: list[dict[str, Any]]) -> None:
    """Add notices to the outbox; a newer notice replaces a queued one with its tag."""
    for notice in notices:
        outbox[:] = [n for n in outbox if n.get("tag") != notice.get("tag")]
        outbox.append(notice)


async def check_once(r, client: aiohttp.ClientSession, settings: dict[str, Any], *,
                     gateway: str, now: float | None = None) -> int:
    """One pass of both detectors; returns how many notices telemetry accepted.

    ``gateway`` is the gateway's own base URL (its readiness probe).
    """
    now = time.time() if now is None else now
    raw = await r.get(STATE_KEY)
    state = json.loads(raw) if raw else {}
    outbox: list[dict[str, Any]] = state.setdefault("outbox", [])

    statuses = await _readiness(client, gateway)
    if statuses is not None:
        # Media backends are not in the gateway's readiness list, so probe
        # them here and feed the same transition machinery. The classified
        # line ("UNREACHABLE (No answer within 4s)") lands in the notice.
        media = media_targets(settings)
        if media:
            results = await asyncio.gather(*(probe_http(client, name, url) for name, url in media.items()))
            for result in results:
                statuses[result.name] = result.line
        _queue(outbox, service_transitions(state.setdefault("services", {}), statuses))

    targets = desktop_targets(settings)
    results = await asyncio.gather(*(_answers(client, url) for url in targets.values()))
    down_minutes = int(settings.get("alert_desktop_down_minutes") or ALERT_DESKTOP_DOWN_MINUTES)
    _queue(outbox, desktop_transitions(state.setdefault("desktop", {}), dict(zip(targets, results, strict=False)),
                                       now, down_minutes))

    hosts = tls_hosts(settings)
    tls_state = state.setdefault("tls", {})
    if hosts and now - float(tls_state.get("checked_at") or 0) >= TLS_CHECK_SECONDS:
        results = await asyncio.gather(*(tls_status(host, now=now) for host in hosts))
        warn_days = int(settings.get("alert_tls_days") or ALERT_TLS_DAYS)
        _queue(outbox, tls_transitions(tls_state, dict(zip(hosts, results, strict=True)), warn_days))
        tls_state["checked_at"] = now

    sent = 0
    pending = []
    for notice in outbox:
        if await notify_admins(client, notice):
            sent += 1
        else:
            pending.append(notice)
    state["outbox"] = pending
    await r.set(STATE_KEY, json.dumps(state))
    return sent
