# services/gateway/orchestrator.py
import asyncio
import logging
import httpx
import os
import json
from typing import Any, Dict, List, Optional, Callable, Awaitable
try:
    from .schemas import ResolvedCredentials
except (ImportError, ValueError):
    from schemas import ResolvedCredentials

log = logging.getLogger("gateway.orchestrator")

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def process_full_orchestration(job_payload: Dict[str, Any], chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    """
    Handles the full Raven orchestration pipeline:
    Decompose -> Memory -> RAG -> Inference -> Tools -> Update.
    """
    query = job_payload["query"]
    user_id = job_payload["creds"]["user"]
    creds = ResolvedCredentials(**job_payload["creds"])
    model = job_payload["model"]
    
    log.info(f"[Orchestrator] Starting orchestration for query: {query[:50]}...")
    
    # 1. Retrieve Memory (Simplified for Phase 1 - should use helpers)
    # In a real impl, we'd call the history service or redis directly
    short_term = [] # Placeholder
    
    # 2. Context Injection (RAG)
    rag_context = await _fetch_rag_context(query, user_id)
    
    # 3. Autonomous Detection
    autonomy_signals = ["raven", "perform", "audit", "index", "reindex", "scan", "repair", "fix", "check", "synchronize", "sync"]
    is_autonomous = any(k in query.lower() for k in autonomy_signals)
    
    # 4. Final Inference / AgentLoop
    full_system = job_payload.get("system", "")
    if is_autonomous:
        try:
            from .agent_loop import AgentLoop
        except (ImportError, ValueError):
            from agent_loop import AgentLoop
        ans = await AgentLoop(query, model, full_system, short_term, user_id, creds)
    else:
        ans = await _single_turn_inference(query, model, rag_context, short_term, chunk_callback)
        
    return ans

async def _fetch_rag_context(query: str, user_id: str) -> str:
    rag_context = ""
    try:
        collections = ["ha_entities", "nextcloud_files", "system_capabilities", "system_learnings"]
        async with httpx.AsyncClient(timeout=10.0) as client:
            for coll in collections:
                resp = await client.post(
                    f"{RAG_SVC}/rag/search",
                    json={"collection_name": coll, "query": query, "user_id": user_id, "k": 10},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code == 200:
                    hits = resp.json().get("results", [])
                    if hits:
                        rag_context += f"\n[{coll.upper()}]\n" + "\n".join([h["content"] for h in hits])
    except Exception as e:
        log.error(f"RAG search failed: {e}")
    return rag_context

async def _single_turn_inference(query: str, model: str, rag_context: str, history: List[Dict[str, str]], chunk_callback: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    system = f"You are Raven, an autonomous AI OS. Context:\n{rag_context}"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}] + history + [{"role": "user", "content": query}],
        "stream": True if chunk_callback else False
    }
    
    full_content = ""
    async with httpx.AsyncClient(timeout=120.0) as client:
        if not chunk_callback:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "Error.")
        
        # Streaming path
        async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line: continue
                try:
                    chunk_json = json.loads(line)
                    content = chunk_json.get("message", {}).get("content", "")
                    if content:
                        full_content += content
                        await chunk_callback(content)
                    if chunk_json.get("done"):
                        break
                except Exception as e:
                    log.error(f"Error parsing streaming chunk: {e}")
    
    return full_content
