# services/gateway/prompts.py
"""
Prompt management module.

All prompts are stored in the Identity service GlobalSettings table and loaded
from the DB at each call site so runtime changes take effect immediately.

Seed defaults live in prompts/*.md files and are loaded by the Identity seed
endpoint (seed_from_env) on first run or when --force is passed.

At runtime, prompts are ONLY read from the Identity DB.

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

import aiohttp

from services.gateway.config import IDENTITY_SVC, INTERNAL_SECRET

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
# Runtime loaders -- fetch fresh from Identity DB only
# =============================================================================

# Cached settings dict and client for sync access
_settings_cache: dict = {}
_settings_cache_time: float = 0
_settings_ttl = 30  # seconds


def load_prompt_sync(prompt_key: str) -> str:
    """Fetch a prompt from the Identity service GlobalSettings table (sync).
    
    Uses a cached settings cache to minimize overhead.
    
    Raises ValueError if the prompt key is not found in the DB.
    """
    import asyncio
    import concurrent.futures
    import time

    # Refresh settings cache if expired
    global _settings_cache, _settings_cache_time
    now = time.time()
    if not _settings_cache or (now - _settings_cache_time) > _settings_ttl:

        async def _fetch():
            from services.gateway.main import shared_http_client
            async with shared_http_client() as client, client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=5.0),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        return {item.get("key"): item.get("value") for item in data if isinstance(item, dict)}
                    return data
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop is None:
                # No running loop (e.g. sync/test context) — run directly.
                _settings_cache = asyncio.run(_fetch()) or {}
            else:
                # A loop is already running (async endpoint). Run the fetch in a
                # separate thread with its own event loop to avoid
                # "this event loop is already running" and to avoid deadlocking
                # the calling loop.
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    _settings_cache = pool.submit(lambda: asyncio.run(_fetch())).result(timeout=10) or {}
        except Exception:
            raise ValueError("Identity service unavailable and no cached prompts available")

        _settings_cache_time = now
    if _settings_cache.get(prompt_key):
        return _settings_cache[prompt_key]

    raise ValueError(f"Prompt not found in settings DB: {prompt_key}")


async def load_prompt(client: aiohttp.ClientSession, prompt_key: str) -> str:
    """Fetch a prompt from the Identity service GlobalSettings table (async).
    
    Raises ValueError if the prompt key is not found in the DB,
    ensuring fail-fast behavior when a prompt is missing.
    """
    resp = await client.get(
        f"{IDENTITY_SVC}/api/settings",
        headers={"X-Internal-Secret": INTERNAL_SECRET}
    )
    if resp.status != 200:
        raise RuntimeError(f"Identity service unavailable ({resp.status})")
    settings = {item["key"]: item["value"] for item in await resp.json()}
    value = settings.get(prompt_key)
    if not value:
        raise ValueError(f"Prompt not found in settings DB: {prompt_key}")
    return value
