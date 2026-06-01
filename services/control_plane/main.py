import docker
from docker.errors import NotFound
import re

from services.config import INTERNAL_SECRET
from fastapi import FastAPI, HTTPException, Header, Depends

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("control_plane")

TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\)|^\s+File ", re.MULTILINE)

app = FastAPI(title="Control Plane Service")

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

@app.get("/health")
def health():
    return {"status": "ok", "service": "control_plane"}

@app.get("/control_plane/health")
def health_prefixed():
    return health()


# ─── Container Management ──────────────────────────────────────────────────────

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
                results.append({
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image and c.image.tags else "unknown"
                })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status/{service_name}", dependencies=[Depends(verify_internal_secret)])
def get_service_status(service_name: str):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        container = client.containers.get(service_name)
        return {
            "name": container.name,
            "status": container.status,
            "image": container.image.tags[0] if container.image and container.image.tags else "unknown"
        }
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
