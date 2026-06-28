# services/gateway/prompts.py
"""
Prompt management module.

All prompts are stored in the Identity service GlobalSettings table and loaded
from the DB at each call site so runtime changes take effect immediately.

Seed defaults live in .env (PROMPT_assistant_system_instruction, etc.)
and are used by the Identity seed endpoint on first run.

PROMPT KEYS (must match Identity DEFAULT_GLOBAL_SETTINGS):
    assistant_system_instruction   -- main assistant / Jarvis persona
    librarian_system_instruction   -- librarian/RAG persona (alias for assistant)
    code_helper_system_instruction  -- code helper / coding agent
    media_troubleshooting_prompt    -- media failure troubleshooting
    raven_autonomous_protocol       -- Raven autonomous repair agent
    raven_narrator_protocol         -- Raven TTS narrator
    raven_plan_prompt               -- Raven planning module
    raven_reflection_prompt         -- Raven post-mission reflection
    single_turn_tool_guide          -- single-turn tool execution guide

UNUSED PROMPTS REMOVED:
    LOG_SUMMARY_PROMPT              -- never referenced in codebase
    AUTONOMOUS_EVOLUTION_AGENT_PROMPT -- predecessor prompt, content merged
                                         into ASSIST_SYSTEM_INSTRUCTION and
                                         RAVEN_AUTONOMOUS_PROTOCOL
"""

import os
import httpx
from dotenv import load_dotenv

# Load .env so prompt seeds are available
load_dotenv()

from services.gateway.config import INTERNAL_SECRET, IDENTITY_SVC


# =============================================================================
# Prompt key constants (settings DB lookup keys)
# =============================================================================
PROMPT_ASSISTANT_SYSTEM_INSTRUCTION = "assistant_system_instruction"
PROMPT_LIBRARIAN_SYSTEM_INSTRUCTION = "librarian_system_instruction"
PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION = "code_helper_system_instruction"
PROMPT_MEDIA_TROUBLESHOOTING = "media_troubleshooting_prompt"
PROMPT_RAVEN_AUTONOMOUS_PROTOCOL = "raven_autonomous_protocol"
PROMPT_RAVEN_NARRATOR_PROTOCOL = "raven_narrator_protocol"
PROMPT_RAVEN_PLAN = "raven_plan_prompt"
PROMPT_RAVEN_REFLECTION = "raven_reflection_prompt"
PROMPT_SINGLE_TURN_TOOL_GUIDE = "single_turn_tool_guide"


# =============================================================================
# Seed defaults from .env (for Identity seed script and test compatibility)
# =============================================================================
_SEED_ENV_PREFIX = "PROMPT_"


def _load_seed_from_env(key: str) -> str:
    """Load a prompt seed from .env via the PROMPT_ prefix convention."""
    env_var = f"{_SEED_ENV_PREFIX}{key}"
    value = os.environ.get(env_var, os.getenv(env_var, ""))
    if not value:
        raise ValueError(f"Prompt seed not found in .env: {env_var}")
    return value


def get_seed_prompt(key: str) -> str:
    """Public helper for tests and seed scripts to load a prompt from .env."""
    return _load_seed_from_env(key)


# =============================================================================
# Backward-compat aliases (tests and external references import these from main.py)
# These load from .env seeds at import time.
# =============================================================================
ASSIST_SYSTEM_INSTRUCTION = get_seed_prompt(PROMPT_ASSISTANT_SYSTEM_INSTRUCTION)
CODE_HELPER_SYSTEM_INSTRUCTION = get_seed_prompt(PROMPT_CODE_HELPER_SYSTEM_INSTRUCTION)


# =============================================================================
# Runtime loaders -- fetch fresh from Identity DB
# =============================================================================

# Cached settings dict and client for sync access
_settings_cache: dict = {}
_settings_cache_time: float = 0
_settings_ttl = 30  # seconds
_sync_client: httpx.Client | None = None


def _ensure_sync_client() -> httpx.Client:
    global _sync_client
    if _sync_client is None:
        _sync_client = httpx.Client(timeout=5.0)
    return _sync_client


def load_prompt_sync(prompt_key: str) -> str:
    """Fetch a prompt from the Identity service GlobalSettings table (sync).
    
    Uses a cached httpx client and settings cache to minimize overhead.
    Falls back to seed values from .env if Identity service is unavailable.
    
    Raises ValueError if the prompt key is not found in the DB or .env.
    """
    import time
    
    # Refresh settings cache if expired
    global _settings_cache, _settings_cache_time
    now = time.time()
    if not _settings_cache or (now - _settings_cache_time) > _settings_ttl:
        try:
            client = _ensure_sync_client()
            resp = client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                _settings_cache = {item["key"]: item["value"] for item in resp.json()}
                _settings_cache_time = now
        except Exception:
            # If Identity is unavailable, fall back to seeds
            pass
    
    if prompt_key in _settings_cache and _settings_cache[prompt_key]:
        return _settings_cache[prompt_key]
    
    # Fallback to .env seed
    try:
        return _load_seed_from_env(prompt_key)
    except ValueError:
        raise ValueError(f"Prompt not found in settings DB or .env seed: {prompt_key}")


async def load_prompt(client: httpx.AsyncClient, prompt_key: str) -> str:
    """Fetch a prompt from the Identity service GlobalSettings table (async).
    
    Raises ValueError if the prompt key is not found in the DB,
    ensuring fail-fast behavior when a prompt is missing.
    """
    resp = await client.get(
        f"{IDENTITY_SVC}/api/settings",
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Identity service unavailable ({resp.status_code})")
    settings = {item["key"]: item["value"] for item in resp.json()}
    value = settings.get(prompt_key)
    if not value:
        raise ValueError(f"Prompt not found in settings DB: {prompt_key}")
    return value
