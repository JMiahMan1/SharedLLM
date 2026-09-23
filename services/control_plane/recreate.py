"""Container recreate helpers shared by in-process and detached self-update.

The detached control-plane child used to inline a second copy of alias/create/
connect logic. Both paths must use these functions so network aliases and IP
handling stay identical.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

import docker


def network_aliases(snap: dict, net_config: dict, new_id: str) -> list[str]:
    """Compose DNS aliases for a recreated container (filter auto container IDs)."""
    aliases = [
        a
        for a in (net_config.get("Aliases") or [])
        if a
        and a != (snap.get("id") or "")[:12]
        and a != new_id[:12]
        and not re.fullmatch(r"[0-9a-f]{12}", a)
    ]
    name = (snap.get("name") or "").lstrip("/")
    short = name.removeprefix("sharedllm_")
    service = snap.get("compose_service") or ""
    for extra in (name, short, service):
        if extra and extra not in aliases:
            aliases.append(extra)
    return list(dict.fromkeys(aliases))


def create_from_snapshot(
    client: docker.DockerClient,
    snap: dict,
    new_image_id: str,
    name: str,
):
    """Low-level create using a pre-stop snapshot of Config/HostConfig."""
    host_config = dict(snap.get("host_config") or {})
    resp = client.api.create_container(
        image=new_image_id,
        name=name,
        command=snap.get("cmd"),
        entrypoint=snap.get("entrypoint"),
        environment=snap.get("env"),
        user=snap.get("user"),
        working_dir=snap.get("working_dir"),
        labels=snap.get("config", {}).get("Labels"),
        host_config=host_config,
        ports=snap.get("exposed_ports") or None,
    )
    return client.containers.get(resp["Id"])


def connect_container_networks(
    client: docker.DockerClient,
    container,
    snap: dict,
    *,
    release_from=None,
) -> None:
    """Attach container to snapshotted networks with aliases; optional IP pin.

    release_from: optional prior container to disconnect first so its IPv4 is free.
    """
    host_config = snap.get("host_config") or {}
    network_mode = host_config.get("NetworkMode") or ""
    for net_name, net_config in (snap.get("networks") or {}).items():
        if net_name == "bridge" and network_mode in ("default", ""):
            continue
        if net_name == "host" and network_mode == "host":
            continue
        try:
            network = client.networks.get(net_name)
        except Exception as ne:
            print(f"[recreate] Network not found {net_name}: {ne}")
            continue
        if release_from is not None:
            with suppress(Exception):
                network.disconnect(release_from)
        aliases = network_aliases(snap, net_config, container.id)
        ipv4 = (net_config.get("IPv4Address") or "").strip() or None
        connected = False
        if ipv4:
            try:
                network.connect(container, aliases=aliases, ipv4_address=ipv4)
                connected = True
                print(
                    f"[recreate] Connected {container.name} to {net_name} "
                    f"aliases={aliases} ipv4={ipv4}"
                )
            except Exception as ne:
                print(f"[recreate] Pinned IP connect failed for {net_name}: {ne}")
        if not connected:
            try:
                network.connect(container, aliases=aliases)
                print(
                    f"[recreate] Connected {container.name} to {net_name} "
                    f"aliases={aliases} ipv4=auto"
                )
            except Exception as ne2:
                print(f"[recreate] Connect failed for {net_name}: {ne2}")


def run_detached_self_recreate(payload_json: str) -> dict:
    """Entry point for the detached self-recreate child process.

    Waits briefly for the parent HTTP response to finish, then stop/rename/
    create/connect/start using the shared helpers above.
    """
    import json
    import time

    payload: dict[str, Any] = json.loads(payload_json)
    snap = payload["snap"]
    new_image_id = payload["new_image_id"]
    backup = payload["backup_name"]
    name = snap["name"]

    time.sleep(1.5)
    d = docker.from_env()
    try:
        old = d.containers.get(name)
        print(f"stopping {name}")
        old.stop(timeout=10)
        old.rename(backup)
        backup_ctr = d.containers.get(backup)

        # Free IPs held by backup before connecting the new container
        for net_name in list((snap.get("networks") or {})):
            with suppress(Exception):
                d.networks.get(net_name).disconnect(backup_ctr)

        new = create_from_snapshot(d, snap, new_image_id, name)
        connect_container_networks(d, new, snap, release_from=backup_ctr)
        new.start()
        with suppress(Exception):
            backup_ctr.remove(force=True)
        print(f"recreate complete {name}")
        return {"ok": True, "container_name": name}
    except Exception:
        import traceback

        traceback.print_exc()
        with suppress(Exception):
            d = docker.from_env()
            b = d.containers.get(backup)
            b.rename(name)
            b.start()
            print("restored backup")
        raise
