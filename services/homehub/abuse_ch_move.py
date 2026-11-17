"""Moving the hub's abuse.ch MalwareBazaar integration to Accounts & Integrations, once.

The abuse.ch Auth-Key app vetting looks app file hashes up with
(docs/APP_VETTING.md) used to be a hub integration (domain ``malwarebazaar``,
one per hub, added under Devices > Add & fix). It is a per-person login in
identity now, and the system default user's key is the household default
that everyone without their own uses. On the first start after the change
the hub:

* gives the system default user the entry's key, unless that user already
  has one (identity's ``POST /api/internal/household-abuse-ch-key`` never
  overwrites);
* removes the ``malwarebazaar`` entry.

The store's ``abuse_ch_moved`` marker makes it run once. While identity
cannot be asked nothing is removed and the move is tried again, so the key
is never lost. The key is never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("homehub.abuse_ch_move")

MARKER = "abuse_ch_moved"
DOMAIN = "malwarebazaar"
#: Seconds between tries while identity cannot be asked (the last one repeats).
RETRY_DELAYS = (30, 60, 120, 300, 600)


def _identity() -> tuple[str, str]:
    return (os.getenv("IDENTITY_SVC_URL") or "").strip().rstrip("/"), os.getenv("INTERNAL_SECRET") or ""


def give_identity_the_key(key: str) -> str:
    """Identity's answer, "set" or "kept"; raises when identity cannot be asked (the tests replace this)."""
    url, secret = _identity()
    if not url:
        raise RuntimeError("IDENTITY_SVC_URL is not set")
    req = urllib.request.Request(
        f"{url}/api/internal/household-abuse-ch-key", data=json.dumps({"key": key}).encode(), method="POST",
        headers={"X-Internal-Secret": secret, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        body = json.loads(resp.read() or b"{}")
    status = str(body.get("status") or "") if isinstance(body, dict) else ""
    if status not in ("set", "kept"):
        raise RuntimeError("identity gave no answer about the key")
    return status


async def migrate(runtime: Any) -> dict[str, Any]:
    """Run the move if it has not run; raises when identity cannot take the key (try again later)."""
    from services.homehub import entries

    store, hub = runtime.store, runtime.hub
    done = store.get_kv(MARKER, None)
    if done:
        return {"status": "done before", **(done if isinstance(done, dict) else {})}
    found = [e for e in hub.config_entries.values() if e.domain == DOMAIN]
    key = next((str((e.data or {}).get("auth_key") or "").strip() for e in found
                if str((e.data or {}).get("auth_key") or "").strip()), "")
    identity = await asyncio.to_thread(give_identity_the_key, key) if key else None
    for entry in found:
        await entries.remove_entry(runtime, entry.entry_id, force=True)
    record = {"at": datetime.now(UTC).isoformat(), "removed": len(found), "household_key": identity}
    store.set_kv(MARKER, record)
    if found:
        log.info("the abuse.ch integration moved to Accounts & Integrations: household key %s; entries removed: %d",
                 {"set": "set", "kept": "already set, kept"}.get(identity or "", "not given (the entry had none)"),
                 len(found))
    return {"status": "moved", **record}


async def run(runtime: Any) -> None:
    """``migrate`` until it succeeds; a failure is logged once, then quietly retried."""
    attempt = 0
    while True:
        try:
            await migrate(runtime)
            if attempt:
                log.info("the abuse.ch integration moved after %d failed tries", attempt)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The type only: a urllib error's text can carry the request.
            (log.warning if attempt == 0 else log.debug)(
                "could not move the abuse.ch key to identity yet (%s); the integration stays until it can",
                type(exc).__name__)
        await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
        attempt += 1
