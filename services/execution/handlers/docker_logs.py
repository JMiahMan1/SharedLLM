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
    Fetch and optionally filter logs from a Docker container.

    req fields (from DockerLogsRequest):
        container_name: str  — exact container name (must start with sharedllm_)
        tail: int            — number of lines to fetch (default 200)
        filter_level: str    — "ERROR", "WARN", "INFO", or None (all)
    """
    container_name: str = req.container_name
    tail: int = getattr(req, "tail", 200)
    filter_level: Optional[str] = getattr(req, "filter_level", None)

    # --- Security: enforce allowlist ---
    if not container_name.startswith(CONTAINER_ALLOWLIST_PREFIX):
        return {
            "status": "FAILURE",
            "message": f"Container '{container_name}' is not in the allowed prefix list ('{CONTAINER_ALLOWLIST_PREFIX}').",
            "service": "docker_logs",
        }

    try:
        client = _get_docker_client()
    except RuntimeError as e:
        return {"status": "FAILURE", "message": str(e), "service": "docker_logs"}

    try:
        container = client.containers.get(container_name)
    except Exception as e:
        return {
            "status": "FAILURE",
            "message": f"Container '{container_name}' not found: {e}",
            "service": "docker_logs",
        }

    try:
        raw_logs: bytes = container.logs(
            tail=tail,
            stdout=True,
            stderr=True,
            timestamps=True,
        )
        lines = raw_logs.decode("utf-8", errors="replace").splitlines()
    except Exception as e:
        return {
            "status": "FAILURE",
            "message": f"Failed to fetch logs: {e}",
            "service": "docker_logs",
        }

    # --- Optional level filter ---
    if filter_level:
        pattern = re.compile(re.escape(filter_level.upper()))
        lines = [l for l in lines if pattern.search(l)]

    log.info(
        f"[DockerLogs] {container_name}: fetched {len(lines)} lines "
        f"(filter={filter_level or 'none'}, tail={tail})"
    )

    return {
        "status": "SUCCESS",
        "message": f"Fetched {len(lines)} log lines from '{container_name}'.",
        "service": "docker_logs",
        "detail": {
            "container": container_name,
            "line_count": len(lines),
            "filter_level": filter_level,
            "lines": lines,
        },
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
            if c.name.startswith(CONTAINER_ALLOWLIST_PREFIX):
                result.append({
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image.tags else "unknown",
                })
        return {
            "status": "SUCCESS",
            "message": f"Found {len(result)} SharedLLM containers.",
            "service": "docker_logs",
            "detail": {"containers": result},
        }
    except Exception as e:
        return {"status": "FAILURE", "message": str(e), "service": "docker_logs"}
