# services/execution/handlers/learning.py
import hashlib
import json
import logging

import aiohttp

from services.config import INTERNAL_SECRET, RAG_SVC_URL
from services.execution.schemas import ExecutionResult, SystemLearningRequest

log = logging.getLogger("execution.learning")

RAG_SVC = RAG_SVC_URL

async def handle_system_learning(req: SystemLearningRequest) -> ExecutionResult:
    try:
        # Stable, citable lesson id derived from the rule so re-ingests of the
        # same lesson converge and `Apply: [id]` citations stay valid.
        _lid = "lesson-" + hashlib.sha1(
            (req.rule or req.content or req.topic).encode("utf-8")
        ).hexdigest()[:10]
        # The stored document is COMPACT JSON (not prose) so the mission-prompt
        # renderer (orchestrator._fetch_rag_context) can inject each lesson as
        # a single short line `- [id][outcome] (conf) RULE` instead of up to
        # 2000 chars of narrative. The truncated summary keeps enough signal
        # for semantic retrieval without wasting the context budget. 400 chars
        # matches the dreaming COMPACT pass (summary_len=400) so stored lessons
        # stay within the 1500-char compactness ceiling the training
        # curriculum asserts (800-char summaries overrun it once rule + topic
        # + JSON wrapper are added).
        content = json.dumps({
            "id": _lid,
            "topic": req.topic,
            "rule": req.rule,
            "root_cause": req.root_cause,
            "outcome": req.outcome,
            "confidence": req.confidence,
            "tags": req.tags,
            "summary": (req.content or "")[:400],
        }, ensure_ascii=False)
        payload = {
            "user_id": req.user_context.user,
            "content": content,
            "collection_name": "system_learnings",
            "metadata": {
                "id": _lid,
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
