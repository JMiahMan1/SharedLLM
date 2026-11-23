"""Container recreate: replace a running container with a new image, keeping
everything else it had.

Shared by the in-process path (``main._recreate_container``) and the detached
self-update child (``run_detached_self_recreate``), so both behave the same.

How a container is replaced (incident 2026-11-22, identity):

* The new container is created from the old one's full ``Config`` and
  ``HostConfig`` (ports, mounts, devices, env, labels, healthcheck, restart
  policy, extra_hosts, cap_add, user, dns, ...), not a hand-picked subset.
  Values the old *image* supplied (its Env, Labels, Cmd, healthcheck...) are
  left out, so the new image's own values apply, as compose would do.
* Its networks and aliases are attached in the create call itself
  (``NetworkingConfig``). Connecting them after create left a window where
  the compose name ("identity") did not resolve, Caddy could not proxy to it
  and execution, which reaches identity through Caddy, failed its startup.
* Start-first when it is safe: the new container starts beside the old one,
  serving the same aliases, and the old one is stopped only once the new one
  is healthy. Containers that cannot run twice (host network, published host
  ports, a static IP, or the ``sharedllm.update=stop-first`` label) stop the
  old one first, then create the new one.
* After create the new container is checked against the old one (ports,
  network mode, networks and aliases, mounts, env, restart policy,
  healthcheck...) and must become healthy. Otherwise it is removed, the old
  container keeps (or resumes) serving, one error is logged and the admins
  are told once.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import docker

log = logging.getLogger("control_plane.recreate")

#: Label a service can carry to never run two copies at once (e.g. a single-writer store).
STOP_FIRST_LABEL = "sharedllm.update"

#: Config keys an image supplies. When the old container's value is the old
#: image's own, it is dropped so the new image's value applies.
IMAGE_DEFAULT_KEYS = ("Cmd", "Entrypoint", "WorkingDir", "User", "Healthcheck", "Volumes", "StopSignal", "Shell", "OnBuild")

#: HostConfig fields that must come through unchanged.
CHECKED_HOST_CONFIG = (
    "PortBindings", "NetworkMode", "Binds", "Devices", "DeviceRequests", "ExtraHosts", "CapAdd", "CapDrop",
    "Dns", "DnsSearch", "DnsOptions", "Privileged", "ReadonlyRootfs", "Tmpfs", "SecurityOpt", "GroupAdd",
    "Memory", "MemorySwap", "NanoCpus", "Init", "ShmSize", "Sysctls", "Ulimits", "PidMode", "IpcMode",
    "LogConfig", "PublishAllPorts",
)

HEALTH_TIMEOUT_MIN_S = 20.0
HEALTH_TIMEOUT_MAX_S = float(os.getenv("RECREATE_HEALTH_TIMEOUT_MAX", "300"))
#: A container without a healthcheck must stay up this long without restarting.
NO_HEALTHCHECK_SETTLE_S = float(os.getenv("RECREATE_SETTLE_SECONDS", "5"))

_alerted: set[tuple[str, str]] = set()


class RecreateFailed(RuntimeError):
    """The new container was refused; the old one is serving again."""


def _say(msg: str) -> None:
    # print, not log.info: the uvicorn logging setup hides INFO from this process,
    # and these lines are what an operator reads in `docker logs`.
    print(f"[recreate] {msg}", flush=True)


def _empty(value: Any) -> Any:
    """None, [], {} and "" all mean "not set" in an inspect."""
    return value if value not in (None, "", [], {}) else None


# --- snapshot --------------------------------------------------------------

def snapshot_container(container) -> dict:
    """Everything needed to recreate ``container``, read while it is still up.

    attrs after stop/rename can have empty NetworkSettings (None Aliases/IP),
    which once left recreated containers with Networks={}.
    """
    container.reload()
    attrs = container.attrs or {}
    config = copy.deepcopy(attrs.get("Config") or {})
    host_config = copy.deepcopy(attrs.get("HostConfig") or {})
    networks: dict[str, dict] = {}
    for net_name, net in ((attrs.get("NetworkSettings") or {}).get("Networks") or {}).items():
        if not isinstance(net, dict):
            continue
        ipam = net.get("IPAMConfig") or {}
        networks[net_name] = {
            "Aliases": list(net.get("Aliases") or []),
            # Only an address the container was configured with is pinned; a
            # dynamic one is whatever IPAM handed out and is not part of its config.
            "IPv4Address": ipam.get("IPv4Address") or "",
            "IPv6Address": ipam.get("IPv6Address") or "",
            "Links": net.get("Links"),
            "DriverOpts": net.get("DriverOpts"),
            "ObservedIPv4": net.get("IPAddress") or "",
        }
    image_config: dict = {}
    with suppress(Exception):
        image_config = copy.deepcopy((container.image.attrs or {}).get("Config") or {})
    mounts = [
        {k: m.get(k) for k in ("Type", "Name", "Source", "Destination", "RW")}
        for m in attrs.get("Mounts") or []
        if isinstance(m, dict)
    ]
    labels = config.get("Labels") or {}
    return {
        "name": (attrs.get("Name") or "").lstrip("/") or container.name,
        "id": container.id,
        "image_id": attrs.get("Image") or "",
        "config": config,
        "host_config": host_config,
        "networks": networks,
        "image_config": image_config,
        "mounts": mounts,
        "compose_service": labels.get("com.docker.compose.service") or "",
    }


def network_aliases(snap: dict, net_config: dict, new_id: str = "") -> list[str]:
    """Compose DNS aliases for a recreated container (filter auto container IDs)."""
    aliases = [
        a
        for a in (net_config.get("Aliases") or [])
        if a
        and a != (snap.get("id") or "")[:12]
        and (not new_id or a != new_id[:12])
        and not re.fullmatch(r"[0-9a-f]{12}", a)
    ]
    name = (snap.get("name") or "").lstrip("/")
    short = name.removeprefix("sharedllm_")
    service = snap.get("compose_service") or ""
    for extra in (name, short, service):
        if extra and extra not in aliases:
            aliases.append(extra)
    return list(dict.fromkeys(aliases))


def _network_mode(snap: dict) -> str:
    return (snap.get("host_config") or {}).get("NetworkMode") or ""


def _attached_networks(snap: dict) -> dict[str, dict]:
    """The networks the new container must be on, by name (none for host/none/container: modes)."""
    mode = _network_mode(snap)
    if mode in ("host", "none") or mode.startswith("container:"):
        return {}
    return {
        name: net
        for name, net in (snap.get("networks") or {}).items()
        if not (name == "bridge" and mode in ("default", "bridge", ""))
    }


def _endpoint(snap: dict, net: dict) -> dict:
    endpoint: dict[str, Any] = {"Aliases": network_aliases(snap, net)}
    ipam = {k: net[k] for k in ("IPv4Address", "IPv6Address") if net.get(k)}
    if ipam:
        endpoint["IPAMConfig"] = ipam
    for key in ("Links", "DriverOpts"):
        if net.get(key):
            endpoint[key] = net[key]
    return endpoint


def stop_first_reason(snap: dict) -> str | None:
    """Why the old container must stop before the new one starts, or None."""
    labels = (snap.get("config") or {}).get("Labels") or {}
    if labels.get(STOP_FIRST_LABEL) == "stop-first":
        return f"label {STOP_FIRST_LABEL}=stop-first"
    mode = _network_mode(snap)
    if mode == "host":
        return "host network"
    if mode.startswith("container:"):
        return "shares another container's network"
    bindings = (snap.get("host_config") or {}).get("PortBindings") or {}
    if any(b.get("HostPort") for binds in bindings.values() for b in binds or []):
        return "publishes host ports"
    if any(net.get("IPv4Address") or net.get("IPv6Address") for net in _attached_networks(snap).values()):
        return "static IP"
    return None


# --- create ----------------------------------------------------------------

def own_env(snap: dict) -> list[str]:
    """The env the container was given (compose/env_file), not what the old image baked in."""
    image_env = set((snap.get("image_config") or {}).get("Env") or [])
    return [e for e in (snap.get("config") or {}).get("Env") or [] if e not in image_env]


def own_labels(snap: dict) -> dict:
    image_labels = (snap.get("image_config") or {}).get("Labels") or {}
    labels = (snap.get("config") or {}).get("Labels") or {}
    return {k: v for k, v in labels.items() if image_labels.get(k) != v}


def build_create_config(snap: dict, new_image_id: str) -> dict:
    """The /containers/create body: the old Config + HostConfig + NetworkingConfig."""
    config = copy.deepcopy(snap.get("config") or {})
    image_config = snap.get("image_config") or {}
    config["Image"] = new_image_id
    for key in IMAGE_DEFAULT_KEYS:
        if key in config and key in image_config and config[key] == image_config[key]:
            config.pop(key)
    config["Env"] = own_env(snap)
    config["Labels"] = own_labels(snap)
    # Docker names an unnamed container's host after its id; carrying the old id
    # over would make the new container claim the old one's hostname.
    if config.get("Hostname") == (snap.get("id") or "")[:12]:
        config.pop("Hostname")
    if not config.get("Domainname"):
        config.pop("Domainname", None)
    config["HostConfig"] = copy.deepcopy(snap.get("host_config") or {})
    endpoints = {name: _endpoint(snap, net) for name, net in _attached_networks(snap).items()}
    if endpoints:
        config["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    return config


def create_from_snapshot(client: docker.DockerClient, snap: dict, new_image_id: str, name: str):
    """Create (not start) the replacement with its networks and aliases already attached."""
    body = build_create_config(snap, new_image_id)
    resp = client.api.create_container_from_config(body, name=name)
    new = client.containers.get(resp["Id"])
    # Daemons before API 1.44 attach only the first endpoint at create. Join the
    # rest now, still before start, so the container never runs without them.
    new.reload()
    have = set(((new.attrs or {}).get("NetworkSettings") or {}).get("Networks") or {})
    for net_name, endpoint in (body.get("NetworkingConfig") or {}).get("EndpointsConfig", {}).items():
        if net_name in have:
            continue
        ipam = endpoint.get("IPAMConfig") or {}
        client.networks.get(net_name).connect(
            new, aliases=endpoint.get("Aliases"), ipv4_address=ipam.get("IPv4Address") or None,
        )
        _say(f"joined {name} to {net_name} before start aliases={endpoint.get('Aliases')}")
    new.reload()
    return new


def connect_container_networks(client: docker.DockerClient, container, snap: dict, *, only=None) -> None:
    """Reattach ``container`` to its snapshotted networks (used when restoring the old one)."""
    for net_name, net in _attached_networks(snap).items():
        if only is not None and net_name not in only:
            continue
        endpoint = _endpoint(snap, net)
        ipam = endpoint.get("IPAMConfig") or {}
        try:
            client.networks.get(net_name).connect(
                container, aliases=endpoint["Aliases"], ipv4_address=ipam.get("IPv4Address") or None,
            )
            _say(f"reconnected {container.name} to {net_name} aliases={endpoint['Aliases']}")
        except Exception as e:
            log.error(f"[recreate] could not reconnect {container.name} to {net_name}: {e}")


# --- checks ----------------------------------------------------------------

def verify_carried_over(snap: dict, new_attrs: dict) -> list[str]:
    """What the new container lost compared with the old one ([] = nothing)."""
    problems: list[str] = []
    old_hc = snap.get("host_config") or {}
    new_hc = new_attrs.get("HostConfig") or {}
    for key in CHECKED_HOST_CONFIG:
        if _empty(old_hc.get(key)) != _empty(new_hc.get(key)):
            problems.append(f"HostConfig.{key} {old_hc.get(key)!r} -> {new_hc.get(key)!r}")
    if (old_hc.get("RestartPolicy") or {}).get("Name") != (new_hc.get("RestartPolicy") or {}).get("Name"):
        problems.append(f"restart policy {old_hc.get('RestartPolicy')} -> {new_hc.get('RestartPolicy')}")

    new_networks = (new_attrs.get("NetworkSettings") or {}).get("Networks") or {}
    for net_name, net in _attached_networks(snap).items():
        got = new_networks.get(net_name)
        if got is None:
            problems.append(f"not on network {net_name}")
            continue
        names = set(got.get("Aliases") or []) | set(got.get("DNSNames") or [])
        missing = [a for a in network_aliases(snap, net) if a not in names]
        if missing:
            problems.append(f"network {net_name} lost aliases {missing}")
        want_ip = net.get("IPv4Address")
        if want_ip and (got.get("IPAMConfig") or {}).get("IPv4Address") != want_ip:
            problems.append(f"network {net_name} lost static IP {want_ip}")

    new_mounts = {m.get("Destination"): m for m in new_attrs.get("Mounts") or []}
    for m in snap.get("mounts") or []:
        got = new_mounts.get(m["Destination"])
        if got is None:
            problems.append(f"mount {m['Destination']} missing")
        elif m.get("Type") == "bind" and got.get("Source") != m.get("Source"):
            problems.append(f"mount {m['Destination']} source changed")
        elif m.get("Type") == "volume" and got.get("Name") != m.get("Name"):
            problems.append(f"mount {m['Destination']} volume changed")

    new_config = new_attrs.get("Config") or {}
    new_env = set(new_config.get("Env") or [])
    lost_env = [e.split("=", 1)[0] for e in own_env(snap) if e not in new_env]
    if lost_env:
        problems.append(f"env lost or changed: {sorted(lost_env)}")  # names only, never values
    new_labels = new_config.get("Labels") or {}
    lost_labels = [k for k, v in own_labels(snap).items() if new_labels.get(k) != v]
    if lost_labels:
        problems.append(f"labels lost: {sorted(lost_labels)}")
    old_config = snap.get("config") or {}
    image_config = snap.get("image_config") or {}
    for key in ("Healthcheck", "User", "ExposedPorts"):
        own = old_config.get(key)
        if key != "ExposedPorts" and own == image_config.get(key):
            continue  # came from the image; the new image decides
        if _empty(own) is not None and _empty(new_config.get(key)) != _empty(own):
            problems.append(f"Config.{key} {own!r} -> {new_config.get(key)!r}")
    return problems


def _seconds(value: Any, default_s: float) -> float:
    """Docker healthcheck durations are nanoseconds; 0/None means the default."""
    return (value / 1e9) if isinstance(value, int | float) and value > 0 else default_s


def health_timeout(attrs: dict) -> float:
    """How long to wait for a container's healthcheck to pass, from its own settings."""
    hc = (attrs.get("Config") or {}).get("Healthcheck") or {}
    if not hc or (hc.get("Test") or ["NONE"])[0] == "NONE":
        return HEALTH_TIMEOUT_MIN_S
    interval = _seconds(hc.get("Interval"), 30.0)
    timeout = _seconds(hc.get("Timeout"), 30.0)
    start = _seconds(hc.get("StartPeriod"), 0.0)
    retries = hc.get("Retries") or 3
    total = start + retries * (interval + timeout) + interval
    return max(HEALTH_TIMEOUT_MIN_S, min(HEALTH_TIMEOUT_MAX_S, total))


def wait_until_healthy(
    container,
    *,
    timeout: float | None = None,
    settle_s: float = NO_HEALTHCHECK_SETTLE_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, str]:
    """(healthy, why). With a healthcheck: until it reports healthy; without one:
    running for ``settle_s`` with no restart."""
    container.reload()
    limit = timeout if timeout is not None else health_timeout(container.attrs or {})
    start = clock()
    up_since: float | None = None
    while True:
        container.reload()
        attrs = container.attrs or {}
        state = attrs.get("State") or {}
        status = state.get("Status")
        if status in ("exited", "dead") or state.get("Restarting") or (attrs.get("RestartCount") or 0) > 0:
            return False, f"container {status or 'restarting'} (exit code {state.get('ExitCode')})"
        health = (state.get("Health") or {}).get("Status")
        now = clock()
        if health == "healthy":
            return True, "healthy"
        if health == "unhealthy":
            return False, "healthcheck reports unhealthy"
        if health is None and status == "running":
            up_since = up_since if up_since is not None else now
            if now - up_since >= settle_s:
                return True, f"running {settle_s:.0f}s (no healthcheck)"
        if now - start >= limit:
            return False, f"not healthy after {limit:.0f}s (status={status}, health={health})"
        sleep(1.0)


# --- the swap --------------------------------------------------------------

def _alert_once(notify: Callable[[dict], Any] | None, name: str, image: str, reason: str) -> None:
    """One error line and one admin notice per (container, image)."""
    key = (name, image)
    if key in _alerted:
        return
    _alerted.add(key)
    log.error(f"[recreate] update of {name} to {image[:19]} refused, old container kept: {reason}")
    if notify is None:
        return
    with suppress(Exception):
        notify({
            "title": f"Update of {name.removeprefix('sharedllm_')} rolled back",
            "body": f"The new image did not come up right, so the old container is still running: {reason}"[:500],
            "tag": f"admin:update:{name}",
            "priority": "high",
        })


def _remove(container) -> None:
    if container is not None:
        with suppress(Exception):
            container.remove(force=True)


def _stop_timeout(snap: dict) -> int:
    value = (snap.get("config") or {}).get("StopTimeout")
    return int(value) if isinstance(value, int) and value > 0 else 10


def recreate_from_snapshot(
    client: docker.DockerClient,
    container,
    snap: dict,
    new_image_id: str,
    *,
    prepare: Callable[[], Any] | None = None,
    notify: Callable[[dict], Any] | None = None,
    force_stop_first: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    health_timeout_s: float | None = None,
) -> dict:
    """Replace ``container`` (described by ``snap``) with one running ``new_image_id``.

    Raises RecreateFailed with the old container serving again when the new one
    loses part of the old config or does not become healthy.
    """
    name = snap["name"]
    stamp = int(time.time())
    reason = "requested" if force_stop_first else stop_first_reason(snap)
    strategy = "stop-first" if reason else "start-first"
    _say(f"{name} -> {new_image_id[:19]} ({strategy}{': ' + reason if reason else ''})")

    def check(new) -> None:
        problems = verify_carried_over(snap, new.attrs or {})
        if problems:
            raise RecreateFailed("config not carried over: " + "; ".join(problems))
        if prepare is not None:
            prepare()
        new.start()
        ok, why = wait_until_healthy(new, timeout=health_timeout_s, sleep=sleep, clock=clock)
        if not ok:
            raise RecreateFailed(why)
        _say(f"{new.name} is {why}")

    if strategy == "start-first":
        temp = f"{name}_next_{stamp}"
        new = None
        try:
            new = create_from_snapshot(client, snap, new_image_id, temp)
            check(new)
        except Exception as e:
            _remove(new)
            _alert_once(notify, name, new_image_id, str(e))
            if isinstance(e, RecreateFailed):
                raise
            raise RecreateFailed(str(e)) from e
        # The new one is healthy and answering on the same aliases; retire the old one.
        backup = f"{name}_old_{stamp}"
        try:
            container.stop(timeout=_stop_timeout(snap))
            container.rename(backup)
            new.rename(name)
        except Exception as e:
            _remove(new)
            with suppress(Exception):
                container.reload()
                if container.name != name:
                    container.rename(name)
                container.start()
            _alert_once(notify, name, new_image_id, f"swap failed: {e}")
            raise RecreateFailed(f"swap failed: {e}") from e
        _remove(container)
        with suppress(Exception):
            new.reload()
        _say(f"{name} now runs {new_image_id[:19]}")
        return {"recreated": True, "container": new, "strategy": strategy}

    # stop-first: the old one cannot run beside the new one.
    backup = f"{name}_backup_{stamp}"
    _say(f"stopping {name}")
    container.stop(timeout=_stop_timeout(snap))
    container.rename(backup)
    released = []
    for net_name, net in _attached_networks(snap).items():
        if net.get("IPv4Address") or net.get("IPv6Address"):
            with suppress(Exception):
                client.networks.get(net_name).disconnect(container)
                released.append(net_name)
    new = None
    try:
        new = create_from_snapshot(client, snap, new_image_id, name)
        check(new)
    except Exception as e:
        _remove(new)
        try:
            container.rename(name)
            if released:
                connect_container_networks(client, container, snap, only=released)
            container.start()
            _say(f"restored {name} on its old image")
        except Exception as fe:
            log.critical(f"[recreate] could not restore {backup} as {name}: {fe}")
        _alert_once(notify, name, new_image_id, str(e))
        if isinstance(e, RecreateFailed):
            raise
        raise RecreateFailed(str(e)) from e
    _remove(container)
    _say(f"{name} now runs {new_image_id[:19]}")
    return {"recreated": True, "container": new, "strategy": strategy}


def recreate_container(client: docker.DockerClient, container, new_image_id: str, **kwargs) -> dict:
    """Snapshot ``container`` while it is up, then replace it (see recreate_from_snapshot)."""
    snap = snapshot_container(container)
    return recreate_from_snapshot(client, container, snap, new_image_id, **kwargs)


def run_detached_self_recreate(payload_json: str) -> dict:
    """Entry point for the detached self-recreate child process.

    Waits briefly for the parent HTTP response to finish, then replaces the
    container stop-first (two control planes must not run at once), with the
    same checks and rollback as any other service.
    """
    import json

    payload: dict[str, Any] = json.loads(payload_json)
    snap = payload["snap"]
    time.sleep(1.5)
    d = docker.from_env()
    notify = None
    with suppress(Exception):
        from services.control_plane.admin_notice import notify_admins as notify
    old = d.containers.get(snap["name"])
    try:
        result = recreate_from_snapshot(d, old, snap, payload["new_image_id"], notify=notify, force_stop_first=True)
        return {"ok": True, "container_name": snap["name"], "strategy": result["strategy"]}
    except Exception:
        import traceback

        traceback.print_exc()
        raise
