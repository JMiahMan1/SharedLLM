# services/execution/handlers/docker_logs.py
"""
DockerLogsHandler — Telemetry "pain receptor" for the Ouroboros autonomous loop.

Fetches recent stdout/stderr from Docker containers via the Docker socket,
optionally filtering by log level (ERROR, WARN, etc.).

Security: Only sharedllm_* containers may be queried. The Docker SDK talks
to the host socket at /var/run/docker.sock (mounted read-only in compose).
"""
import logging
import re
from typing import Optional

log = logging.getLogger("execution.docker_logs")

# Allowlist: only containers matching this prefix may be queried.
CONTAINER_ALLOWLIST_PREFIX = "sharedllm_"


def _get_docker_client():
    """Lazy-import docker client so the module loads even without the SDK."""
    try:
        import docker
        return docker.from_env()
    except ImportError:
        raise RuntimeError(
            "The 'docker' Python SDK is not installed. "
            "Add 'docker>=7.0.0' to services/execution/requirements.txt."
        )
    except Exception as e:
        raise RuntimeError(f"Could not connect to Docker socket: {e}")


async def handle_docker_logs(req) -> dict:
    """
    Fetch and optionally filter logs from one or more Docker containers.
    """
    tail: int = getattr(req, "tail_lines", 200)
    filter_level: Optional[str] = getattr(req, "grep_filter", None)
    
    container_names = []
    if req.container_name:
        container_names.append(req.container_name)
    if req.services:
        for s in req.services:
            if not s.startswith(CONTAINER_ALLOWLIST_PREFIX):
                container_names.append(f"{CONTAINER_ALLOWLIST_PREFIX}{s}")
            else:
                container_names.append(s)

    if not container_names:
        return {"status": "FAILURE", "message": "No container_name or services provided.", "service": "docker_logs"}

    try:
        client = _get_docker_client()
    except RuntimeError as e:
        return {"status": "FAILURE", "message": str(e), "service": "docker_logs"}

    results = {}
    for name in container_names:
        # --- Security: enforce allowlist ---
        if not name.startswith(CONTAINER_ALLOWLIST_PREFIX):
            results[name] = {"status": "FAILURE", "message": "Access denied."}
            continue

        try:
            container = client.containers.get(name)
            raw_logs: bytes = container.logs(tail=tail, stdout=True, stderr=True, timestamps=True)
            lines = raw_logs.decode("utf-8", errors="replace").splitlines()
            
            if filter_level:
                try:
                    pattern = re.compile(filter_level, re.IGNORECASE)
                    lines = [l for l in lines if pattern.search(l)]
                except Exception:
                    lines = [l for l in lines if filter_level.lower() in l.lower()]
            
            results[name] = {
                "status": "SUCCESS",
                "line_count": len(lines),
                "lines": lines
            }
        except Exception as e:
            results[name] = {"status": "FAILURE", "message": str(e)}

    return {
        "status": "SUCCESS" if any(r["status"] == "SUCCESS" for r in results.values()) else "FAILURE",
        "message": f"Processed logs for {len(results)} containers.",
        "service": "docker_logs",
        "detail": results
    }


async def handle_list_containers(req) -> dict:
    """
    Return a list of all sharedllm_* containers with their current status.
    Used by the autonomous loop to discover which services exist.
    """
    try:
        client = _get_docker_client()
    except RuntimeError as e:
        return {"status": "FAILURE", "message": str(e), "service": "docker_logs"}

    try:
        all_containers = client.containers.list(all=True)
        result = []
        for c in all_containers:
            if c.name is not None and c.name.startswith(CONTAINER_ALLOWLIST_PREFIX):
                result.append({
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image and c.image.tags else "unknown",
                })
        return {
            "status": "SUCCESS",
            "message": f"Found {len(result)} SharedLLM containers.",
            "service": "docker_logs",
            "detail": {"containers": result},
        }
    except Exception as e:
        return {"status": "FAILURE", "message": str(e), "service": "docker_logs"}
