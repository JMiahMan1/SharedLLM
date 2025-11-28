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
SYSTEM_PROMPT_FILE = os.getenv("SYSTEM_PROMPT_FILE", "/app/system_prompt.txt")

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

# --- Resource Loading (Hot Reloadable) ---
async def initialize_rag_resources():
    """Initializes or re-initializes the RAG components (Embedding Model & ChromaDB)."""
    log.info("--- Loading RAG Resources (Hot Reload) ---")
    try:
        os.environ["ANONYMIZED_TELEMETRY"] = "False"
        from langchain_huggingface import HuggingFaceEmbeddings
        from langchain_chroma import Chroma
        
        # Load Model (Cached by library usually, but safe to re-init)
        if not GlobalResources.embedding_model:
            log.info(f"Loading embedding model: {EMB_MODEL} ...")
            GlobalResources.embedding_model = HuggingFaceEmbeddings(model_name=EMB_MODEL)
        
        log.info(f"Connecting to ChromaDB at {CHROMA_DIR} ...")
        # Re-creating the client forces it to re-read the directory on disk
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
        log.info("RAG Resources loaded successfully.")
    except Exception as e:
        log.critical(f"CRITICAL: Failed to load RAG resources: {e}")
        log.critical(traceback.format_exc())

# --- LIFESPAN (Startup Logic) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Load RAG (Vector DB)
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
    default = "You are a helpful AI assistant."
    if os.path.exists(SYSTEM_PROMPT_FILE):
        try:
            with open(SYSTEM_PROMPT_FILE, "r") as f: return f.read().strip()
        except: pass
    return default
