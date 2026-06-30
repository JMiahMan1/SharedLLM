import docker
from docker.errors import NotFound, ImageNotFound
import re

from services.config import INTERNAL_SECRET
from fastapi import FastAPI, HTTPException, Header, Depends

import logging
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

import time
import os
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


@app.post("/api/restart/{service_name}", dependencies=[Depends(verify_internal_secret)])
def restart_service(service_name: str):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    if not service_name.startswith("sharedllm_"):
        raise HTTPException(status_code=400, detail="Can only restart sharedllm_ prefixed containers")

    try:
        container = client.containers.get(service_name)
        container.restart()
        return {"status": "SUCCESS", "message": f"Container {service_name} restarted successfully"}
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/containers/{service_name}", dependencies=[Depends(verify_internal_secret)])
def delete_container(service_name: str):
    """Remove a stopped container. Only for stopped containers."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    if not service_name.startswith("sharedllm_"):
        raise HTTPException(status_code=400, detail="Can only delete sharedllm_ prefixed containers")

    try:
        container = client.containers.get(service_name)
        if container.status == "running":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot delete running container {service_name}. Stop it first."
            )
        container.remove()
        return {"status": "SUCCESS", "message": f"Container {service_name} removed successfully"}
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
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
    import urllib.request
    import json as _json

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

            try:
                image_tags = container.image.tags if container.image else []
                image_tag = image_tags[0] if image_tags else None
                if not image_tag:
                    log.warning(f"[updates] {container.name} has no image tag, skipping")
                    updates.append({
                        "service": container.name,
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
                    "service": container.name,
                    "image": image_tag,
                    "current_digest": current_digest,
                    "remote_digest": remote_digest,
                    "has_update": has_update,
                    "check_error": check_error,
                    "status": container.status,
                })

                log.info(
                    f"[updates] {container.name}: local={current_digest} remote={remote_digest} "
                    f"has_update={has_update}"
                )
            except Exception as e:
                log.warning(f"[updates] Failed to check {container.name}: {e}")
                updates.append({
                    "service": container.name,
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

    if not service_name.startswith("sharedllm_"):
        raise HTTPException(status_code=400, detail="Can only pull for sharedllm_ prefixed containers")

    try:
        container = client.containers.get(service_name)
        current_image_id = container.image.id
        if not current_image_id:
            raise HTTPException(status_code=500, detail="Cannot determine current image ID")

        image_tag = container.image.tags[0] if container.image.tags else None
        if not image_tag:
            raise HTTPException(status_code=400, detail="Container has no image tag to pull")

        # Pull the latest version of the image
        log.info(f"Pulling latest image for {service_name}: {image_tag}")
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
    except ImageNotFound:
        raise HTTPException(status_code=404, detail=f"Image not found for {service_name}")
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/containers/{service_name}/logs", dependencies=[Depends(verify_internal_secret)])
def get_container_logs(service_name: str, tail: int = 100):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        container = client.containers.get(service_name)
        logs = container.logs(tail=tail, stdout=True, stderr=True).decode("utf-8")
        tb_matches = TRACEBACK_RE.findall(logs)
        has_tracebacks = len(tb_matches) > 0
        log.info(f"[logs] {service_name} tail={tail} tracebacks_found={len(tb_matches)}")
        return {
            "name": service_name,
            "logs": logs,
            "has_tracebacks": has_tracebacks,
            "traceback_count": len(tb_matches),
        }
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
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

    if not service_name.startswith("sharedllm_"):
        raise HTTPException(status_code=400, detail="Can only exec into sharedllm_ prefixed containers")

    command = body.get("command")
    if not command:
        raise HTTPException(status_code=400, detail="'command' is required in request body")

    try:
        container = client.containers.get(service_name)
        if container.status != "running":
            raise HTTPException(
                status_code=409,
                detail=f"Container {service_name} is not running (status: {container.status})"
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
        log.info(f"[exec] {service_name} `{command}` → exit_code={exit_code}")
        return {
            "service": service_name,
            "command": command,
            "exit_code": exit_code,
            "output": output_str
        }
    except NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
