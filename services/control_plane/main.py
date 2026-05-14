import os
import docker
from fastapi import FastAPI, HTTPException, Header, Depends
from typing import Optional

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")

app = FastAPI(title="Control Plane Service")

@app.get("/health")
def health():
    return {"status": "ok", "service": "control_plane"}

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("control_plane")

# Initialize Docker client
try:
    client = docker.from_env()
    log.info("Docker client initialized successfully.")
except Exception as e:
    import traceback
    log.error(f"Failed to initialize docker client: {e}")
    log.error(traceback.format_exc())
    client = None

def verify_internal_secret(x_internal_secret: str = Header(...)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Invalid internal secret")
    return True

@app.post("/api/restart/{service_name}", dependencies=[Depends(verify_internal_secret)])
def restart_service(service_name: str):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    
    # Optional security: ensure we only restart internal services
    if not service_name.startswith("sharedllm_"):
        raise HTTPException(status_code=400, detail="Can only restart sharedllm_ prefixed containers")

    try:
        container = client.containers.get(service_name)
        container.restart()
        return {"status": "SUCCESS", "message": f"Container {service_name} restarted successfully"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/status/{service_name}")
def get_service_status(service_name: str, x_internal_secret: str = Header(None)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        container = client.containers.get(service_name)
        return {
            "name": container.name,
            "status": container.status,
            "image": container.image.tags[0] if container.image.tags else "unknown"
        }
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Service not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/containers")
def list_containers(x_internal_secret: str = Header(None)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        containers = client.containers.list(all=True)
        # Only show sharedllm containers for security
        results = []
        for c in containers:
            if c.name.startswith("sharedllm_"):
                results.append({
                    "name": c.name,
                    "status": c.status,
                    "image": c.image.tags[0] if c.image.tags else "unknown"
                })
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/containers/{service_name}/logs")
def get_container_logs(service_name: str, tail: int = 100, x_internal_secret: str = Header(None)):
    if x_internal_secret != INTERNAL_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        container = client.containers.get(service_name)
        logs = container.logs(tail=tail, stdout=True, stderr=True).decode("utf-8")
        return {"name": service_name, "logs": logs}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/containers/{service_name}/exec", dependencies=[Depends(verify_internal_secret)])
def exec_in_container(service_name: str, body: Dict[str, Any]):
    if not client:
        raise HTTPException(status_code=500, detail="Docker client not initialized")
    try:
        container = client.containers.get(service_name)
        command = body.get("command")
        if not command:
            raise HTTPException(status_code=400, detail="No command provided")
        
        exec_result = container.exec_run(command)
        return {
            "exit_code": exec_result.exit_code,
            "output": exec_result.output.decode("utf-8")
        }
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
