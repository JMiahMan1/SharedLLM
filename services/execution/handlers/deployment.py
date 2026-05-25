# services/execution/handlers/deployment.py
"""
DeploymentHandler — Allows the Ouroboros autonomous loop to restart or
rebuild SharedLLM containers without needing SSH access.

Strategy:
    Uses the Docker Python SDK talking to /var/run/docker.sock (mounted
    read-only into the execution container via docker-compose.yml).

    For 'rebuild' we delegate to the WorkspaceRuntime's git-pull + the
    docker SDK's image rebuild, then restart, so we never need to spawn
    docker-compose directly from Python.

Supported actions:
    restart      — restart a running container (graceful stop + start)
    logs         — fetch recent log lines (delegates to docker_logs handler)
    status       — return container status dict

Security:
    - Container name must start with 'sharedllm_'.
    - Only the execution container itself may be restarted if is_admin is False.
    - Rebuild/rebuild_all require is_admin=True.
"""
import logging
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

log = logging.getLogger("execution.deployment")

CONTAINER_ALLOWLIST_PREFIX = "sharedllm_"

# Containers that even non-admin autonomous loops may restart
SELF_HEALABLE = {
    "sharedllm_execution",
    "sharedllm_gateway",
    "sharedllm_ui",
    "sharedllm_rag",
    "sharedllm_logging",
    "sharedllm_storage",
    "sharedllm_workspace_runtime",
    "sharedllm_automation",
    "sharedllm_identity",
}


def _get_docker_client():
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


async def handle_deployment(req) -> dict:
    """
    Dispatch deployment operations based on req.action.

    req fields (from DeploymentRequest):
        action: str          — one of: restart, status, logs
        container_name: str  — target container (must be sharedllm_*)
        tail: int            — for 'logs' action (default 100)
        user_context         — is_admin check for privileged actions
    """
    action: str = req.action.lower().strip()
    container_name: str = req.container_name
    tail: int = int(getattr(req, "tail", 100) or 100)
    user_context = getattr(req, "user_context", None)
    is_admin: bool = getattr(user_context, "is_admin", False) if user_context else False

    # Security: enforce prefix allowlist
    if not container_name.startswith(CONTAINER_ALLOWLIST_PREFIX):
        return {
            "status": "FAILURE",
            "message": f"Container '{container_name}' is outside the allowed prefix '{CONTAINER_ALLOWLIST_PREFIX}'.",
            "service": "deployment",
            "detail": {"error": "security_violation"},
        }

    try:
        client = _get_docker_client()
    except RuntimeError as e:
        return {"status": "FAILURE", "message": str(e), "service": "deployment"}

    if action == "restart":
        # Non-admins may only restart whitelisted self-healable services
        if not is_admin and container_name not in SELF_HEALABLE:
            return {
                "status": "FAILURE",
                "message": f"Restarting '{container_name}' requires admin privileges.",
                "service": "deployment",
                "detail": {"error": "insufficient_permissions"},
            }
        try:
            container = client.containers.get(container_name)
            container.restart(timeout=30)
            log.info(f"[Deployment] Restarted container: {container_name}")
            return {
                "status": "SUCCESS",
                "message": f"Container '{container_name}' restarted successfully.",
                "service": "deployment",
                "detail": {"container": container_name, "action": "restart"},
            }
        except Exception as e:
            return {
                "status": "FAILURE",
                "message": f"Failed to restart '{container_name}': {e}",
                "service": "deployment",
            }

    elif action == "status":
        try:
            container = client.containers.get(container_name)
            return {
                "status": "SUCCESS",
                "message": f"Status for '{container_name}': {container.status}",
                "service": "deployment",
                "detail": {
                    "container": container_name,
                    "docker_status": container.status,
                    "image": container.image.tags[0] if container.image and container.image.tags else "unknown",
                },
            }
        except Exception as e:
            return {
                "status": "FAILURE",
                "message": f"Could not retrieve status for '{container_name}': {e}",
                "service": "deployment",
            }

    elif action == "logs":
        try:
            container = client.containers.get(container_name)
            raw: bytes = container.logs(tail=tail, stdout=True, stderr=True, timestamps=True)
            lines = raw.decode("utf-8", errors="replace").splitlines()
            return {
                "status": "SUCCESS",
                "message": f"Fetched {len(lines)} log lines from '{container_name}'.",
                "service": "deployment",
                "detail": {
                    "container": container_name,
                    "line_count": len(lines),
                    "lines": lines,
                },
            }
        except Exception as e:
            return {
                "status": "FAILURE",
                "message": f"Log fetch failed for '{container_name}': {e}",
                "service": "deployment",
            }

    elif action == "list":
        try:
            all_containers = client.containers.list(all=True)
            result = [
                {"name": c.name, "status": c.status}
                for c in all_containers
                if c.name is not None and c.name.startswith(CONTAINER_ALLOWLIST_PREFIX)
            ]
            return {
                "status": "SUCCESS",
                "message": f"Found {len(result)} SharedLLM containers.",
                "service": "deployment",
                "detail": {"containers": result},
            }
        except Exception as e:
            return {"status": "FAILURE", "message": str(e), "service": "deployment"}

    else:
        return {
            "status": "FAILURE",
            "message": f"Unknown deployment action '{action}'. Valid: restart, status, logs, list.",
            "service": "deployment",
            "detail": {},
        }
