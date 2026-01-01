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
log_file = os.getenv("LOG_FILE", "/data/app.log")
# Fallback if directory doesn't exist
if os.path.dirname(log_file) and not os.path.exists(os.path.dirname(log_file)):
    log_file = "app.log"

logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
)
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
SERVER_URL = os.getenv("SERVER_URL", "http://192.168.2.211:11435")  # External URL for video streaming
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS")
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
SYSTEM_PROMPT_FILE = os.getenv("SYSTEM_PROMPT_FILE", "/app/data/system_prompt.txt")

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen3:latest")
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
# Timeouts & Retries
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))
OLLAMA_RETRY = int(os.getenv("OLLAMA_RETRY", "1"))

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
INFORMATIONAL_INTENTS = [
    "time_query",
    "calendar_list",
    "timer_list",
    "web_search",
    "weather_query",
    "content_query",
    "calendar_read",
    "note_read",
    "note_list",
    "music_search",
    "music_list",
    "general_query",
]

# --- Alarm & Timer Config ---
ALARM_KEYWORDS_PATH = os.getenv(
    "ALARM_KEYWORDS_PATH", "/app/config/alarm_keywords.json"
)
ALARM_SOUNDS_DIR = os.getenv("ALARM_SOUNDS_DIR", "/local/alarm_sounds")


# --- Roku Configuration ---
ROKU_USE_MEDIA_ASSISTANT = os.getenv("ROKU_USE_MEDIA_ASSISTANT", "True") in ("1", "true", "True")


# --- Prompts (Externalized) ---
DEFAULT_CONTEXT_PROMPT = """Rewrite the following query to be self-contained, resolving pronouns and confirmations (Yes/No) using history.
 Examples:
 History: [User: Play music, Assistant: TV is off. Turn on?] Input: [Yes] -> Refined: [Turn on TV and play music]
 History: [User: Who is Barack Obama?] Input: [How old is he?] -> Refined: [How old is Barack Obama]
 History:
 {history}
 Input: {query}
 Refined (Return ONLY the refined query string):"""
CONTEXT_REWRITE_PROMPT = os.getenv("CONTEXT_REWRITE_PROMPT", DEFAULT_CONTEXT_PROMPT)

DEFAULT_CALENDAR_PROMPT = """Extract details from: "{query}".
Return JSON with keys: 'summary' (string), 'start_time' (natural language), 'calendar_target' (string or null), 'intent' ('add', 'delete', 'update').
IMPORTANT: 'summary' MUST be the event title. If input is 'RAG_Test_123', summary is 'RAG_Test_123'.
JSON:"""
CALENDAR_EXTRACT_PROMPT = os.getenv("CALENDAR_EXTRACT_PROMPT", DEFAULT_CALENDAR_PROMPT)

DEFAULT_ORCHESTRATOR_PROMPT = """You are an action planning agent. Your task is to analyze the user's intent and decide the next action based on the available tools.
User Query: {query}
Best Vector Intent Match: {intent_name} (Confidence: {intent_score:.2f})
{conversation_history}

Available Tools:
1. 'calendar_add' (Schedule/create an event. Keywords: schedule, meeting, appointment, calendar)
2. 'calendar_delete' (Cancel an event by fuzzy name match)
3. 'calendar_list' (List available calendars)
4. 'calendar_read' (Read upcoming events from your calendars)
5. 'calendar_update' (Reschedule an existing event)
6. 'timer_add' (Set a timer or alarm. Keywords: timer, alarm, wake me, remind me in X minutes)
7. 'timer_delete' (Cancel a timer/alarm)
8. 'timer_list' (List active timers/alarms)
9. 'timer_pause' (Pause a timer)
10. 'timer_resume' (Resume a timer)
11. 'media_command' (Handle media/HA control. Requires 'intent' and 'device_name'. For play_media, can include 'media_title')
12. 'intent_learn' (Teach the AI a new phrase mapping)
13. 'web_search' (Use for factual/external queries, if no other tool applies. If using web_search, make the query specific and contextual based on conversation history)
14. 'note_add' (Create a new note. Params: 'title', 'content')
15. 'note_append' (Append to a note/list. Params: 'title', 'content')
16. 'note_read' (Read a specific note file. Params: 'title')
17. 'note_delete' (Delete a note file. Params: 'title')
18. 'note_update' (Overwrite/Update a note. Params: 'title', 'content')
19. 'music_list' (List playlists or radio stations in Music Assistant)
20. 'music_search' (Search Music Assistant library for artist/album/track)
21. 'ha_notify' (Send a persistent notification to Home Assistant. Params: 'message', 'title')

CRITICAL: Distinguish between Alarms/Timers and Calendar Events.
- "Set an alarm for 8am" -> timer_add
- "Remind me in 10 minutes" -> timer_add
- "Wake me up at 7" -> timer_add
- "Schedule a meeting at 8am" -> calendar_add
- "Add to my calendar" -> calendar_add

MEDIA COMMAND EXAMPLES:
- "Play Brandon Lake on the Office TV" -> {{"action": "tool_call", "tool_name": "media_command", "parameters": {{"intent": "play_media", "device_name": "Office TV", "media_title": "Brandon Lake"}}}}
- "Turn on the living room light" -> {{"action": "tool_call", "tool_name": "media_command", "parameters": {{"intent": "turn_on", "device_name": "living room light"}}}}

WEB SEARCH CONTEXT: When using web_search, ALWAYS make the query specific and contextual. If the user is asking follow-up questions, incorporate the context from previous messages. For example, if they first asked about "weather in New York" and then "is it going to rain?", the web_search query should be "will it rain today in New York?" not just "will it rain today?".

If the intent is a clear, confident action, generate the JSON for a tool call.
If the query is conversational, informational, ambiguous, or requires the user's personal context/RAG (e.g., "What devices are in the office?", "Is the garage door open?"), output 'CONVERSE'.

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

### CHAT HISTORY
{history}

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


# User management is imported at the end to avoid circular imports


# --- Resource Loading (Hot Reloadable) ---
async def load_resources():
    log.info("Loading Global Resources...")

    # 1. Redis
    if redis and REDIS_URL:
        try:
            GlobalResources.redis_client = redis.Redis.from_url(
                REDIS_URL, decode_responses=True
            )
            GlobalResources.redis_client.ping()
            log.info("Redis Connected.")
        except Exception as e:
            log.error(f"Redis Connection Failed: {e}")
            GlobalResources.redis_client = None

    # 2. Embeddings (Mock or Real)
    if EMB_MODEL:
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            GlobalResources.embedding_model = HuggingFaceEmbeddings(
                model_name=EMB_MODEL
            )
            log.info(f"Embedding Model Loaded: {EMB_MODEL}")
        except Exception as e:
            log.error(f"Failed to load embedding model: {e}")

    # 3. ChromaDB (Vector Store)
    if CHROMA_DIR and GlobalResources.embedding_model:
        try:
            import chromadb
            from langchain_chroma import Chroma

            # Initialize Native Client for Direct Access (Health/Upsert)
            GlobalResources.chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

            # Nextcloud Collection
            GlobalResources.nextcloud_collection = Chroma(
                client=GlobalResources.chroma_client,
                collection_name="nextcloud_docs",
                embedding_function=GlobalResources.embedding_model,
            )
            # Home Assistant Collection
            GlobalResources.ha_collection = Chroma(
                client=GlobalResources.chroma_client,
                collection_name="home_assistant",
                embedding_function=GlobalResources.embedding_model,
            )
            log.info(f"ChromaDB Loaded from {CHROMA_DIR}")
        except Exception as e:
            log.error(f"ChromaDB Load Failed: {e}")


# --- Lifespan moved to main.py to avoid circular imports ---




# --- Caching ---
# Simple in-memory cache for HA states to reduce API spam
ha_state_cache = {}


# --- Utilities ---
def get_ha_state(entity_id: str) -> Optional[Dict]:
    # TODO: Implement proper caching with TTL
    return ha_state_cache.get(entity_id)


# --- User Management (imported here to avoid circular imports) ---
from .users import get_user_creds
