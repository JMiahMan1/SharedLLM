# services/execution/handlers/learning.py
import logging
import httpx
from services.config import RAG_SVC_URL, INTERNAL_SECRET
from services.schemas import SystemLearningRequest, ExecutionResult

log = logging.getLogger("execution.learning")

RAG_SVC = RAG_SVC_URL

async def handle_system_learning(req: SystemLearningRequest) -> ExecutionResult:
    try:
        payload = {
            "user_id": req.user_context.user,
            "content": f"### TOPIC: {req.topic}\n\n{req.content}\n\nTAGS: {', '.join(req.tags)}",
            "collection_name": "system_learnings",
            "metadata": {
                "topic": req.topic,
                "tags": req.tags,
                "type": "learning"
            }
        }
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RAG_SVC}/rag/ingest",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=10.0
            )
            
            if resp.status_code == 200:
                return ExecutionResult(status="SUCCESS", message="Learning persisted successfully.", service="learning")
            else:
                return ExecutionResult(status="FAILURE", message=f"RAG ingestion failed: {resp.text}", service="learning")
                
    except Exception as e:
        log.error(f"System learning persistence failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="learning")
