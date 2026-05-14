import os
import docker
from fastapi import FastAPI, HTTPException, Header, Depends
from typing import Optional

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

import logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("control_plane")

app = FastAPI(title="Control Plane Service")

@app.get("/health")
def health():
    return {"status": "ok", "service": "control_plane"}

# Initialize Docker client
try:
    # Explicitly use the unix socket to avoid "http+docker" scheme errors
    client = docker.DockerClient(base_url="unix://var/run/docker.sock")
except Exception as e:
    print(f"Warning: Failed to initialize docker client: {e}")
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
        log.info(f"Restarting service: {service_name}")
        container = client.containers.get(service_name)
        container.restart()
        return {"status": "SUCCESS", "message": f"Container {service_name} restarted successfully"}
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail=f"Container {service_name} not found")
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
            "image": container.image.tags[0] if container.image.tags else "unknown"
        }
    except docker.errors.NotFound:
        raise HTTPException(status_code=404, detail="Service not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/containers", dependencies=[Depends(verify_internal_secret)])
def list_containers():
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
