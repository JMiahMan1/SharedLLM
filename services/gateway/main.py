# services/gateway/main.py
"""
Microservice 2: Intent Gateway
Entry point for all clients. Coordinates Identity, RAG, Execution, and LLM.
"""
import os
import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from schemas import ChatRequest, ChatResponse
from intent_engine import engine

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")

IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://identity:8001")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
RAG_SVC = os.getenv("RAG_SVC_URL", "http://rag:8004")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
FAST_PATH_THRESHOLD = float(os.getenv("FAST_PATH_THRESHOLD", "0.85"))

# LLM Config (simplified for gateway)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Gateway starting up...")
    engine.load()
    yield
    log.info("Gateway shutting down...")

app = FastAPI(title="SharedLLM Intent Gateway", version="1.0.0", lifespan=lifespan)

async def resolve_identity(req: ChatRequest) -> dict:
    """Call Identity Service to get decrypted credentials."""
    async with httpx.AsyncClient() as client:
        try:
            payload = {
                "rag_user": req.rag_user,
                "voice_id": req.voice_id,
                "device_id": req.device_id
            }
            resp = await client.post(
                f"{IDENTITY_SVC}/api/resolve",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5.0
            )
            if resp.status_code == 404:
                raise HTTPException(status_code=401, detail="User resolution failed")
            resp.raise_for_status()
            return resp.json()
        except httpx.RequestError as e:
            log.error(f"Identity Service unreachable: {e}")
            raise HTTPException(status_code=503, detail="Identity service unavailable")

async def execute_command(endpoint: str, payload: dict) -> dict:
    """Call Execution Bridge."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{EXECUTION_SVC}{endpoint}",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=10.0
            )
            if resp.status_code != 200:
                log.error(f"Execution Bridge error ({resp.status_code}): {resp.text}")
                return {"status": "FAILURE", "message": f"Execution error: {resp.text}", "service": endpoint}
            return resp.json()
        except httpx.RequestError as e:
            log.error(f"Execution Bridge unreachable: {e}")
            return {"status": "FAILURE", "message": f"Execution service error: {str(e)}"}

def extract_entity_heuristic(query: str, intent: str) -> str:
    """
    Very basic heuristic entity extraction for Fast Path.
    In a real system, this might use a lightweight NER model or regex.
    """
    q = query.lower()
    # Simple mock extraction based on known devices
    if "piano" in q or "lamp" in q:
        return "light.piano_lamp"
    if "tv" in q:
        return "media_player.living_room_tv"
    if "kitchen" in q:
        return "media_player.kitchen_speaker"
    
    # Fallback dummy entity
    return "light.dummy_light"

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    # 1. Identity Resolution
    creds = await resolve_identity(req)
    user_context = {
        "user": creds["user"],
        "ha_url": creds.get("ha_url", ""),
        "ha_token": creds.get("ha_token", "")
    }

    # 2. Intent Classification (Semantic Router)
    intent, confidence = engine.classify(req.query)
    log.info(f"[gateway] Classified '{req.query}' -> {intent} ({confidence:.2f})")

    # 3. Fast Path Evaluation
    is_fast_path = confidence >= FAST_PATH_THRESHOLD and intent in ("turn_on", "turn_off", "toggle", "play_media", "pause_media", "announce")

    if is_fast_path:
        log.info(f"[gateway] FAST PATH triggered for {intent}")
        entity_id = extract_entity_heuristic(req.query, intent)
        
        if intent in ("turn_on", "turn_off", "toggle"):
            exec_payload = {
                "user_context": user_context,
                "entity_id": entity_id,
                "action": intent
            }
            exec_res = await execute_command("/execute/light", exec_payload)
            
        elif intent in ("play_media", "pause_media"):
            if intent == "play_media":
                exec_payload = {
                    "user_context": user_context,
                    "entity_id": entity_id,
                    "query": req.query.replace("play", "").strip()
                }
                exec_res = await execute_command("/execute/media/play", exec_payload)
            else:
                exec_payload = {
                    "user_context": user_context,
                    "entity_id": entity_id,
                    "command": "pause"
                }
                exec_res = await execute_command("/execute/media/transport", exec_payload)
                
        elif intent == "announce":
            exec_payload = {
                "user_context": user_context,
                "entity_id": entity_id,
                "message": req.query.replace("announce", "").replace("say", "").strip()
            }
            exec_res = await execute_command("/execute/announce", exec_payload)
            
        return ChatResponse(
            status=exec_res.get("status", "FAILURE"),
            message=exec_res.get("message", "Execution completed"),
            intent=intent,
            confidence=confidence,
            execution_result=exec_res
        )

    # 4. Slow Path (LLM + RAG)
    # If not fast path, we would call the RAG service and then the LLM.
    # For this architecture scaffold, we simulate the LLM response.
    log.info(f"[gateway] SLOW PATH for intent {intent} ({confidence:.2f})")
    
    # Simulate RAG call
    async with httpx.AsyncClient() as client:
        try:
            rag_payload = {"query": req.query, "user_id": creds["user"], "k": 3}
            rag_resp = await client.post(
                f"{RAG_SVC}/rag/search",
                json=rag_payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=5.0
            )
            rag_data = rag_resp.json() if rag_resp.status_code == 200 else {}
        except Exception:
            rag_data = {}

    return ChatResponse(
        status="SUCCESS",
        message=f"I am processing your request. Simulated LLM response for: {req.query}",
        intent=intent,
        confidence=confidence
    )

@app.get("/health")
def health():
    return {"status": "ok", "service": "gateway"}
