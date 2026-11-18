"""Moving the admin page's "light clusters" into the hub's light groups, once.

Light clusters were a list kept in identity (``GlobalSetting`` ``light_cluster:*``,
Admin > Device Groups). Nothing could switch one: they only named lights for the
light patterns. A light group (``light_groups.py``) is a real light the whole
house can use, so on the first start after the change the hub:

* makes a light group from every cluster (its name, its lights that are in the
  hub, its room when the cluster named one the hub has), unless a group of that
  name exists already;
* deletes the cluster from identity once its group exists (or it had no lights
  left to group: its record, members included, is kept in the marker).

The store's ``light_clusters_moved`` marker makes it run once. While identity
cannot be asked nothing changes and the move is tried again later.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("homehub.light_cluster_move")

MARKER = "light_clusters_moved"
#: Seconds between tries while identity cannot be asked (the last one repeats).
RETRY_DELAYS = (30, 60, 120, 300, 600)


def _identity() -> tuple[str, str]:
    return (os.getenv("IDENTITY_SVC_URL") or "").strip().rstrip("/"), os.getenv("INTERNAL_SECRET") or ""


def _request(method: str, path: str) -> Any:
    url, secret = _identity()
    if not url:
        raise RuntimeError("IDENTITY_SVC_URL is not set")
    req = urllib.request.Request(f"{url}{path}", method=method, headers={"X-Internal-Secret": secret})
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        return json.loads(resp.read() or b"null")


def read_clusters() -> list[dict[str, Any]]:
    """Identity's light clusters (the tests replace this)."""
    body = _request("GET", "/api/groups/lights")
    if isinstance(body, dict):
        body = body.get("clusters") or []
    return [c for c in body or [] if isinstance(c, dict)]


def delete_cluster(cluster_id: str) -> None:
    """Delete one cluster from identity (the tests replace this)."""
    _request("DELETE", f"/api/groups/lights/{urllib.parse.quote(cluster_id, safe='')}")


async def migrate(runtime: Any) -> dict[str, Any]:
    """Run the move if it has not run; raises when identity cannot be asked (try again later)."""
    from services.homehub import light_groups

    store, hub = runtime.store, runtime.hub
    done = store.get_kv(MARKER, None)
    if done:
        return {"status": "done before", **(done if isinstance(done, dict) else {})}
    clusters = await asyncio.to_thread(read_clusters)
    moved: list[dict[str, Any]] = []
    for c in clusters:
        cluster_id = str(c.get("cluster_id") or c.get("name") or "").strip()
        name = " ".join(str(c.get("cluster_name") or c.get("name") or cluster_id).split())
        if not cluster_id or not name:
            continue
        wanted = [str(m) for m in c.get("member_entity_ids") or [] if str(m).startswith("light.")]
        lights = [m for m in wanted if m in hub.entities]
        room = str(c.get("room") or "").strip()
        area_id = hub.area_id_by_name(room) if room else None
        record = {"cluster": cluster_id, "name": name, "members": list(c.get("member_entity_ids") or []),
                  "room": room or None}
        if light_groups._name_taken(hub, name):
            record["group"] = "a group of that name was there already"
        elif len(lights) < 2:
            record["group"] = "not made: fewer than two of its lights are in the hub"
        else:
            body: dict[str, Any] = {"name": name, "entities": lights}
            if area_id:
                body["area_id"] = area_id
            made = await light_groups.create_group(runtime, body)
            record["group"] = made.get("entity_id")
        await asyncio.to_thread(delete_cluster, cluster_id)
        moved.append(record)
    out = {"at": datetime.now(UTC).isoformat(), "clusters": moved}
    store.set_kv(MARKER, out)
    if moved:
        log.info("moved %d light clusters to light groups", len(moved))
    return {"status": "moved", **out}


async def run(runtime: Any) -> None:
    """``migrate`` until it succeeds; a failure is logged once, then quietly retried."""
    attempt = 0
    while True:
        try:
            await migrate(runtime)
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            (log.warning if attempt == 0 else log.debug)(
                "could not move the light clusters yet (%s); they stay until it can", type(exc).__name__)
        await asyncio.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
        attempt += 1
