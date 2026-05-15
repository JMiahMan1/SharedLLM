# services/execution/handlers/diagnostics.py
import logging
import asyncio
import subprocess
import os
from ..schemas import ExecutionResult

log = logging.getLogger("execution.diagnostics")

async def handle_get_system_logs(req_data: dict) -> ExecutionResult:
    """
    Advanced log retrieval for a specific service.
    """
    service = req_data.get("service", "execution")
    lines = req_data.get("lines", 50)
    
    try:
        # We use docker command to get logs of the container
        # Note: the execution container must have access to docker.sock
        cmd = ["docker", "logs", "--tail", str(lines), f"sharedllm_{service}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            return ExecutionResult(
                status="SUCCESS", 
                message=f"Retrieved {lines} lines for {service}", 
                service="diagnostics",
                detail={"logs": result.stdout}
            )
        else:
            return ExecutionResult(
                status="FAILURE", 
                message=f"Failed to get logs for {service}: {result.stderr}", 
                service="diagnostics"
            )
    except Exception as e:
        return ExecutionResult(status="FAILURE", message=f"Diagnostics error: {e}", service="diagnostics")
