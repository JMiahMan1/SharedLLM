"""Moving the app vetting settings from identity's global settings to the hub's integrations, once.

The two outside sources app vetting asks (docs/APP_VETTING.md) used to be
identity global settings edited in an Admin box: ``malwarebazaar_auth_key``
and ``app_vetting_common_sense_enabled``. They are integrations now
(manual_flows: ``malwarebazaar``, ``common_sense_media``), set up on the
Devices page. On the first start after the change the hub:

* creates the Common Sense Media entry unless the old switch was turned off
  ("false" and the like), because Common Sense lookups were on by default and
  the household must not lose them without a word;
* creates the MalwareBazaar entry with the stored key, when there was one
  (no lookup to check it: telemetry already used it, and says so if
  abuse.ch refuses it);
* empties both settings in identity, which deletes the emptied rows when it
  next starts (``services/identity/main.py`` ``_RETIRED_SETTINGS``).

The store's ``app_checks_moved`` marker makes it run once: an admin who later
removes either entry keeps it removed. Until identity has answered and the
settings are emptied the marker is not set, so a hub that started before
identity tries again (an entry already there is never made twice). The key is
never logged.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("homehub.app_checks_migration")

MARKER = "app_checks_moved"
OLD_KEY = "malwarebazaar_auth_key"
OLD_SWITCH = "app_vetting_common_sense_enabled"
_OFF = ("false", "0", "no", "off")
#: Seconds between tries while identity cannot be asked (the last one repeats).
RETRY_DELAYS = (30, 60, 120, 300, 600)


# -- identity (the tests replace these) ----------------------------------------------------

def _identity() -> tuple[str, str]:
    return (os.getenv("IDENTITY_SVC_URL") or "").strip().rstrip("/"), os.getenv("INTERNAL_SECRET") or ""


def read_identity_settings() -> dict[str, Any] | None:
    """Identity's global settings with the internal secret (secrets unmasked); None without identity."""
    from services.common import household_tz

    url, secret = _identity()
    if not url:
        return None
    return household_tz.fetch_identity_settings(url, secret)


def clear_identity_settings(keys: list[str]) -> None:
    """Empty the old settings (identity deletes empty retired rows when it starts)."""
    url, secret = _identity()
    if not url or not keys:
        return
    req = urllib.request.Request(
        f"{url}/api/settings", data=json.dumps(dict.fromkeys(keys, "")).encode(), method="POST",
        headers={"X-Internal-Secret": secret, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        resp.read()


# -- the move ------------------------------------------------------------------------------

def _has(hub: Any, domain: str) -> bool:
    return any(e.domain == domain and e.source != "ignore" for e in hub.config_entries.values())


async def migrate(runtime: Any) -> dict[str, Any]:
    """Run the move if it has not run; raises when identity cannot be read or emptied (try again later)."""
    from services.homehub import manual_flows
    from services.homehub.flows import commit

    store, hub = runtime.store, runtime.hub
    done = store.get_kv(MARKER, None)
    if done:
        return {"status": "done before", **(done if isinstance(done, dict) else {})}
    settings = await asyncio.to_thread(read_identity_settings) or {}
    created: list[str] = []
    switch = str(settings.get(OLD_SWITCH) or "").strip().lower()
    if switch not in _OFF and not _has(hub, "common_sense_media"):
        await commit(runtime, manual_flows.common_sense_plan(hub))
        created.append("common_sense_media")
    key = str(settings.get(OLD_KEY) or "").strip()
    if key and key != "***" and not _has(hub, "malwarebazaar"):
        await commit(runtime, manual_flows.malwarebazaar_plan(hub, key))
        created.append("malwarebazaar")
    old = [k for k in (OLD_KEY, OLD_SWITCH) if settings.get(k) not in (None, "")]
    if old:
        await asyncio.to_thread(clear_identity_settings, old)
    record = {"at": datetime.now(UTC).isoformat(), "created": created, "cleared": old,
              "common_sense_was_off": switch in _OFF}
    store.set_kv(MARKER, record)
    log.info("app checks moved to integrations: created %s; emptied identity settings %s",
             ", ".join(created) or "nothing", ", ".join(old) or "none")
    return {"status": "moved", **record}


async def run(runtime: Any) -> None:
    """``migrate`` until it succeeds; a failure is logged once, then quietly retried."""
    attempt = 0
    while True:
        try:
            await migrate(runtime)
            if attempt:
                log.info("app checks moved after %d failed tries", attempt)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The type only: a urllib error's text can carry the request.
            (log.warning if attempt == 0 else log.debug)(
                "could not move the app check settings from identity yet (%s); trying again",
                type(exc).__name__)
        await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
        attempt += 1
