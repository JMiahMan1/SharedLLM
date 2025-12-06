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
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[
                        logging.StreamHandler(),
                        logging.FileHandler("/data/app.log")
                    ])
log = logging.getLogger("unified-rag")

# --- Configuration ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
HA_URL = os.getenv("HA_URL")
HA_DEFAULT_USER = os.getenv("HA_DEFAULT_USER", "Admin")
HA_ENV_TOKEN = os.getenv("HA_TOKEN")

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

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

# Initialize OpenAI Client
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import AsyncOpenAI
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    except ImportError:
        log.warning("openai module not installed, skipping client init")

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

# --- Intent Thresholds & Groups ---
# CRITICAL FIX: Lowered from 0.80 to 0.45 to catch "Turn on X" commands (scoring ~0.5-0.6)
ACTION_TOOL_CONFIDENCE_THRESHOLD = 0.45
INFORMATIONAL_INTENTS = ["general_query", "content_query", "time_query"]

# --- Alarm & Timer Config ---
ALARM_KEYWORDS_PATH = os.getenv("ALARM_KEYWORDS_PATH", "/app/config/alarm_keywords.json")
ALARM_SOUNDS_DIR = os.getenv("ALARM_SOUNDS_DIR", "/local/alarm_sounds")


# --- Prompts (Externalized) ---
DEFAULT_CONTEXT_PROMPT = "Rewrite the following query to be self-contained, resolving any pronouns (he, she, it, they, him, her) using the chat history.\nHistory:\n{history}\nInput: {query}\nRefined (Return ONLY the refined query string, NO JSON, NO MARKDOWN):"
CONTEXT_REWRITE_PROMPT = os.getenv("CONTEXT_REWRITE_PROMPT", DEFAULT_CONTEXT_PROMPT)

DEFAULT_CALENDAR_PROMPT = """Extract details from: "{query}".
Return JSON with keys: 'summary' (string), 'start_time' (natural language), 'calendar_target' (string or null), 'intent' ('add', 'delete', 'update').
IMPORTANT: 'summary' MUST be the event title. If input is 'RAG_Test_123', summary is 'RAG_Test_123'.
JSON:"""
CALENDAR_EXTRACT_PROMPT = os.getenv("CALENDAR_EXTRACT_PROMPT", DEFAULT_CALENDAR_PROMPT)

DEFAULT_ORCHESTRATOR_PROMPT = """You are an action planning agent. Your task is to analyze the user's intent and decide the next action based on the available tools.
User Query: {query}
Best Vector Intent Match: {intent_name} (Confidence: {intent_score:.2f})

Available Tools:
1. 'calendar_add' (Schedule/create an event. Keywords: schedule, meeting, appointment, calendar)
2. 'calendar_delete' (Cancel an event by fuzzy name match)
3. 'calendar_list' (List available calendars)
4. 'calendar_update' (Reschedule an existing event)
5. 'timer_add' (Set a timer or alarm. Keywords: timer, alarm, wake me, remind me in X minutes)
6. 'timer_delete' (Cancel a timer/alarm)
7. 'timer_list' (List active timers/alarms)
8. 'timer_pause' (Pause a timer)
9. 'timer_resume' (Resume a timer)
10. 'media_command' (Handle media/HA control, requires 'intent' and 'device_name')
11. 'intent_learn' (Teach the AI a new phrase mapping)
12. 'web_search' (Use for factual/external queries, if no other tool applies)
13. 'note_add' (Create a new note. Params: 'title', 'content')
14. 'note_append' (Append to a note/list. Params: 'title', 'content')
15. 'note_read' (Read a specific note file. Params: 'title')
16. 'note_delete' (Delete a note file. Params: 'title')

CRITICAL: Distinguish between Alarms/Timers and Calendar Events.
- "Set an alarm for 8am" -> timer_add
- "Remind me in 10 minutes" -> timer_add
- "Wake me up at 7" -> timer_add
- "Schedule a meeting at 8am" -> calendar_add
- "Add to my calendar" -> calendar_add

If the intent is a clear, confident action, generate the JSON for a tool call.
If the query is conversational, informational, ambiguous, or requires the user's personal context/RAG, output 'CONVERSE'.

Output ONLY a single JSON object (DO NOT use markdown backticks). Example:
{{"action": "tool_call", "tool_name": "timer_add", "parameters": {{"summary": "Dinner", "time_expression": "20 minutes", "is_alarm": false}}}}
OR
{{"action": "CONVERSE"}}

JSON:"""
ORCHESTRATOR_PROMPT = os.getenv("ORCHESTRATOR_PROMPT", DEFAULT_ORCHESTRATOR_PROMPT)

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

{action_context}
"""

def load_system_prompt():
    if os.path.exists(SYSTEM_PROMPT_FILE):
        try:
            with open(SYSTEM_PROMPT_FILE, "r") as f:
                return f.read().strip()
        except Exception as e:
            log.error(f"Failed to load system prompt: {e}")
    return "You are a helpful AI assistant."

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
                collection_name="home_assistant",
                embedding_function=GlobalResources.embedding_model,
                persist_directory=CHROMA_DIR
            )
            log.info(f"ChromaDB Loaded from {CHROMA_DIR}")
        except Exception as e:
            log.error(f"ChromaDB Load Failed: {e}")

async def initialize_rag_resources():
    """Reloads RAG resources for hot-reloading."""
    await load_resources()
    from intent_engine import engine
    await engine.load()

# --- LIFESPAN (Startup Logic) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_resources()
    
    # Initialize Intent Engine
    from intent_engine import engine
    await engine.load()

    # Start Timer Scheduler
    from logic.timer_scheduler import start_scheduler, stop_scheduler
    log.info("Starting Timer/Alarm Scheduler...")
    scheduler_task = asyncio.create_task(start_scheduler())
    
    yield
    
    # Shutdown
    log.info("--- SHUTDOWN: Cleaning up resources ---")
    await stop_scheduler()
    try:
        scheduler_task.cancel()
    except: pass
    
    GlobalResources.embedding_model = None
    GlobalResources.chroma_client = None
    GlobalResources.ha_collection = None
    GlobalResources.nextcloud_collection = None
    if GlobalResources.redis_client:
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
