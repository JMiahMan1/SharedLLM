"""The services the dashboard watches, as the containers themselves declare them.

No list of service names lives in code. Every ``sharedllm_*`` container is a
monitored service unless it opts out, and each one says how it is checked with
labels on its own compose definition:

``sharedllm.health: "<port>/<path>"``
    Probe ``http://<host>:<port>/<path>``; ``<host>`` is the compose service
    name on the bridge network, or ``host.docker.internal`` for a container on
    the host network. The probe reads ``git_sha`` / ``start_time`` when the
    service reports them.
``sharedllm.monitor: "false"``
    Not a long-running service (a one-shot job such as ``capabilities-sync``).

A container without ``sharedllm.health`` is judged on what Docker knows: its
state and, when its image defines one, its Docker healthcheck. Each entry says
which (``check``), so a green card never hides how little was checked.
"""
from __future__ import annotations

import re
from typing import Any

PREFIX = "sharedllm_"
HEALTH_LABEL = "sharedllm.health"
MONITOR_LABEL = "sharedllm.monitor"
_HEALTH_RE = re.compile(r"^(\d{1,5})(/\S*)?$")


class BadHealthLabel(ValueError):
    """A ``sharedllm.health`` label that is not ``<port>/<path>``."""


def parse_health_label(value: str) -> tuple[int, str]:
    """``"8009/health"`` -> ``(8009, "/health")``. Raises :class:`BadHealthLabel`."""
    m = _HEALTH_RE.match((value or "").strip())
    if not m or not 0 < int(m.group(1)) < 65536:
        raise BadHealthLabel(f"{HEALTH_LABEL}={value!r} is not '<port>/<path>', e.g. '8009/health'")
    return int(m.group(1)), m.group(2) or "/"


def _attrs(container: Any) -> dict[str, Any]:
    return getattr(container, "attrs", None) or {}


def describe(container: Any) -> dict[str, Any] | None:
    """One monitored service from a docker-py container, or None when it is not one."""
    name = getattr(container, "name", "") or ""
    if not name.startswith(PREFIX):
        return None
    labels = getattr(container, "labels", None) or {}
    if str(labels.get(MONITOR_LABEL, "")).strip().lower() in ("false", "no", "0", "off"):
        return None
    attrs = _attrs(container)
    state = attrs.get("State") or {}
    network_mode = (attrs.get("HostConfig") or {}).get("NetworkMode") or ""
    host_network = network_mode == "host"
    compose_service = labels.get("com.docker.compose.service") or name[len(PREFIX):]
    entry: dict[str, Any] = {
        "name": name[len(PREFIX):],
        "container": name,
        "state": getattr(container, "status", None) or state.get("Status") or "unknown",
        "docker_health": (state.get("Health") or {}).get("Status"),
        "exit_code": state.get("ExitCode"),
        "started_at": state.get("StartedAt"),
        "network": "host" if host_network else "bridge",
        "health_url": None,
        "check": "docker healthcheck" if (state.get("Health") or {}).get("Status") else "container state",
        "label_error": None,
    }
    raw = labels.get(HEALTH_LABEL)
    if raw:
        try:
            port, path = parse_health_label(raw)
        except BadHealthLabel as e:
            # Reported, not skipped: a typo in a label must show on the board.
            entry["label_error"] = str(e)
        else:
            host = "host.docker.internal" if host_network else compose_service
            entry["health_url"] = f"http://{host}:{port}{path}"
            entry["check"] = "http"
    return entry


def monitored(containers: list[Any]) -> list[dict[str, Any]]:
    """Every monitored service among ``containers``, sorted by name."""
    out = [d for d in (describe(c) for c in containers) if d is not None]
    return sorted(out, key=lambda d: d["name"])
