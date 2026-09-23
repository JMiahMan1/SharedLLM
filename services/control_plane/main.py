import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager, suppress

import requests
from docker.errors import NotFound
from fastapi import Depends, FastAPI, Header, HTTPException

import docker
from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET, WORKSPACE_RUNTIME_SVC_URL

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("control_plane")

from services.shared.info_endpoint import info_router

TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\)|^\s+File ", re.MULTILINE)

CONTAINER_PREFIX = "wsbox-"
NETWORK_PREFIX = "wsnet-"
# How often (seconds) the reaper sweeps for orphaned sandbox containers.
SANDBOX_REAP_INTERVAL = int(__import__("os").getenv("SANDBOX_REAP_INTERVAL", "3600"))


def _slugify_workspace_id(workspace_id: str) -> str:
    """Mirror workspace_sandbox._slug so container-name suffixes match ids."""
    s = re.sub(r"[^a-z0-9]+", "-", (workspace_id or "").strip().lower()).strip("-")
    return s or "workspace"


def _known_workspace_slugs() -> set[str] | None:
    """Slug set of every registered workspace id, or None if lookup failed.

    Returns None on any error so the reaper can skip the sweep entirely —
    failing closed (keep everything) instead of destroying live sandboxes.
    """
    try:
        resp = requests.get(
            f"{WORKSPACE_RUNTIME_SVC_URL}/workspaces/internal/ids",
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10.0,
        )
        if resp.status_code != 200:
            log.warning(
                f"[reap] workspace id list returned {resp.status_code}; skipping sweep"
            )
            return None
        ids = resp.json().get("ids") or []
        return {_slugify_workspace_id(str(i)) for i in ids}
    except Exception as e:
        log.warning(f"[reap] workspace id list failed: {e}")
        return None  # assume alive on error to avoid destroying a live workspace


def _reap_orphan_sandboxes() -> int:
    """Remove wsbox-* containers (and wsnet-* networks) with no DB workspace.

    Catches sandboxes leaked by deletes that happened before teardown was wired
    into delete_workspace, plus any container the runtime failed to clean up.
    """
    if client is None:
        return 0
    removed = 0
    try:
        containers = client.containers.list(all=True, filters={"name": CONTAINER_PREFIX})
    except Exception as e:
        log.warning(f"[reap] list failed: {e}")
        return 0
    known_slugs = _known_workspace_slugs()
    if known_slugs is None:
        return 0
    for c in containers:
        name = c.name
        if not name.startswith(CONTAINER_PREFIX):
            continue
        ws_slug = name[len(CONTAINER_PREFIX):]
        if ws_slug in known_slugs:
            continue
        log.info(f"[reap] removing orphaned sandbox container {name}")
        try:
            with __import__("contextlib").suppress(Exception):
                if c.status == "running":
                    c.stop(timeout=5)
            c.remove(force=True)
        except Exception as e:
            log.warning(f"[reap] failed to remove {name}: {e}")
            continue
        try:
            net = client.networks.get(f"{NETWORK_PREFIX}{ws_slug}")
            net.remove()
        except NotFound:
            pass
        except Exception as e:
            log.warning(f"[reap] failed to remove network for {ws_slug}: {e}")
        removed += 1
    if removed:
        log.info(f"[reap] removed {removed} orphaned sandbox container(s)")
    return removed


def _reaper_loop() -> None:
    while True:
        time.sleep(SANDBOX_REAP_INTERVAL)
        try:
            _reap_orphan_sandboxes()
        except Exception as e:  # never let the reaper thread die
            log.error(f"[reap] unexpected error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=_reaper_loop, name="sandbox-reaper", daemon=True)
    t.start()
    log.info(f"Sandbox reaper started (interval={SANDBOX_REAP_INTERVAL}s)")
    yield


app = FastAPI(title="Control Plane Service", lifespan=lifespan)
app.include_router(info_router)

# Initialize Docker client
try:
    client = docker.from_env()
    log.info("Docker client initialized successfully.")
except Exception as e:
    import traceback
    log.error(f"Failed to initialize docker client: {e}")
    log.error(traceback.format_exc())
    client = None


# ─── Background Pull Tracking ──────────────────────────────────────────────────
# Tracks non-blocking image pulls so the UI can poll progress without
# holding an HTTP connection open. Keyed by compose service name.
_pull_status: dict[str, dict] = {}
_pull_lock = threading.Lock()


def _pull_image_background(service_name: str, image_tag: str, current_image_id: str):
    """Pull an image in a background thread, recording status for polling."""
    with _pull_lock:
        _pull_status[service_name] = {
            "status": "pulling",
            "progress": "Starting pull...",
            "image": image_tag,
            "current_image_id": current_image_id,
            "started_at": time.time(),
            "completed_at": None,
            "new_image_id": None,
            "error": None,
        }

    try:
        log.info(f"[pull] Background pull started for {service_name}: {image_tag}")
        # Stream pull output to capture layer progress
        for line in client.api.pull(image_tag, stream=True, decode=True):
            status = line.get("status", "")
            progress = line.get("progress", "")
            if progress:
                display = f"{status}: {progress}"
            elif status:
                display = status
            else:
                display = str(line)
            with _pull_lock:
                _pull_status[service_name]["progress"] = display
                _pull_status[service_name]["last_update"] = time.time()

        new_image_id = client.images.get(image_tag).id
        updated = new_image_id != current_image_id
        with _pull_lock:
            _pull_status[service_name]["status"] = "completed"
            _pull_status[service_name]["progress"] = "Pull completed"
            _pull_status[service_name]["completed_at"] = time.time()
            _pull_status[service_name]["new_image_id"] = new_image_id
            _pull_status[service_name]["updated"] = updated
        log.info(f"[pull] Background pull completed for {service_name}: updated={updated}")
    except Exception as e:
        log.error(f"[pull] Background pull failed for {service_name}: {e}")
        with _pull_lock:
            _pull_status[service_name]["status"] = "failed"
            _pull_status[service_name]["progress"] = str(e)
            _pull_status[service_name]["completed_at"] = time.time()
            _pull_status[service_name]["error"] = str(e)


def _fix_volume_permissions(container) -> list[str]:
    """Fix volume permissions to avoid permission denied errors on recreate.

    Mirrors the deploy.sh volume permission guard: ensures named volumes
    are owned by the current process UID/GID so the recreated container
    (running as PUID:PGID) can read/write its data.
    """
    import os

    puid = os.getuid()
    pgid = os.getgid()
    fixed = []
    try:
        mounts = container.attrs.get("Mounts", [])
        for mount in mounts:
            if mount.get("Type") == "volume":
                vol_name = mount.get("Name", "")
                if not vol_name:
                    continue
                try:
                    log.info(f"[permissions] Fixing volume {vol_name} -> {puid}:{pgid}")
                    client.containers.run(
                        "busybox",
                        command=f"sh -c 'chown -R {puid}:{pgid} /data && chmod -R 775 /data'",
                        volumes={vol_name: {"bind": "/data", "mode": "rw"}},
                        remove=True,
                    )
                    fixed.append(vol_name)
                except Exception as ve:
                    log.warning(f"[permissions] Failed to fix volume {vol_name}: {ve}")
    except Exception as e:
        log.warning(f"[permissions] Error checking volumes for {container.name}: {e}")
    return fixed


# ─── Auth Dependency ───────────────────────────────────────────────────────────

def verify_internal_secret(x_internal_secret: str = Header(..., alias="X-Internal-Secret")):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Invalid internal secret")
    return True


# ─── Health ────────────────────────────────────────────────────────────────────


START_TIME = time.time()

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "control_plane",
        "git_sha": os.getenv("GIT_SHA", "unknown"),
        "start_time": START_TIME
    }

@app.get("/control_plane/health")
def health_prefixed():
    return health()


# ─── Container Management ──────────────────────────────────────────────────────

def _format_uptime(uptime_seconds: float) -> str:
    """Format uptime seconds into a human-readable duration."""
    delta = int(uptime_seconds)
    days, remainder = divmod(delta, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if not parts:
        return "<1m"
    return " ".join(parts)


def _get_image_pull_time(container) -> str | None:
    """Extract the image pull time from container inspect data."""
    try:
        inspect = container.attrs
        created = inspect.get("Created") or inspect.get("CreatedTime")
        if created:
            return created
    except Exception:
        pass
    return None


def _get_container_info(container):
    """Extract comprehensive info from a container."""
    inspect = container.attrs
    state = inspect.get("State", {})
    health = state.get("Health") or {}
    started_at = state.get("StartedAt")
    finished_at = state.get("FinishedAt")
    exit_code = state.get("ExitCode", -1)

    # Calculate uptime seconds
    uptime_seconds = None
    if started_at:
        try:
            from datetime import datetime
            started_at_clean = started_at[:-1] + "+00:00" if started_at.endswith("Z") else started_at
            start_dt = datetime.fromisoformat(started_at_clean)
            uptime_seconds = time.time() - start_dt.timestamp()
            if uptime_seconds < 0:
                uptime_seconds = None
        except Exception:
            pass

    # Health status
    health_status = None
    if health:
        health_status = health.get("Status")

    # Get image tags
    image_tags = []
    if container.image and container.image.tags:
        image_tags = list(container.image.tags)

    return {
        "name": container.name,
        "status": container.status,
        "image": image_tags[0] if image_tags else "unknown",
        "image_id": container.image.id if container.image else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "uptime_seconds": round(uptime_seconds) if uptime_seconds is not None else None,
        "uptime": _format_uptime(uptime_seconds) if uptime_seconds is not None else None,
        "health": health_status,
        "health_status": health_status,
        "pid": state.get("Pid"),
        "restart_count": state.get("RestartCount", 0),
        "image_pull_time": _get_image_pull_time(container),
        "memory_usage": state.get("MemoryStats", {}).get("usage") if state.get("MemoryStats") else None,
    }


@app.get("/api/containers", dependencies=[Depends(verify_internal_secret)])
def list_containers():
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        containers = client.containers.list(all=True)
        # Only expose sharedllm_ prefixed containers for security
        results = []
        for c in containers:
            if c.name and c.name.startswith("sharedllm_"):
                results.append(_get_container_info(c))
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health", dependencies=[Depends(verify_internal_secret)])
def system_health():
    """Aggregated health status of all sharedllm services."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        containers = client.containers.list(all=True)
        services = []
        running = 0
        stopped = 0
        unhealthy = 0
        for c in containers:
            if c.name and c.name.startswith("sharedllm_"):
                info = _get_container_info(c)
                services.append(info)
                if c.status == "running":
                    running += 1
                else:
                    stopped += 1
                if info.get("health_status") == "unhealthy":
                    unhealthy += 1

        # Get control plane git info and uptime
        control_plane_info = {
            "status": "running",
            "git_sha": "unknown",
            "start_time": START_TIME,
            "uptime": _format_uptime(time.time() - START_TIME)
        }
        try:
            from services.shared.info_endpoint import _get_git_commit
            control_plane_info["git_sha"] = _get_git_commit()
        except Exception:
            pass

        return {
            "total_services": len(services),
            "running": running,
            "stopped": stopped,
            "unhealthy": unhealthy,
            "control_plane": control_plane_info,
            "services": services,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status/{service_name}", dependencies=[Depends(verify_internal_secret)])
def get_service_status(service_name: str):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        container = client.containers.get(service_name)
        return _get_container_info(container)
    except NotFound:
        raise HTTPException(status_code=404, detail="Service not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _resolve_container(service_name: str):
    """
    Finds a container matching service_name.
    Supports:
      1. Base service name (e.g. "gateway")
      2. Full container name (e.g. "sharedllm_gateway_1" or "sharedllm_gateway")
    """
    if not client:
        return None

    # Try exact match first
    try:
        return client.containers.get(service_name)
    except Exception:
        pass

    # Try finding by com.docker.compose.service label
    for c in client.containers.list(all=True):
        if c.labels.get("com.docker.compose.service") == service_name:
            return c

    # Try constructing name variations
    for name_var in [f"sharedllm_{service_name}_1", f"sharedllm_{service_name}", service_name]:
        try:
            return client.containers.get(name_var)
        except Exception:
            pass

    return None


def _verify_sharedllm_container(container) -> bool:
    """Ensure the target container belongs to the sharedllm project."""
    if not container:
        return False
    return (
        container.name.startswith("sharedllm_") or
        container.labels.get("com.docker.compose.project") == "sharedllm"
    )


def _infer_ghcr_image_ref(service_name: str) -> str | None:
    """
    Construct the GHCR image reference for a sharedllm service.

    Images are published as ghcr.io/{GHCR_NAMESPACE}/sharedllm-{service}:latest
    where GHCR_NAMESPACE defaults to 'jmiahman1' (matching the CI workflow).
    Returns None if the service name doesn't look like a sharedllm service.
    """
    # Strip common prefixes/suffixes from the service name
    clean_name = service_name
    if clean_name.startswith("sharedllm_"):
        clean_name = clean_name[len("sharedllm_"):]
    if clean_name.endswith("_1"):
        clean_name = clean_name[:-2]

    # Only infer for known sharedllm services
    known_services = {"gateway", "identity", "rag", "storage", "logging",
                      "workspace_runtime", "control_plane", "geo", "ui", "caddy"}
    if clean_name not in known_services:
        return None

    namespace = os.getenv("GHCR_NAMESPACE", "jmiahman1")
    return f"ghcr.io/{namespace}/sharedllm-{clean_name}:latest"


def _snapshot_container_config(container) -> dict:
    """Capture Config/HostConfig/network membership while the container is still up.

    attrs after stop/rename can have empty NetworkSettings (None Aliases/IP),
    which previously left recreated containers with Networks={}.
    """
    container.reload()
    attrs = container.attrs or {}
    config = dict(attrs.get("Config") or {})
    host_config = dict(attrs.get("HostConfig") or {})
    networks_raw = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    networks: dict[str, dict] = {}
    for net_name, net_config in networks_raw.items():
        if not isinstance(net_config, dict):
            continue
        networks[net_name] = {
            "Aliases": list(net_config.get("Aliases") or []),
            "IPv4Address": (
                (net_config.get("IPAMConfig") or {}).get("IPv4Address")
                or net_config.get("IPAddress")
                or ""
            ),
        }
    labels = config.get("Labels") or {}
    compose_service = labels.get("com.docker.compose.service") or ""
    return {
        "name": container.name,
        "id": container.id,
        "config": config,
        "host_config": host_config,
        "networks": networks,
        "exposed_ports": config.get("ExposedPorts"),
        "compose_service": compose_service,
        "env": config.get("Env") or [],
        "cmd": config.get("Cmd"),
        "entrypoint": config.get("Entrypoint"),
        "user": config.get("User"),
        "working_dir": config.get("WorkingDir"),
    }


def _network_aliases(snap: dict, net_name: str, net_config: dict, new_id: str) -> list[str]:
    """Compose DNS aliases for a recreated container (filter auto container IDs)."""
    aliases = [
        a for a in (net_config.get("Aliases") or [])
        if a and a != (snap.get("id") or "")[:12] and a != new_id[:12]
        and not re.fullmatch(r"[0-9a-f]{12}", a)
    ]
    name = (snap.get("name") or "").lstrip("/")
    short = name.removeprefix("sharedllm_")
    service = snap.get("compose_service") or ""
    for extra in (name, short, service):
        if extra and extra not in aliases:
            aliases.append(extra)
    # Dedup preserve order
    return list(dict.fromkeys(aliases))


def _connect_container_networks(container, snap: dict, *, release_from=None) -> None:
    """Attach container to snapshotted networks with aliases; optional IP pin.

    release_from: optional prior container to disconnect first so its IPv4 is free.
    """
    if not client:
        return
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
            log.warning(f"[recreate] Network not found {net_name}: {ne}")
            continue
        if release_from is not None:
            with suppress(Exception):
                network.disconnect(release_from)
        aliases = _network_aliases(snap, net_name, net_config, container.id)
        ipv4 = (net_config.get("IPv4Address") or "").strip() or None
        connected = False
        if ipv4:
            try:
                network.connect(container, aliases=aliases, ipv4_address=ipv4)
                connected = True
                log.info(f"[recreate] Connected {container.name} to {net_name} aliases={aliases} ipv4={ipv4}")
            except Exception as ne:
                log.warning(f"[recreate] Pinned IP connect failed for {net_name}: {ne}")
        if not connected:
            try:
                network.connect(container, aliases=aliases)
                log.info(f"[recreate] Connected {container.name} to {net_name} aliases={aliases} ipv4=auto")
            except Exception as ne2:
                log.error(f"[recreate] Connect failed for {net_name}: {ne2}")


def _create_from_snapshot(snap: dict, new_image_id: str, name: str):
    """Low-level create using a pre-stop snapshot of Config/HostConfig."""
    host_config = dict(snap.get("host_config") or {})
    # Never inherit a pinned NetworkMode that would skip our explicit connect.
    # Docker still needs the field for bridge/host modes.
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


def _is_self_container(container) -> bool:
    """True when this process runs inside the container being recreated."""
    hostname = os.environ.get("HOSTNAME", "")
    return bool(
        hostname
        and (
            hostname == container.name
            or hostname == container.id
            or hostname == container.id[:12]
            or hostname.startswith(container.id[:12])
        )
    )


def _recreate_self_detached(snap: dict, new_image_id: str) -> dict:
    """Recreate this container from a detached child so we can finish the HTTP response.

    Stopping ourselves in-process previously SIGKILL'd mid-recreate (exit 137)
    and left control_plane down with no new container.
    """
    import json
    import subprocess
    import sys

    script = r'''
import json, sys, time, traceback
import docker
from contextlib import suppress

payload = json.loads(sys.argv[1])
snap = payload["snap"]
new_image_id = payload["new_image_id"]
name = snap["name"]
backup = payload["backup_name"]
log = print

def aliases_for(net_config, new_id):
    out = []
    for a in (net_config.get("Aliases") or []):
        if a and a != (snap.get("id") or "")[:12] and a != new_id[:12]:
            out.append(a)
    short = name.lstrip("/").removeprefix("sharedllm_")
    for extra in (name.lstrip("/"), short, snap.get("compose_service") or ""):
        if extra and extra not in out:
            out.append(extra)
    return list(dict.fromkeys(out))

def main():
    d = docker.from_env()
    time.sleep(1.5)  # let the parent finish the HTTP response and exit
    old = d.containers.get(name)
    log("stopping", name)
    old.stop(timeout=10)
    old.rename(backup)
    backup_ctr = d.containers.get(backup)
    host_config = dict(snap.get("host_config") or {})
    networks = snap.get("networks") or {}
    # Free IPs held by backup before connecting the new container
    for net_name in list(networks.keys()):
        with suppress(Exception):
            d.networks.get(net_name).disconnect(backup_ctr)
    resp = d.api.create_container(
        image=new_image_id,
        name=name,
        command=snap.get("cmd"),
        entrypoint=snap.get("entrypoint"),
        environment=snap.get("env"),
        user=snap.get("user"),
        working_dir=snap.get("working_dir"),
        labels=(snap.get("config") or {}).get("Labels"),
        host_config=host_config,
        ports=snap.get("exposed_ports") or None,
    )
    new = d.containers.get(resp["Id"])
    network_mode = host_config.get("NetworkMode") or ""
    for net_name, net_config in networks.items():
        if net_name == "bridge" and network_mode in ("default", ""):
            continue
        if net_name == "host" and network_mode == "host":
            continue
        try:
            net = d.networks.get(net_name)
        except Exception as e:
            log("missing net", net_name, e)
            continue
        als = aliases_for(net_config, new.id)
        ipv4 = (net_config.get("IPv4Address") or "").strip() or None
        ok = False
        if ipv4:
            try:
                net.connect(new, aliases=als, ipv4_address=ipv4)
                ok = True
                log("connected", net_name, als, ipv4)
            except Exception as e:
                log("pinned ip failed", net_name, e)
        if not ok:
            try:
                net.connect(new, aliases=als)
                log("connected auto", net_name, als)
            except Exception as e:
                log("connect failed", net_name, e)
    new.start()
    with suppress(Exception):
        backup_ctr.remove(force=True)
    log("recreate complete", name)

try:
    main()
except Exception:
    traceback.print_exc()
    # Best-effort restore
    try:
        d = docker.from_env()
        with suppress(Exception):
            b = d.containers.get(payload["backup_name"])
            b.rename(name)
            b.start()
            log("restored backup")
    except Exception:
        pass
    sys.exit(1)
'''
    backup_name = f"{snap['name']}_backup_{int(time.time())}"
    payload = json.dumps({"snap": snap, "new_image_id": new_image_id, "backup_name": backup_name})
    # Detached: survives parent exit when we stop ourselves.
    subprocess.Popen(
        [sys.executable, "-c", script, payload],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    log.info(f"[recreate] Scheduled detached self-recreate for {snap['name']} -> {new_image_id}")
    return {"recreated": True, "pending": True, "container_name": snap["name"], "backup_name": backup_name}


def _recreate_container(container, new_image_id: str):
    """
    Recreates a container with the new image, preserving ports, volumes, env,
    and custom networks/aliases.

    Config is snapshotted BEFORE stop. Self-recreate (control_plane updating
    itself) is handed off to a detached child so the API process is not killed
    mid-flight (previously exit 137 with the stack left down).
    """
    import time
    if not client:
        raise ValueError("Docker client not initialized")

    snap = _snapshot_container_config(container)
    old_name = snap["name"]
    backup_name = f"{old_name}_backup_{int(time.time())}"

    if _is_self_container(container):
        return _recreate_self_detached(snap, new_image_id)

    # 1. Stop the old container
    log.info(f"[recreate] Stopping old container {old_name}...")
    container.stop(timeout=10)

    # 2. Rename old container to backup name
    log.info(f"[recreate] Renaming {old_name} to {backup_name}...")
    container.rename(backup_name)

    new_container = None
    try:
        # 3. Create new container from the pre-stop snapshot
        log.info(f"[recreate] Creating new container {old_name} with image {new_image_id}...")
        new_container = _create_from_snapshot(snap, new_image_id, old_name)

        # 4. Free the backup's network endpoints, then attach the new container
        #    with original aliases/IPs (snapshot already captured while healthy).
        _connect_container_networks(new_container, snap, release_from=container)

        # 5b. Fix volume permissions before starting (mirrors deploy.sh guard)
        fixed_vols = _fix_volume_permissions(container)
        if fixed_vols:
            log.info(f"[recreate] Fixed permissions for volumes: {fixed_vols}")

        # 6. Start the new container
        log.info(f"[recreate] Starting new container {new_container.name}...")
        new_container.start()

        # Verify we actually landed on the intended networks
        new_container.reload()
        attached = (new_container.attrs.get("NetworkSettings") or {}).get("Networks") or {}
        if snap.get("networks") and not any(
            (attached.get(n) or {}).get("IPAddress")
            for n in snap["networks"]
            if n not in ("bridge", "host")
        ):
            log.warning(f"[recreate] {old_name} started with no network IP; reconnecting...")
            _connect_container_networks(new_container, snap, release_from=None)

        # 7. Success: remove backup container
        log.info(f"[recreate] Recreate successful. Removing backup container {backup_name}...")
        try:
            container.remove(force=True)
        except Exception as re:
            log.warning(f"[recreate] Failed to remove backup container {backup_name}: {re}")

        return {"recreated": True, "container": new_container}

    except Exception as e:
        log.error(f"[recreate] Failed to recreate container {old_name}: {e}. Falling back to old container...")
        # Clean up new container if it was created
        if new_container:
            with suppress(Exception):
                new_container.remove(force=True)
        # Restore backup container
        try:
            container.rename(old_name)
            container.start()
            _connect_container_networks(container, snap, release_from=None)
            log.info(f"[recreate] Fallback successful. Old container {old_name} restored and started.")
        except Exception as fe:
            log.critical(f"[recreate] Critical failure: Could not restore backup container {backup_name}: {fe}")
        raise e


@app.post("/api/restart/{service_name}", dependencies=[Depends(verify_internal_secret)])
def restart_service(service_name: str, recreate: bool = False):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    container = _resolve_container(service_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container for service '{service_name}' not found")

    if not _verify_sharedllm_container(container):
        raise HTTPException(status_code=400, detail=f"Container '{container.name}' is not part of the sharedllm stack")

    try:
        # Determine if recreation is needed
        image_tags = container.image.tags if container.image else []
        image_tag = image_tags[0] if image_tags else None

        should_recreate = recreate
        local_latest_image_id = None
        if image_tag:
            try:
                local_latest_image = client.images.get(image_tag)
                local_latest_image_id = local_latest_image.id
                if local_latest_image_id != container.image.id:
                    should_recreate = True
                    log.info(
                        f"[restart] Container {container.name} is running image {container.image.id}, "
                        f"but latest local tag {image_tag} is {local_latest_image_id}. Recreating..."
                    )
            except Exception as ie:
                log.warning(f"[restart] Failed to compare image IDs for {container.name}: {ie}")

        if should_recreate and (local_latest_image_id or container.image):
            target_image_id = local_latest_image_id or container.image.id
            _recreate_container(container, target_image_id)
            return {
                "status": "SUCCESS",
                "message": f"Container {container.name} recreated and updated to image {target_image_id[:12]} successfully"
            }
        else:
            container.restart()
            return {"status": "SUCCESS", "message": f"Container {container.name} restarted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/containers/{service_name}", dependencies=[Depends(verify_internal_secret)])
def delete_container(service_name: str):
    """Remove a stopped container. Only for stopped containers."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    container = _resolve_container(service_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container for service '{service_name}' not found")

    if not _verify_sharedllm_container(container):
        raise HTTPException(status_code=400, detail=f"Container '{container.name}' is not part of the sharedllm stack")

    try:
        if container.status == "running":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete running container {container.name}. Stop it first."
            )
        container.remove()
        return {"status": "SUCCESS", "message": f"Container {container.name} removed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/services/updates", dependencies=[Depends(verify_internal_secret)])
def check_all_updates():
    """
    Check all sharedllm services for available image updates.

    Strategy: compare the locally running image's RepoDigest (sha256) against the
    remote registry manifest digest fetched via the OCI Distribution HTTP API.
    This never pulls an image — it only does a HEAD/GET request to the registry.

    For GHCR images, authentication uses (in order):
    1. github_token for user ID 1 from the Identity service
    2. GHCR_TOKEN environment variable
    3. GITHUB_TOKEN environment variable
    """
    import json as _json
    import urllib.request

    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    # GHCR auth token (GitHub PAT with read:packages scope)
    # First, try to fetch user ID 1's github_token from identity service
    ghcr_token = ""
    try:
        identity_svc_url = IDENTITY_SVC_URL
        req_data = _json.dumps({"user_id": 1}).encode("utf-8")
        req = urllib.request.Request(
            f"{identity_svc_url}/api/resolve",
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "X-Internal-Secret": INTERNAL_SECRET
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp_data = _json.loads(resp.read().decode("utf-8"))
            ghcr_token = resp_data.get("github_token") or ""
            if ghcr_token:
                log.info("[updates] Successfully resolved GitHub token for user ID 1 from identity")
    except Exception as e:
        log.warning(f"[updates] Failed to resolve GitHub token for user ID 1: {e}")

    # Fallback to GHCR_TOKEN environment variable
    if not ghcr_token:
        ghcr_token = os.getenv("GHCR_TOKEN", "")

    # Fallback to GITHUB_TOKEN environment variable
    if not ghcr_token:
        ghcr_token = os.getenv("GITHUB_TOKEN", "")

    # Decide whether remote digest checks are viable BEFORE looping over every
    # container. Without a usable GHCR token (or with GHCR unreachable), each
    # _get_remote_digest call would block on a failing 401/TLS round-trip
    # (~15s each) and this endpoint would hang ~60s, triggering the browser's
    # ECONNABORTED. We still report the locally-running services (fast, no
    # network) so the Admin Updates page stays populated — we just can't detect
    # whether a newer image exists upstream.
    remote_error = None
    remote_available = False
    if not ghcr_token:
        log.warning(
                 "[updates] No GHCR/GitHub token available — skipping remote digest "
                 "checks (set GHCR_TOKEN or GITHUB_TOKEN in .env to enable update detection)."
        )
        remote_error = "ghcr_auth_unavailable"
    else:
        def _ghcr_auth_ok(token: str) -> bool:
            import base64

            url = "https://ghcr.io/v2/"
            req = urllib.request.Request(url, method="GET")
            token_b64 = base64.b64encode(f":{token}".encode()).decode()
            req.add_header("Authorization", f"Basic {token_b64}")
            try:
                with urllib.request.urlopen(req, timeout=8) as resp:
                    # 200 = authenticated and usable; 401/403 = unusable token.
                    return resp.status == 200
            except urllib.error.HTTPError:
                # 401/403 = token rejected -> skip. Any other HTTP error is
                # inconclusive but treated as unusable to avoid slow timeouts.
                return False
            except Exception:
                # Registry unreachable (timeout/DNS/TLS): cannot verify digests.
                return False

        if _ghcr_auth_ok(ghcr_token):
            remote_available = True
        else:
            log.warning(
                "[updates] GHCR token present but authentication failed/unreachable "
                "(401/403 or registry down) — skipping remote digest checks."
            )
            remote_error = "ghcr_auth_unavailable"

    def _get_remote_digest(image_ref: str) -> str | None:
        """
        Fetch the manifest digest for an image reference from its registry
        without pulling. Returns the digest string (e.g. sha256:abc...) or None.
        """
        # Parse registry / repository / tag from image ref
        # Expected format: ghcr.io/owner/repo:tag  OR  owner/repo:tag  OR  image:tag
        tag = "latest"
        if ":" in image_ref:
            image_ref, tag = image_ref.rsplit(":", 1)

        if image_ref.startswith("ghcr.io/"):
            registry = "ghcr.io"
            repo = image_ref[len("ghcr.io/"):]
        elif "/" in image_ref and image_ref.split("/")[0].count(".") > 0:
            # other registry host e.g. registry.example.com/repo
            parts = image_ref.split("/", 1)
            registry = parts[0]
            repo = parts[1]
        else:
            # Docker Hub or plain image name
            registry = "registry-1.docker.io"
            repo = f"library/{image_ref}" if "/" not in image_ref else image_ref

        # OCI-compliant manifest accept types (prefer multi-arch index)
        accept_types = (
            "application/vnd.oci.image.index.v1+json,"
            "application/vnd.docker.distribution.manifest.list.v2+json,"
            "application/vnd.oci.image.manifest.v1+json,"
            "application/vnd.docker.distribution.manifest.v2+json"
        )

        url = f"https://{registry}/v2/{repo}/manifests/{tag}"

        # Build auth header
        auth_header = None
        if registry == "ghcr.io" and ghcr_token:
            import base64
            token_b64 = base64.b64encode(f":{ghcr_token}".encode()).decode()
            auth_header = f"Basic {token_b64}"

        req = urllib.request.Request(url, method="HEAD")
        req.add_header("Accept", accept_types)
        if auth_header:
            req.add_header("Authorization", auth_header)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                # The Docker-Content-Digest header is the canonical digest
                digest = resp.headers.get("Docker-Content-Digest")
                return digest
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                log.warning(f"[updates] Auth error for {url}: {e.code}. Set GHCR_TOKEN env var.")
            else:
                log.warning(f"[updates] HTTP {e.code} fetching manifest for {image_ref}:{tag}")
            return None
        except Exception as e:
            log.warning(f"[updates] Failed to fetch manifest for {image_ref}:{tag}: {e}")
            return None

    def _get_local_digest(image) -> str | None:
        """Extract the repo digest from a locally cached image object."""
        try:
            attrs = image.attrs or {}
            repo_digests = attrs.get("RepoDigests", [])
            if repo_digests:
                # Format: ghcr.io/owner/repo@sha256:abc...
                digest_part = repo_digests[0].split("@")[-1]
                return digest_part
        except Exception:
            pass
        return None

    try:
        containers = client.containers.list(all=True)
        updates = []

        for container in containers:
            if not container.name or not container.name.startswith("sharedllm_"):
                continue

            # Determine base service name
            compose_service = container.labels.get("com.docker.compose.service")
            if compose_service:
                svc_name = compose_service
            else:
                svc_name = container.name
                if svc_name.startswith("sharedllm_"):
                    svc_name = svc_name[len("sharedllm_"):]
                if svc_name.endswith("_1"):
                    svc_name = svc_name[:-2]

            try:
                image_tags = container.image.tags if container.image else []
                image_tag = image_tags[0] if image_tags else None
                if not image_tag:
                    # Try to infer from compose image label
                    compose_image = container.labels.get("com.docker.compose.image")
                    if compose_image:
                        image_tag = compose_image
                    else:
                        # Try to construct from service name (GHCR pattern)
                        inferred = _infer_ghcr_image_ref(svc_name)
                        if inferred:
                            image_tag = inferred
                        else:
                            log.warning(f"[updates] {container.name} has no image tag, skipping")
                            updates.append({
                                "service": svc_name,
                                "image": "unknown",
                                "current_digest": None,
                                "remote_digest": None,
                                "has_update": False,
                                "check_error": "no_image_tag",
                                "status": container.status,
                            })
                            continue

                # Local digest (from what was pulled when the container was last started)
                current_digest = _get_local_digest(container.image)

                # Remote digest (via OCI registry API — no pull). Only attempted
                # when GHCR is reachable + authenticated; otherwise skip the
                # network call entirely so this endpoint stays fast.
                remote_digest = _get_remote_digest(image_tag) if remote_available else None

                if not remote_available:
                    has_update = False
                    check_error = remote_error or "ghcr_unavailable"
                elif current_digest is None or remote_digest is None:
                    has_update = False
                    check_error = "digest_unavailable"
                else:
                    # Compare only the sha256 hex, strip any prefix differences
                    def _norm(d: str) -> str:
                        return d.split(":")[-1].lower() if d else ""
                    has_update = _norm(current_digest) != _norm(remote_digest)
                    check_error = None

                updates.append({
                    "service": svc_name,
                    "image": image_tag,
                    "current_digest": current_digest,
                    "remote_digest": remote_digest,
                    "has_update": has_update,
                    "check_error": check_error,
                    "status": container.status,
                })

                log.info(
                    f"[updates] {svc_name} ({container.name}): local={current_digest} remote={remote_digest} "
                    f"has_update={has_update}"
                )
            except Exception as e:
                log.warning(f"[updates] Failed to check {container.name}: {e}")
                updates.append({
                    "service": svc_name,
                    "image": getattr(container, "image_tag", "unknown"),
                    "has_update": False,
                    "check_error": str(e),
                    "status": container.status,
                })
                continue

        return {
            "checked": len(updates),
            "updates_available": sum(1 for u in updates if u["has_update"]),
            "services": updates,
            "check_error": remote_error,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/api/containers/{service_name}/pull", dependencies=[Depends(verify_internal_secret)])
def pull_image_update(service_name: str):
    """Start a non-blocking pull of the latest image for a service.

    Returns immediately with status ``pulling``. Poll
    ``GET /api/containers/{service_name}/pull/status`` for progress.
    """
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    container = _resolve_container(service_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container for service '{service_name}' not found")

    if not _verify_sharedllm_container(container):
        raise HTTPException(status_code=400, detail=f"Container '{container.name}' is not part of the sharedllm stack")

    try:
        current_image_id = container.image.id
        if not current_image_id:
            raise HTTPException(status_code=500, detail="Cannot determine current image ID")

        image_tags = container.image.tags if container.image else []
        image_tag = image_tags[0] if image_tags else None
        if not image_tag:
            # Try to infer from container labels (compose image label)
            compose_image = container.labels.get("com.docker.compose.image")
            if compose_image:
                image_tag = compose_image
            else:
                # Try to construct from service name (GHCR pattern)
                inferred = _infer_ghcr_image_ref(service_name)
                if inferred:
                    image_tag = inferred
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Container has no image tag to pull. "
                               "Rebuild the image with a tag (e.g. ghcr.io/owner/repo:latest) "
                               "and recreate the container."
                    )

        # If a pull is already running, return its current status
        with _pull_lock:
            existing = _pull_status.get(service_name)
            if existing and existing.get("status") == "pulling":
                return {
                    "service": service_name,
                    "image": image_tag,
                    "current_image_id": current_image_id,
                    "status": "pulling",
                    "message": "Pull already in progress",
                    "pull_status": existing,
                }

        # Start the pull in a daemon thread so the HTTP request returns immediately
        thread = threading.Thread(
            target=_pull_image_background,
            args=(service_name, image_tag, current_image_id),
            daemon=True,
        )
        thread.start()

        return {
            "service": service_name,
            "image": image_tag,
            "current_image_id": current_image_id,
            "status": "pulling",
            "message": "Pull started in background. Poll pull/status for progress.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/containers/{service_name}/pull/status", dependencies=[Depends(verify_internal_secret)])
def get_pull_status(service_name: str):
    """Get the status of a background image pull for a service."""
    with _pull_lock:
        status = _pull_status.get(service_name)
    if not status:
        return {
            "service": service_name,
            "status": "idle",
            "message": "No pull in progress. Call POST /api/containers/{service_name}/pull to start one.",
        }
    return {"service": service_name, **status}


@app.post("/api/containers/{service_name}/pull-and-restart", dependencies=[Depends(verify_internal_secret)])
def pull_and_restart(service_name: str):
    """Pull the latest image and immediately restart the container with it.

    This is the 'phone update' flow: pull the new image (blocking for this
    combined operation), fix volume permissions, then recreate the container
    with the new image.
    """
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    container = _resolve_container(service_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container for service '{service_name}' not found")

    if not _verify_sharedllm_container(container):
        raise HTTPException(status_code=400, detail=f"Container '{container.name}' is not part of the sharedllm stack")

    try:
        current_image_id = container.image.id
        if not current_image_id:
            raise HTTPException(status_code=500, detail="Cannot determine current image ID")

        image_tags = container.image.tags if container.image else []
        image_tag = image_tags[0] if image_tags else None
        if not image_tag:
            # Try to infer from container labels (compose image label)
            compose_image = container.labels.get("com.docker.compose.image")
            if compose_image:
                image_tag = compose_image
            else:
                # Try to construct from service name (GHCR pattern)
                inferred = _infer_ghcr_image_ref(service_name)
                if inferred:
                    image_tag = inferred
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="Container has no image tag to pull. "
                               "Rebuild the image with a tag (e.g. ghcr.io/owner/repo:latest) "
                               "and recreate the container."
                    )

        log.info(f"[pull-and-restart] Pulling latest image for {container.name}: {image_tag}")
        client.images.pull(image_tag)

        new_image_id = client.images.get(image_tag).id
        if new_image_id == current_image_id:
            return {
                "service": service_name,
                "status": "SUCCESS",
                "message": "Image is up to date, no restart needed",
                "updated": False,
                "current_image_id": current_image_id,
                "new_image_id": new_image_id,
            }

        # Fix volume permissions before recreating (mirrors deploy.sh guard)
        fixed_vols = _fix_volume_permissions(container)
        log.info(f"[pull-and-restart] Fixed permissions for volumes: {fixed_vols}")

        _recreate_container(container, new_image_id)

        return {
            "service": service_name,
            "status": "SUCCESS",
            "message": f"Container {container.name} updated and restarted with new image",
            "updated": True,
            "current_image_id": current_image_id,
            "new_image_id": new_image_id,
            "volumes_fixed": fixed_vols,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/containers/{service_name}/logs", dependencies=[Depends(verify_internal_secret)])
def get_container_logs(service_name: str, tail: int = 100):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    container = _resolve_container(service_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container for service '{service_name}' not found")

    if not _verify_sharedllm_container(container):
        raise HTTPException(status_code=400, detail=f"Container '{container.name}' is not part of the sharedllm stack")

    try:
        logs = container.logs(tail=tail, stdout=True, stderr=True).decode("utf-8")
        tb_matches = TRACEBACK_RE.findall(logs)
        has_tracebacks = len(tb_matches) > 0
        log.info(f"[logs] {container.name} tail={tail} tracebacks_found={len(tb_matches)}")
        return {
            "name": service_name,
            "logs": logs,
            "has_tracebacks": has_tracebacks,
            "traceback_count": len(tb_matches),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/containers/{service_name}/exec", dependencies=[Depends(verify_internal_secret)])
def exec_in_container(service_name: str, body: dict):
    """
    Execute a shell command inside a running container.
    Body: { "command": "ping -c 1 sharedllm_control_plane" }
    """
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    container = _resolve_container(service_name)
    if not container:
        raise HTTPException(status_code=404, detail=f"Container for service '{service_name}' not found")

    if not _verify_sharedllm_container(container):
        raise HTTPException(status_code=400, detail=f"Container '{container.name}' is not part of the sharedllm stack")

    command = body.get("command")
    if not command:
        raise HTTPException(status_code=400, detail="'command' is required in request body")

    try:
        if container.status != "running":
            raise HTTPException(
                status_code=409,
                detail=f"Container {container.name} is not running (status: {container.status})"
            )

        exit_code, output = container.exec_run(
            cmd=["sh", "-c", command],
            stdout=True,
            stderr=True,
            demux=False
        )
        if isinstance(output, bytes):
            output_str = output.decode("utf-8") if output else ""
        elif isinstance(output, tuple):
            output_str = (output[0] or b"").decode("utf-8") + (output[1] or b"").decode("utf-8")
        else:
            output_str = ""
        log.info(f"[exec] {container.name} `{command}` → exit_code={exit_code}")
        return {
            "service": service_name,
            "command": command,
            "exit_code": exit_code,
            "output": output_str
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Integration: DNS Sync Webhook ───────────────────────────────────────────

@app.post("/api/webhooks/dns-sync")
async def dns_sync_webhook(request: dict):
    """Handle webhook notifications from DNS sync service."""
    event = request.get("event")
    data = request.get("data", {})
    log.info(f"[dns-sync webhook] Event: {event}, Data: {data}")

    if event == "network_change":
        log.info(f"[dns-sync] Network change: added={data.get('added')}, removed={data.get('removed')}")

    return {"status": "ok", "event": event}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
