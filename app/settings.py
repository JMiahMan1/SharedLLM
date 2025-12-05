# app/settings.py
import os
import logging
import asyncio
import traceback
from typing import Optional, Dict, Any, Tuple
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from fastapi import FastAPI

# Import Redis
try:
    import redis
except ImportError:
    redis = None

# Load .env
if os.getenv("DOCKER_ENV") != "1" and os.path.exists(".env"):
    from dotenv import load_dotenv
    load_dotenv(".env")

# --- Logging ---
DEBUG = os.getenv("DEBUG", "0") in ("1", "true", "True")
logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("unified-rag")

# --- Configuration ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
HA_URL = os.getenv("HA_URL")
HA_DEFAULT_USER = os.getenv("HA_DEFAULT_USER", "Admin")
HA_ENV_TOKEN = os.getenv("HA_TOKEN")

WHOOGLE_URL = os.getenv("WHOOGLE_URL", "https://search.sumemail.com")
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
SYSTEM_PROMPT_FILE = os.getenv("SYSTEM_PROMPT_FILE", "/app/data/system_prompt.txt")

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5:latest")
EMB_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Timeouts & Retries
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300")) 
OLLAMA_RETRY = int(os.getenv("OLLAMA_RETRY", "2"))

# Lowered cache TTL for faster test feedback
HA_CACHE_TTL = float(os.getenv("HA_CACHE_TTL", "5.0")) 
QUERY_CACHE_TTL = float(os.getenv("QUERY_CACHE_TTL", "60.0"))
MAX_HISTORY_TURNS = 15

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0") 
CHAT_HISTORY_TTL = int(os.getenv("CHAT_HISTORY_TTL", 86400))

# CRITICAL FIX: Lowered from 0.80 to 0.45 to catch "Turn on X" commands (scoring ~0.5-0.6)
ACTION_TOOL_CONFIDENCE_THRESHOLD = 0.45
INFORMATIONAL_INTENTS = ["general_query", "content_query", "time_query"]

# --- Alarm & Timer Config ---
ALARM_KEYWORDS_PATH = os.getenv("ALARM_KEYWORDS_PATH", "/app/config/alarm_keywords.json")
ALARM_SOUNDS_DIR = os.getenv("ALARM_SOUNDS_DIR", "/local/alarm_sounds")

# --- Prompts (Externalized) ---
def load_system_prompt():
    if os.path.exists(SYSTEM_PROMPT_FILE):
        try:
            with open(SYSTEM_PROMPT_FILE, "r") as f:
                return f.read().strip()
        except Exception as e:
            log.error(f"Failed to load system prompt: {e}")
    return "You are a helpful AI assistant."

# --- TEMPLATE 1: FULL PERSONALITY (For Chat/Search) ---
RAG_TEMPLATE = """{system_prompt}

### SYSTEM CONTEXT
{sys_info}

### KNOWLEDGE CONTEXT
{ha_ctx}
{nc_ctx}
{search_ctx}
{cal_ctx}

### USER QUERY
{query}
"""

# --- TEMPLATE 2: SIMPLE/ROBOTIC (For Success Confirmation) ---
# This template omits the custom {system_prompt} and contextual RAG blocks for brevity.
SIMPLE_RAG_TEMPLATE = """You are a concise home automation assistant.
The user's command was successfully executed.
Briefly confirm the action in 1 short sentence. Do not offer help. Do not be chatty.

### SYSTEM CONTEXT
{sys_info}

### USER QUERY
{query}
"""

CONTEXT_REWRITE_PROMPT = """Given the chat history, rewrite the last user query to be standalone and fully descriptive.
If the query is already standalone, return it exactly as is.
Do not answer the query, just rewrite it.

Chat History:
{history}

Last Query: {query}
Rewritten Query:"""

ORCHESTRATOR_PROMPT = """You are an action planning agent. Your task is to analyze the user's intent and decide the next action based on the available tools.

User Query: "{query}"
Detected Intent: "{intent_name}" (Confidence: {intent_score:.2f})

Available Tools:
1. 'media_command' (Turn on/off lights, switches, play music, stop music, volume control)
   - Parameters: "intent" (turn_on, turn_off, toggle, play_media, stop_media, media_next, media_previous)
2. 'calendar_add' (Schedule a new event/meeting)
3. 'calendar_list' (List upcoming events)
4. 'calendar_delete' (Delete/Cancel an event)
5. 'calendar_update' (Reschedule/Update an event)
6. 'intent_learn' (Teach the AI a new phrase mapping)
7. 'web_search' (Use for factual/external queries, if no other tool applies)
8. 'timer_add' (Set a timer or alarm)
9. 'timer_list' (List active timers/alarms)
10. 'timer_delete' (Cancel a timer/alarm)
11. 'timer_pause' (Pause a timer)
12. 'timer_resume' (Resume a timer)

Instructions:
- If the intent is clear and matches a tool, output a JSON object with "action": "tool_call", "tool_name": "<TOOL_NAME>", and "parameters": {{...}}.
- If the intent is "general_query" or "content_query" (informational), or if no tool fits, output "action": "CONVERSE".
- If the confidence is low (<0.4) and it's not a simple conversational greeting, output "action": "CONVERSE" (so the LLM can ask for clarification or answer generally).
- IMPORTANT: For 'media_command', always include the "intent" parameter.

Example JSON Output:
{{
  "action": "tool_call",
  "tool_name": "media_command",
  "parameters": {{ "intent": "turn_on" }}
}}

Respond ONLY with the JSON object.
"""

# --- Thread Pool ---
executor = ThreadPoolExecutor(max_workers=4)

async def run_blocking(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, partial(func, *args))

# --- Shared Resources ---
class GlobalResources:
    embedding_model = None
    chroma_client = None
    nextcloud_collection = None
    ha_collection = None
    redis_client = None

def get_user_creds(user: str = "default") -> Dict[str, str]:
    # In a real app, this would fetch from DB
    return {
        "user": user,
        "nextcloud_url": NEXTCLOUD_URL,
        "nextcloud_user": NEXTCLOUD_USER,
        "nextcloud_pass": NEXTCLOUD_PASS,
        "ha_url": HA_URL,
        "ha_token": HA_ENV_TOKEN
    }

# --- Resource Loading (Hot Reloadable) ---
async def load_resources():
    log.info("Loading Global Resources...")
    
    # 1. Redis
    if redis and REDIS_URL:
        try:
            GlobalResources.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
            GlobalResources.redis_client.ping()
            log.info("Redis Connected.")
        except Exception as e:
            log.error(f"Redis Connection Failed: {e}")
            GlobalResources.redis_client = None

    # 2. Embeddings (Mock or Real)
    if EMB_MODEL:
        try:
            from langchain_huggingface import HuggingFaceEmbeddings
            GlobalResources.embedding_model = HuggingFaceEmbeddings(model_name=EMB_MODEL)
            log.info(f"Embedding Model Loaded: {EMB_MODEL}")
        except Exception as e:
            log.error(f"Failed to load embedding model: {e}")

    # 3. ChromaDB (Vector Store)
    if CHROMA_DIR and GlobalResources.embedding_model:
        try:
            from langchain_chroma import Chroma
            # Nextcloud Collection
            GlobalResources.nextcloud_collection = Chroma(
                collection_name="nextcloud_docs",
                embedding_function=GlobalResources.embedding_model,
                persist_directory=CHROMA_DIR
            )
            # Home Assistant Collection
            GlobalResources.ha_collection = Chroma(
                collection_name="ha_entities",
                embedding_function=GlobalResources.embedding_model,
                persist_directory=CHROMA_DIR
            )
            log.info(f"ChromaDB Loaded from {CHROMA_DIR}")
        except Exception as e:
            log.error(f"ChromaDB Load Failed: {e}")

# --- LIFESPAN (Startup Logic) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_resources()
    
    # Initialize Intent Engine
    from intent_engine import engine
    await engine.load()

    # Start Timer Scheduler
    from app.logic.timer_scheduler import start_scheduler, stop_scheduler
    scheduler_task = asyncio.create_task(start_scheduler())
    
    yield
    
    # Shutdown
    stop_scheduler()
    if scheduler_task:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
            
    if GlobalResources.redis_client:
        GlobalResources.redis_client.close()
    log.info("Shutdown complete.")

# --- Caching ---
# Simple in-memory cache for HA states to reduce API spam
ha_state_cache = {}

# --- Utilities ---
def get_ha_state(entity_id: str) -> Optional[Dict]:
    # TODO: Implement proper caching with TTL
    return ha_state_cache.get(entity_id)
