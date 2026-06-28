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
    """Check all sharedllm services for available image updates without pulling."""
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")

    try:
        containers = client.containers.list(all=True)
        updates = []

        for container in containers:
            if not container.name or not container.name.startswith("sharedllm_"):
                continue

            try:
                image_tag = container.image.tags[0] if container.image.tags else None
                if not image_tag:
                    continue

                current_image_id = container.image.id
                # Check registry without pulling
                try:
                    client.images.get_registry_data(image_tag)
                    latest_image = client.images.pull(image_tag, dry_run=True)
                    latest_image_id = latest_image.id
                except Exception:
                    # If dry_run fails, try regular pull check
                    try:
                        client.images.pull(image_tag)
                        latest_image_id = client.images.get(image_tag).id
                    except Exception:
                        latest_image_id = current_image_id

                has_update = latest_image_id != current_image_id

                updates.append({
                    "service": container.name,
                    "image": image_tag,
                    "current_image_id": current_image_id,
                    "latest_image_id": latest_image_id,
                    "has_update": has_update,
                    "status": container.status
                })
            except Exception as e:
                log.warning(f"Failed to check updates for {container.name}: {e}")
                continue

        return {
            "checked": len(updates),
            "updates_available": sum(1 for u in updates if u["has_update"]),
            "services": updates
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
