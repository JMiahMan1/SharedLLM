# services/execution/handlers/learning.py
import logging

import aiohttp

from services.config import INTERNAL_SECRET, RAG_SVC_URL
from services.execution.schemas import ExecutionResult, SystemLearningRequest

log = logging.getLogger("execution.learning")

RAG_SVC = RAG_SVC_URL

async def handle_system_learning(req: SystemLearningRequest) -> ExecutionResult:
    try:
        # Structured, honest lesson. The content wraps the narrative evidence; the
        # transferable takeaway lives in `rule` so it is mechanically matchable
        # and renderable as a first-class field (not buried prose).
        content = (
            f"### TOPIC: {req.topic}\n\n"
            f"**RULE:** {req.rule}\n\n"
            f"**ROOT CAUSE:** {req.root_cause}\n\n"
            f"**OUTCOME:** {req.outcome}\n\n"
            f"**CONFIDENCE:** {req.confidence:.2f}\n\n"
            f"{req.content}\n\n"
            f"TAGS: {', '.join(req.tags)}"
        )
        payload = {
            "user_id": req.user_context.user,
            "content": content,
            "collection_name": "system_learnings",
            "metadata": {
                "topic": req.topic,
                "rule": req.rule,
                "root_cause": req.root_cause,
                "outcome": req.outcome,
                "confidence": req.confidence,
                "tags": req.tags,
                "type": "learning",
                "supersedes": req.supersedes,
            },
        }

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client, client.post(
            f"{RAG_SVC}/rag/ingest",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        ) as resp:
            if resp.status == 200:
                return ExecutionResult(status="SUCCESS", message="Learning persisted successfully.", service="learning")
            else:
                return ExecutionResult(status="FAILURE", message=f"RAG ingestion failed: {await resp.text()}", service="learning")

    except Exception as e:
        log.error(f"System learning persistence failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="learning")
