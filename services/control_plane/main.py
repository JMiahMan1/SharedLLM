import logging
import re

from docker.errors import NotFound
from fastapi import Depends, FastAPI, Header, HTTPException

import docker
from services.config import INTERNAL_SECRET

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("control_plane")

from services.shared.info_endpoint import info_router

TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\)|^\s+File ", re.MULTILINE)

app = FastAPI(title="Control Plane Service")
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


# ─── Auth Dependency ───────────────────────────────────────────────────────────

def verify_internal_secret(x_internal_secret: str = Header(..., alias="X-Internal-Secret")):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Invalid internal secret")
    return True


# ─── Health ────────────────────────────────────────────────────────────────────

import os
import time

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
            if started_at.endswith("Z"):
                started_at_clean = started_at[:-1] + "+00:00"
            else:
                started_at_clean = started_at
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


def _recreate_container(container, new_image_id: str):
    """
    Recreates a container with the new image, preserving all config (port bindings,
    volume binds, environment, network mode, and custom networks/aliases).
    """
    import time
    if not client:
        raise ValueError("Docker client not initialized")

    old_name = container.name
    backup_name = f"{old_name}_backup_{int(time.time())}"

    # 1. Stop the old container
    log.info(f"[recreate] Stopping old container {old_name}...")
    container.stop(timeout=10)

    # 2. Rename old container to backup name
    log.info(f"[recreate] Renaming {old_name} to {backup_name}...")
    container.rename(backup_name)

    new_container = None
    try:
        # 3. Extract old container configurations
        old_config = container.attrs
        config = old_config.get("Config", {})
        host_config = old_config.get("HostConfig", {})
        networks_dict = old_config.get("NetworkSettings", {}).get("Networks", {})

        exposed_ports = config.get("ExposedPorts")

        # 4. Create new container using the low-level API to preserve HostConfig exactly
        log.info(f"[recreate] Creating new container {old_name} with image {new_image_id}...")
        container_resp = client.api.create_container(
            image=new_image_id,
            name=old_name,
            command=config.get("Cmd"),
            entrypoint=config.get("Entrypoint"),
            environment=config.get("Env"),
            user=config.get("User"),
            working_dir=config.get("WorkingDir"),
            labels=config.get("Labels"),
            host_config=host_config,
            ports=exposed_ports if exposed_ports else None
        )

        new_container_id = container_resp["Id"]
        new_container = client.containers.get(new_container_id)

        # 5. Connect new container to custom networks with original aliases & IPs
        for net_name, net_config in networks_dict.items():
            if net_name == "bridge" and host_config.get("NetworkMode") == "default":
                continue
            if net_name == "host" and host_config.get("NetworkMode") == "host":
                continue

            try:
                network = client.networks.get(net_name)
                # Disconnect first to avoid auto-connect conflicts and set aliases/IPs
                try:
                    network.disconnect(new_container)
                except Exception:
                    pass

                # Filter auto-generated aliases (like container IDs) to avoid conflicts
                aliases = [
                    a for a in net_config.get("Aliases", [])
                    if a != container.id[:12] and a != backup_name and a != new_container.id[:12]
                ]
                ipv4 = net_config.get("IPAMConfig", {}).get("IPv4Address", "") or net_config.get("IPAddress", "")

                network.connect(
                    new_container,
                    aliases=aliases,
                    ipv4_address=ipv4 or None
                )
            except Exception as ne:
                log.warning(f"[recreate] Network connect warning for {net_name}: {ne}")

        # 6. Start the new container
        log.info(f"[recreate] Starting new container {new_container.name}...")
        new_container.start()

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
            try:
                new_container.remove(force=True)
            except Exception:
                pass
        # Restore backup container
        try:
            container.rename(old_name)
            container.start()
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

    For GHCR images, authentication uses the GHCR_TOKEN environment variable
    (a GitHub PAT with packages:read scope — the same token used by CI to push).
    """
    import json as _json
    import urllib.request

    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    # GHCR auth token (GitHub PAT with read:packages scope)
    # First, try to fetch user ID 1's github_token from identity service
    ghcr_token = ""
    try:
        identity_svc_url = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
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
            if "/" not in image_ref:
                repo = f"library/{image_ref}"
            else:
                repo = image_ref

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

                # Remote digest (via OCI registry API — no pull)
                remote_digest = _get_remote_digest(image_tag)

                if current_digest is None or remote_digest is None:
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
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/api/containers/{service_name}/pull", dependencies=[Depends(verify_internal_secret)])
def pull_image_update(service_name: str):
    """Pull latest image for a service to check for updates."""
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
            raise HTTPException(status_code=400, detail="Container has no image tag to pull")

        # Pull the latest version of the image
        log.info(f"Pulling latest image for {container.name}: {image_tag}")
        client.images.pull(image_tag)

        # Check if image ID changed
        new_image_id = client.images.get(image_tag).id
        updated = new_image_id != current_image_id

        return {
            "service": service_name,
            "image": image_tag,
            "current_image_id": current_image_id,
            "latest_image_id": new_image_id,
            "updated": updated,
            "message": "Image is up to date" if not updated else "New version pulled successfully"
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
