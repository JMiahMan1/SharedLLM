# services/execution/handlers/diagnostics.py
import logging
import asyncio
import subprocess
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from ..schemas import ExecutionResult
except ImportError:
    try:
        from execution.schemas import ExecutionResult
    except ImportError:
        from schemas import ExecutionResult

log = logging.getLogger("execution.diagnostics")

async def handle_get_system_logs(req_data: dict) -> ExecutionResult:
    """
    Advanced log retrieval for a specific service.
    """
    service = req_data.get("service", "execution")
    lines = req_data.get("lines", 50)
    
    try:
        if req_data.get("action") == "ls":
            path = req_data.get("path", "/app")
            cmd = ["ls", "-la", path]
            result = subprocess.run(cmd, capture_output=True, text=True)
        else:
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


async def handle_execution_logs(req_data: dict) -> ExecutionResult:
    """
    Query Execution service logs with optional filtering by handler/service and keyword.
    Designed for LLM verification of task execution and troubleshooting.
    """
    lines = req_data.get("lines", 100)
    service_filter = req_data.get("service")
    keyword = req_data.get("keyword")
    
    # Ignore hallucinated catch-all values
    if service_filter and service_filter.lower() in ("all", "any", "*", "none", ""):
        service_filter = None
    
    try:
        cmd = ["docker", "logs", "--tail", str(lines), "sharedllm_execution"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            return ExecutionResult(
                status="FAILURE",
                message=f"Failed to retrieve execution logs: {result.stderr}",
                service="execution_logs"
            )
        
        log_lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        
        # Filter by service/handler if specified
        if service_filter:
            log_lines = [line for line in log_lines if service_filter.lower() in line.lower()]
        
        # Filter by keyword if specified
        if keyword:
            log_lines = [line for line in log_lines if keyword.lower() in line.lower()]
        
        if not log_lines:
            filter_desc = []
            if service_filter: filter_desc.append(f"service='{service_filter}'")
            if keyword: filter_desc.append(f"keyword='{keyword}'")
            msg = "No execution logs found"
            if filter_desc: msg += f" matching filters ({', '.join(filter_desc)})"
            return ExecutionResult(
                status="SUCCESS",
                message=msg,
                service="execution_logs"
            )
        
        # Build a summary for quick LLM comprehension
        summary_lines = []
        for line in log_lines[-50:]:  # Cap at 50 lines for response size
            summary_lines.append(line)
        
        log_text = "\n".join(summary_lines)
        
        return ExecutionResult(
            status="SUCCESS",
            message=f"Retrieved {len(log_lines)} execution log lines",
            service="execution_logs",
            detail={"logs": log_text, "total_matches": len(log_lines)}
        )
    except Exception as e:
        return ExecutionResult(status="FAILURE", message=f"Execution log query error: {e}", service="execution_logs")
