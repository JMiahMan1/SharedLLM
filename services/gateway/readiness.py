"""Readiness of every service, from the list the containers declare (no names in code).

The control plane lists the ``sharedllm_*`` containers and how each says it is
checked (``services/control_plane/monitored.py``: the ``sharedllm.health`` and
``sharedllm.monitor`` compose labels). A service added to docker-compose shows
up here on its next readiness poll without anyone editing a list.

When the control plane cannot be asked, the last list it gave is reused and the
answer says so (``inventory.source == "cached"``, with the error), and the
control plane itself shows as unreachable, so the board never looks complete
when it is not. With no list ever fetched, only that failure is reported.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

log = logging.getLogger("gateway.readiness")

#: The last inventory the control plane gave: (fetched_at, services).
_last: tuple[float, list[dict[str, Any]]] | None = None


async def inventory(client: aiohttp.ClientSession, control_plane_url: str,
                    secret: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(services, inventory report). Raises nothing: failure is in the report."""
    global _last
    if not control_plane_url:
        return [], {"source": "none", "error": "CONTROL_PLANE_URL is not set; no service list to check"}
    url = f"{control_plane_url.rstrip('/')}/api/monitored"
    try:
        async with client.get(url, headers={"X-Internal-Secret": secret},
                              timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"control plane answered {resp.status}: {(await resp.text())[:200]}")
            body = await resp.json()
        services = body.get("services")
        if not isinstance(services, list):
            raise RuntimeError("control plane answer has no 'services' list")
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        log.warning("[readiness] service list unavailable: %s", error)
        if _last is None:
            return [], {"source": "none", "error": error}
        return _last[1], {"source": "cached", "as_of": _last[0], "error": error}
    _last = (time.time(), services)
    return services, {"source": "control_plane", "as_of": _last[0]}


async def probe_http(client: aiohttp.ClientSession, url: str) -> tuple[str, dict[str, Any] | None]:
    """("OK", details) or (a failure line, None), with a short retry for a busy service."""
    status = "UNREACHABLE"
    for attempt in range(3):
        try:
            # async with: a non-200 answer left its connection unreleased.
            async with client.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status == 200:
                    details: dict[str, Any] = {}
                    try:
                        data = await resp.json(content_type=None)
                    except (aiohttp.ContentTypeError, ValueError):
                        data = None
                    if isinstance(data, dict):
                        for key in ("git_sha", "start_time", "started_at"):
                            if data.get(key) is not None:
                                details[key] = data[key]
                    return "OK", details
                status = f"ERROR ({resp.status})"
        except Exception as e:
            status = "UNREACHABLE"
            log.warning("[readiness] %s failed (attempt %d): %s", url, attempt + 1, e)
        await asyncio.sleep(0.3)
    return status, None


async def check(client: aiohttp.ClientSession, entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """One service's status line and details from its control-plane entry."""
    details: dict[str, Any] = {"check": entry.get("check"), "container": entry.get("container")}
    if entry.get("label_error"):
        details["error"] = entry["label_error"]
        return "MISCONFIGURED", details
    state = entry.get("state")
    if state != "running":
        code = entry.get("exit_code")
        return (f"STOPPED ({state}, exit {code})" if code is not None else f"STOPPED ({state})"), details
    url = entry.get("health_url")
    if url:
        status, extra = await probe_http(client, url)
        details.update(extra or {})
        return status, details
    docker_health = entry.get("docker_health")
    if docker_health == "unhealthy":
        return "UNHEALTHY", details
    if docker_health == "starting":
        return "STARTING", details
    if entry.get("started_at"):
        details["start_time"] = entry["started_at"]
    return "OK", details


async def collect(client: aiohttp.ClientSession, control_plane_url: str, secret: str,
                  skip: set[str]) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, Any]]:
    """(statuses, details, inventory report) for every listed service not in ``skip``.

    ``skip`` holds the services the caller reports itself (the gateway answering,
    redis by its own ping).
    """
    services, report = await inventory(client, control_plane_url, secret)
    entries = [e for e in services if isinstance(e, dict) and e.get("name") and e["name"] not in skip]
    results = await asyncio.gather(*(check(client, e) for e in entries))
    statuses: dict[str, str] = {}
    details: dict[str, dict[str, Any]] = {}
    for entry, (status, detail) in zip(entries, results, strict=True):
        statuses[entry["name"]] = status
        details[entry["name"]] = detail
    if report["source"] != "control_plane":
        statuses["control_plane"] = "UNREACHABLE"
        details.setdefault("control_plane", {})["error"] = report.get("error")
    return statuses, details, report
