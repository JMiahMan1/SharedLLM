"""Alert admins once when a stored login stops being accepted.

Background flows (home alerts, telemetry ingestion, media probes, discovery
sweeps) call external services with stored logins. When one is rejected — an
expired Home Assistant long-lived token is the classic case — every attempt
logs the same line forever and nobody notices. This module keeps one flag per
(service, account): the first rejection logs a line and sends a single admin
notice through telemetry, repeats stay quiet, and the first accepted call
logs a recovery line and replaces the notice on the phone (same tag). State
lives in Redis, so a restart neither repeats an alert nor forgets one; a
notice telemetry did not take is retried on the next pass.
"""
from __future__ import annotations

import logging
import time

from services import config
from services.common import notify

log = logging.getLogger("execution.credentials")

STATE_KEY = "admin:alerts:credentials"

_SERVICE_LABELS = {"home_assistant": "Home Assistant"}

#: The flags this process knows about. Loaded from Redis on first use; kept in
#: memory so a healthy call never touches Redis.
_mirror: dict[str, dict] | None = None


def _label(service: str) -> str:
    return _SERVICE_LABELS.get(service, service.replace("_", " ").title())


def _entry_key(service: str, account: str) -> str:
    return f"{service}:{account}"


async def _redis_client():
    from redis import asyncio as aioredis

    return aioredis.from_url(config.REDIS_URL, decode_responses=True)


async def _load(r) -> dict:
    """The persisted state, best effort: an unreadable store just means a
    fresh start (the alert repeats once after a crash — the safe direction)."""
    global _mirror
    if _mirror is None:
        _mirror = {"flagged": {}, "outbox": []}
        try:
            import json

            raw = await r.get(STATE_KEY)
            if raw:
                state = json.loads(raw)
                if isinstance(state.get("flagged"), dict):
                    _mirror["flagged"] = state["flagged"]
                if isinstance(state.get("outbox"), list):
                    _mirror["outbox"] = state["outbox"]
        except Exception as e:  # store unavailable: in-memory only this run
            log.debug(f"[credentials] state read failed: {e}")
    return _mirror


async def _save(r, state: dict) -> None:
    try:
        import json

        await r.set(STATE_KEY, json.dumps(state))
    except Exception as e:
        log.debug(f"[credentials] state write failed: {e}")


async def _deliver(state: dict, payload: dict) -> None:
    """Send now; keep it queued (newest of its tag wins) if telemetry is down."""
    tag = payload.get("tag")
    state["outbox"] = [p for p in state.get("outbox", []) if p.get("tag") != tag]
    if not await notify.send(payload):
        state["outbox"].append(payload)


async def _flush(state: dict) -> None:
    pending = list(state.get("outbox", []))
    state["outbox"] = []
    for payload in pending:
        if not await notify.send(payload):
            state["outbox"].append(payload)


async def auth_failed(service: str, account: str, detail: str = "") -> None:
    """A stored login was rejected (HTTP 401/403). First sight notifies, then quiet."""
    key = _entry_key(service, account)
    r = None
    try:
        r = await _redis_client()
        state = await _load(r)
        if key in _mirror.get("flagged", {}):
            return
        state["flagged"][key] = {"since": time.time()}
        await _flush(state)
        label = _label(service)
        log.warning(f"[credentials] {label} rejected the {account} login ({detail or 'unauthorized'}); admins notified")
        payload = notify.notice(
            "@admins",
            "admin",
            f"Credential expired: {label}",
            f"The {label} login for {account} was rejected ({detail or 'unauthorized'}). "
            "Renew it in Settings; flows using it keep failing until then.",
            url="/",
            priority=3,
            tag=f"admin:expired:{key}",
            kind="admin",
        )
        await _deliver(state, payload)
        await _save(r, state)
    except Exception as e:
        log.debug(f"[credentials] alert failed: {e}")
    finally:
        if r is not None:
            try:
                await r.aclose()
            except Exception:
                pass


async def auth_ok(service: str, account: str) -> None:
    """The login works. If it was flagged, log once and replace the notice."""
    key = _entry_key(service, account)
    if _mirror is not None and key not in _mirror.get("flagged", {}):
        return  # the common case: nothing was wrong
    r = None
    try:
        r = await _redis_client()
        state = await _load(r)
        if key not in state["flagged"]:
            return
        del state["flagged"][key]
        await _flush(state)
        label = _label(service)
        log.info(f"[credentials] {label} accepted the {account} login again")
        payload = notify.notice(
            "@admins",
            "admin",
            f"{label} login works again",
            f"The stored login for {account} was accepted again.",
            url="/",
            priority=2,
            tag=f"admin:expired:{key}",
            kind="admin",
        )
        await _deliver(state, payload)
        await _save(r, state)
    except Exception as e:
        log.debug(f"[credentials] recovery notice failed: {e}")
    finally:
        if r is not None:
            try:
                await r.aclose()
            except Exception:
                pass
