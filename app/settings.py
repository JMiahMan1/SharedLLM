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
HA_CACHE_TTL = float(os.getenv("HA_CACHE_TTL", "30.0"))
QUERY_CACHE_TTL = float(os.getenv("QUERY_CACHE_TTL", "60.0"))
MAX_HISTORY_TURNS = 15

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0") 
CHAT_HISTORY_TTL = int(os.getenv("CHAT_HISTORY_TTL", 86400)) 

# --- Intent Thresholds & Groups ---
ACTION_TOOL_CONFIDENCE_THRESHOLD = 0.80
INFORMATIONAL_INTENTS = ["general_query", "content_query", "time_query"]


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
1. 'calendar_add' (Schedule/create an event)
2. 'calendar_delete' (Cancel an event by fuzzy name match)
3. 'calendar_list' (List available calendars)
4. 'calendar_update' (Reschedule an existing event)
5. 'media_command' (Handle media/HA control, requires 'intent' and 'device_name')
6. 'intent_learn' (Teach the AI a new phrase mapping)
7. 'web_search' (Use for factual/external queries, if no other tool applies)

If the intent is a clear, confident action, generate the JSON for a tool call.
If the query is conversational, informational, ambiguous, or requires the user's personal context/RAG, output 'CONVERSE'.

Output ONLY a single JSON object (DO NOT use markdown backticks). Example:
{{"action": "tool_call", "tool_name": "calendar_add", "parameters": {{"summary": "Dinner with Dad", "start_time": "tonight at 7pm"}}}}
OR
{{"action": "CONVERSE"}}

JSON:"""
ORCHESTRATOR_PROMPT = os.getenv("ORCHESTRATOR_PROMPT", DEFAULT_ORCHESTRATOR_PROMPT)

DEFAULT_RAG_TEMPLATE = """### SYSTEM
{system_prompt}
{sys_info}

### CONTEXT
{ha_ctx}
{nc_ctx}
{search_ctx}
{cal_ctx}

### QUERY
{query}
"""
RAG_TEMPLATE = os.getenv("RAG_TEMPLATE", DEFAULT_RAG_TEMPLATE)

SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# --- Thread Pool ---
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("THREADPOOL_SIZE", "8")))

# --- Shared Resources ---
openai_client = None
if OPENAI_API_KEY:
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
        openai_client = openai
    except ImportError:
        pass

class GlobalResources:
    embedding_model = None
    chroma_client = None
    nextcloud_collection = None
    ha_collection = None
    redis_client = None

def configure_hf_offline():
    """
    Checks if the embedding model is already cached. 
    If so, forces offline mode to prevent hanging on lock files or network checks.
    """
    try:
        # Standard HF cache location
        cache_home = os.getenv("HF_HOME", os.path.expanduser("~/.cache/huggingface/hub"))
        model_dir_name = f"models--{EMB_MODEL.replace('/', '--')}"
        model_path = os.path.join(cache_home, model_dir_name)
        
        # Check if it looks like a valid cached model (has snapshots)
        if os.path.exists(model_path) and os.path.isdir(model_path):
            snapshots = os.path.join(model_path, "snapshots")
            if os.path.exists(snapshots) and os.listdir(snapshots):
                log.info(f"Embedding model found in cache at {model_path}. Forcing offline mode.")
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
    except Exception as e:
        log.warning(f"Failed to check HF cache: {e}")

# --- Resource Loading (Hot Reloadable) ---
async def initialize_rag_resources():
    """Initializes or re-initializes RAG and Intent Engine."""
    log.info("--- Loading RAG Resources (Hot Reload) ---")
    try:
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        configure_hf_offline() # Prevent hang on startup
        
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        
        # 1. Load Model
        if not GlobalResources.embedding_model:
            log.info(f"Loading embedding model: {EMB_MODEL} ...")
            GlobalResources.embedding_model = HuggingFaceEmbeddings(model_name=EMB_MODEL)
        
        # 2. Load ChromaDB
        log.info(f"Connecting to ChromaDB at {CHROMA_DIR} ...")
        GlobalResources.chroma_client = Chroma(
            persist_directory=CHROMA_DIR,
            embedding_function=GlobalResources.embedding_model
        )
        
        GlobalResources.nextcloud_collection = Chroma(
            collection_name="nextcloud_docs",
            embedding_function=GlobalResources.embedding_model,
            persist_directory=CHROMA_DIR
        )
        
        GlobalResources.ha_collection = Chroma(
            collection_name="ha_sensors",
            embedding_function=GlobalResources.embedding_model,
            persist_directory=CHROMA_DIR
        )
        
        # 3. Load Intent Engine
        from intent_engine import engine
        await engine.load()
        
        log.info("RAG Resources & Intent Engine loaded successfully.")
    except Exception as e:
        log.critical(f"CRITICAL: Failed to load RAG resources: {e}")
        log.critical(traceback.format_exc())

# --- LIFESPAN (Startup Logic) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Load RAG & Intents
    await initialize_rag_resources()

    # 2. Initialize Redis
    if redis:
        try:
            log.info(f"Connecting to Redis at {REDIS_URL} ...")
            GlobalResources.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            GlobalResources.redis_client.ping()
            log.info("Redis connection successful.")
        except Exception as e:
            log.warning(f"Failed to connect to Redis. Falling back to in-memory cache. Error: {e}")
            GlobalResources.redis_client = None
    else:
        log.warning("Redis library not installed. Falling back to in-memory cache.")
    
    yield
    
    log.info("--- SHUTDOWN: Cleaning up resources ---")
    GlobalResources.embedding_model = None
    GlobalResources.chroma_client = None
    GlobalResources.ha_collection = None
    GlobalResources.nextcloud_collection = None
    if GlobalResources.redis_client:
        try:
            GlobalResources.redis_client.close()
        except: pass
    GlobalResources.redis_client = None

# --- Caching ---
class TTLCache:
    def __init__(self):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str):
        async with self._lock:
            v = self._store.get(key)
            if not v: return None
            import time
            if time.time() - v[0] > QUERY_CACHE_TTL:
                del self._store[key]
                return None
            return v[1]

    async def set(self, key: str, value: Any):
        import time
        async with self._lock:
            self._store[key] = (time.time(), value)

_ha_cache = TTLCache()

async def ha_cache_get(user: str) -> Optional[str]:
    return await _ha_cache.get(user)

async def ha_cache_set(user: str, value: str):
    await _ha_cache.set(user, value)

# --- Utilities ---
async def run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, partial(fn, *args, **kwargs))

def get_user_creds(user: Optional[str] = None, token: Optional[str] = None):
    if token: return {"user": user or "API", "ha_token": token}
    user = user or HA_DEFAULT_USER
    ha_token = os.getenv(f"HA_{user}_TOKEN") or HA_ENV_TOKEN
    nc_pass = os.getenv(f"NEXTCLOUD_{user}_PASS") or NEXTCLOUD_PASS
    return {"user": user, "ha_token": ha_token, "nc_pass": nc_pass}

def load_system_prompt():
    default = "You are a helpful AI assistant. No emojis."
    if os.path.exists(SYSTEM_PROMPT_FILE):
        try:
            with open(SYSTEM_PROMPT_FILE, "r") as f: return f.read().strip()
        except Exception as e:
            log.error(f"Error loading system prompt from {SYSTEM_PROMPT_FILE}: {e}")
    else:
        log.warning(f"System prompt file not found at {SYSTEM_PROMPT_FILE}")
    return default
