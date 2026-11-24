# services/gateway/orchestrator.py
import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

import aiohttp

from services.common.backend_health import BackendUnavailable
from services.common.circuit_breaker import CircuitOpenError, get_breaker
from services.common.rag_collections import HOME_DEVICES, canonical_collection
from services.gateway import context_router, library_names, prompt_budget, tool_cache
from services.gateway.config import (
    CHAPTER_PREFETCH_MAX_CHARS,
    CONTROL_PLANE_URL,
    EXECUTION_SVC,
    IDENTITY_SVC,
    INTERNAL_SECRET,
    LOGGING_SVC,
    RAG_HIT_MAX_CHARS,
    RAG_PREFETCH_K,
    RAG_PREFETCH_MAX_CHARS,
    RAG_PREFETCH_MIN_SIMILARITY,
    RAG_PREFETCH_VOICE_MAX_CHARS,
    RAG_SEARCH_MIN_SIMILARITY,
    RAG_SVC,
    STORAGE_SVC,
    WORKSPACE_RUNTIME_SVC,
)
from services.gateway.device_claims import DEVICE_TOOLS, drop_repeated_request, honest_reply
from services.gateway.history import get_history
from services.gateway.intent_engine import is_raven_intent
from services.gateway.knowledge_map import knowledge_map_for
from services.gateway.llm_providers import get_provider
from services.gateway.location_query import parse_location_query
from services.gateway.prompts import PROMPT_ASSISTANT_PERSONA, PROMPT_SINGLE_TURN_TOOL_GUIDE, load_prompt_sync
from services.gateway.schemas import ResolvedCredentials
from services.gateway.studio import actions as studio_actions
from services.gateway.studio import tools as studio_tools
from services.gateway.tool_registry import (
    KNOWLEDGE_SEARCH_DEFAULT_K,
    KNOWLEDGE_SEARCH_MAX_K,
    SINGLE_TURN_GARDEN_TOOLS,
    hub_tool_guide,
    names_withheld_hub_tool,
    promote_tool_discriminator,
    single_turn_tools,
)

log = logging.getLogger("gateway.orchestrator")

#: Largest retrieved-context block sent to a model. The always-on curriculum
#: can push a family-chat prompt past an 8k-token window; this keeps every
#: single-turn request inside the context Ollama actually loaded.
MAX_RAG_CONTEXT_CHARS = 12000

#: A spoken question gets a short spoken answer, and every token read before it is
#: time the person stands waiting: 25k characters of context once took 38 s to read.
#: A voice turn pre-fetches this much (devices first) and skips Raven's always-on curriculum.
VOICE_RAG_CHARS = RAG_PREFETCH_VOICE_MAX_CHARS
VOICE_RAG_HITS = 6
#: A spoken follow-up ("turn it off", "and the kitchen?") needs the last exchange or two.
#: The household's last ten messages (long answers included) were about 2,000 tokens,
#: read again before every spoken answer: about 10 s on the household's card.
VOICE_HISTORY_MESSAGES = 4
VOICE_HISTORY_CHARS = 400


#: The place a request ends on: "... on the den speaker", "... in the sunroom" (any wording a household uses).
_TRAILING_PLACE_RE = re.compile(r"\b(?:on|to|in|via|using)\s+the\s+([A-Za-z][A-Za-z' ]*?)\s*[.!?]*\s*$", re.IGNORECASE)


def _trailing_place(query: str) -> str | None:
    """The place a request ends on ("play jazz in the den" -> "den"), or None.

    Nothing here knows any room: execution matches what was said against the
    hub's areas (names and aliases) and then its device names, so every
    household's own rooms work without a list of room words in code.
    """
    m = _TRAILING_PLACE_RE.search(query or "")
    return m.group(1).strip() if m else None


def _voice_history(history: list, query: str = "") -> list:
    """The last exchanges of a spoken conversation, each cut to a sentence or two.

    An earlier exchange for the same words as ``query`` is left out: a repeated request is
    wanted again, and shown its old answer the model repeated that answer instead of acting
    (``device_claims``).
    """
    out = []
    for message in drop_repeated_request(history, query)[-VOICE_HISTORY_MESSAGES:]:
        if not isinstance(message, dict):
            continue
        content = str(message.get("content") or "")
        if len(content) > VOICE_HISTORY_CHARS:
            content = content[:VOICE_HISTORY_CHARS].rstrip() + "..."
        out.append({**message, "content": content})
    return out


def _conversation_of(job_payload: dict) -> str:
    """A stable id for the conversation a job belongs to (the tool cache's scope).

    A voice satellite's own conversation id, else the satellite; a workspace; a Talk
    room; a caller-supplied id; else the person's one rolling chat on that client.
    """
    voice = job_payload.get("voice_context") or {}
    for key, value in (
        ("voice", voice.get("conversation_id") if isinstance(voice, dict) else None),
        ("satellite", voice.get("satellite") if isinstance(voice, dict) else None),
        ("mission", job_payload.get("_mission_id")),
        ("workspace", job_payload.get("workspace_id")),
        ("talk", job_payload.get("_talk_token")),
        ("chat", job_payload.get("conversation_id")),
    ):
        if value:
            return f"{key}:{value}"
    return f"client:{job_payload.get('client') or 'chat'}:{job_payload.get('device_id') or ''}"


def _history_chars(history: list) -> int:
    """Characters of conversation history a prompt carries."""
    return sum(len(str(m.get("content") or "")) for m in history if isinstance(m, dict))


def _truncate_rag_context(rag_context: str) -> str:
    """Trim retrieved context to a size that fits the model window."""
    if len(rag_context) <= MAX_RAG_CONTEXT_CHARS:
        return rag_context
    return (
        rag_context[:MAX_RAG_CONTEXT_CHARS].rstrip()
        + "\n\n[Retrieved context truncated to fit the model window.]"
    )

# Vocabulary that means "the answer is probably in something I have read", which
# is what a Calibre library of 3,000 books holds.
#
# This list is deliberately narrower than LIBRARIAN_SIGNALS. That one decides
# which MODEL answers, so it can afford to be broad -- "notes" or "music" are
# good enough reasons to use the Librarian. This one decides whether to spend
# the context budget on book text, so it must not fire on a merely Librarian-ish
# question: adding it there would silently pull books into every music or notes
# answer. Bare verbs are excluded for the same reason -- "read the logs" is a
# systems task, and "read" cannot be a book signal without catching it.
LIBRARY_INTENT_SIGNALS = (
    "book", "books", "bookshelf", "library", "libraries", "calibre", "calibre library",
    "ebook", "ebooks", "e-book", "author", "authors", "wrote by", "written by",
    "commentary", "commentaries", "sermon", "sermons", "preach", "preaching",
    "preached", "homily", "devotional", "devotionals", "lectionary", "hymn",
    "hymns", "theology", "meditation", "meditate", "quote", "quotation", "cite",
    "passage", "exposition", "gospel", "epistle", "psalm", "psalms", "proverb",
    "proverbs", "testament", "chapter", "verse", "scripture", "biblical",
    # The family keeps books on fasting and the early church. Not "fast"
    # (breakfast) or "lent" (excellent): signals match as substrings.
    "fasting", "fasted", "early church", "church fathers", "patristic",
)

# How a collection is named to the model. The internal name is an identifier,
# not prose, and "CALIBRE_FILES" tells the Librarian nothing about what it can
# answer. Anything absent falls back to the uppercased collection name.
COLLECTION_HEADERS = {
    # The instruction to answer from these passages is load-bearing, not
    # decoration. Observed: given a Macduff question with the right passages
    # already in its context, the model threw them away, called
    # ContextSearchRequest without a collection (landing on the default
    # system_capabilities), and answered "no results were found" while the
    # evidence sat unread in its own system prompt.
    "calibre_files": (
        "LIBRARY: TEXT FROM YOUR BOOK LIBRARY, ALREADY RETRIEVED FOR THIS "
        "QUESTION. Answer from these passages and cite the bracketed source and "
        "chapter. Do not call ContextSearchRequest to look them up again; only "
        "search the library if these passages plainly do not answer the question."
    ),
    "user_facts": (
        "USER FACTS & PREFERENCES: remembered facts about the person asking; "
        "apply them directly when relevant."
    ),
    "mission_history": (
        "PAST MISSION HISTORY: outcomes and errors from prior Raven missions; "
        "reuse fixes and avoid repeating past failures."
    ),
    "conversation_memory": (
        "CONVERSATION MEMORY: recent household intercom transcripts and spoken exchanges."
    ),
    "network_topology": (
        "NETWORK TOPOLOGY: discovered Docker containers, services, ports, and IP addresses."
    ),
    "telemetry_alerts": (
        "TELEMETRY ALERTS: recent device, sensor, and service alerts."
    ),
}


def _collection_header(collection: str) -> str:
    """Return the prompt header for a collection name."""
    return COLLECTION_HEADERS.get(collection, collection.upper())


_GROUNDED_OMITTED_TOOLS = ("### ContextSearchRequest", "### WebSearchRequest", "### CalibreRequest")

_GROUNDED_OMITTED_ACTIONS = ("contextsearchrequest", "websearchrequest", "calibrerequest")

_GROUNDING_NOTICE = (
    "Note: retrieval, library-fetch, and web-search tools are deliberately "
    "omitted for this turn. The retrieved context for this question is already "
    "included below, so answer from it and cite it rather than looking "
    "anything up.\n\n"
)


def _grounded_tool_guide(guide: str) -> str:
    """Strip the retrieval tools from the tool guide when context is already loaded.

    A grounded turn has already paid for retrieval, so advertising a search tool
    is an invitation to discard it. Prompting is not enough: on 2026-10-06 the
    Librarian was given 15,913 characters of its own book passages with an
    explicit instruction not to re-search them, called WebSearchRequest anyway,
    and returned a summary of Shakespeare's Macbeth for a question about John
    R. Macduff. Absence is the only lever that has held, so the sections go.
    """
    parts = re.split(r"(?m)^(?=#{2,3} )", guide)
    kept = [p for p in parts if not any(p.startswith(omit) for omit in _GROUNDED_OMITTED_TOOLS)]
    joined = "".join(kept)
    marker = "## Available Tools"
    if marker in joined:
        return joined.replace(marker, f"{marker}\n\n{_GROUNDING_NOTICE}", 1)
    return f"{_GROUNDING_NOTICE}{joined}"


def _library_intent_asked(query: str) -> bool:
    """True when a query names the kind of thing a book library answers."""
    q = query.lower()
    return any(signal in q for signal in LIBRARY_INTENT_SIGNALS)


def _collections_for_query(
    query: str,
    *,
    include_library: bool = False,
    devices_first: bool = False,
    intent: str | None = None,
    confidence: float = 0.0,
) -> list[str]:
    """Choose which RAG collections a query may pre-fetch from (``context_router``).

    Only what the question's intent or wording names: a question that names no source
    pre-fetches nothing and the model fetches what it needs through the tools the
    knowledge map lists. A standing set searched for every question is what gave
    "What's the verse of the day?" 45 unrelated rows.

    A book library is not searched unless the query asks for one (``LIBRARY_INTENT_SIGNALS``)
    or the caller already knows books are wanted (``include_library``: the research mode,
    where naming an author is the natural way to ask and no keyword list can match every
    surname). When it is searched it goes first: a passage is the strongest evidence.

    No home hub set up: the device index is left out, as the hub tools are.
    """
    from services import config

    return context_router.route_collections(
        query,
        intent=intent,
        confidence=confidence,
        library=include_library or (_library_intent_asked(query) and not context_router.tool_answers(query)),
        hub=config.any_home_hub(),
        devices_first=devices_first,
    )


# --- Default service URLs (Docker DNS), overridable via Identity settings ---
_DEFAULTS = cast(dict[str, str], {
    "identity_svc_url": IDENTITY_SVC,
    "execution_svc_url": EXECUTION_SVC,
    "rag_svc_url": RAG_SVC,
    "storage_svc_url": STORAGE_SVC,
    "logging_svc_url": LOGGING_SVC,
    "workspace_runtime_svc_url": WORKSPACE_RUNTIME_SVC,
    "control_plane_url": CONTROL_PLANE_URL,
    "llm_local_url": "",  # .env is seed-only; runtime resolved from Identity settings
    "redis_url": "redis://redis:6379/0",
    "ollama_timeout": "600",
    "fast_path_threshold": "0.85",
    # Only consulted when Identity has no value for the key, so it must match
    # the seeded row (services/identity/models.py) and services/config.py.
    "raven_max_total_seconds": "1800",
    "raven_iteration_timeout": "600",
    "raven_heartbeat_interval": "30",
    "raven_hung_threshold": "600",
    "raven_check_interval": "300",
    "raven_error_threshold": "5",
    "timezone": "",  # Resolved at runtime from Identity settings
    "embedding_model": "nomic-ai/nomic-embed-text-v1.5",
})

async def get_all_settings() -> dict[str, str]:
    """Fetches ALL configuration from Identity service (single source of truth).

    Results are served from the shared in-memory TTL cache (see
    ``services.gateway.cache``) and invalidated on any settings write.
    """
    from services.gateway.cache import get_cached_settings

    async def _fetch() -> dict[str, str]:
        from services.gateway.main import shared_http_client
        async with shared_http_client() as client:
            resp = await client.get(
                f"{IDENTITY_SVC}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=3.0),
            )
            if resp.status != 200:
                raise RuntimeError(f"Identity settings returned HTTP {resp.status}")
            fetched = {item["key"]: item["value"] for item in await resp.json()}
            # Merge with defaults for non-model keys only
            model_keys = {
                "active_llm_provider", "assistant_model", "coding_model", "librarian_model",
            "telemetry_model",
            }
            for key, default in _DEFAULTS.items():
                if key in model_keys:
                    continue
                if key not in fetched or not fetched[key]:
                    fetched[key] = default
            # Sync module-level constants in main.py for backward compat
            _sync_main_constants(fetched)
            return fetched

    return await get_cached_settings(_fetch, dict(_DEFAULTS))
def _as_meta_dict(value):
    """Normalise a RAG hit's metadata into a dict.

    ``/rag/sync/files`` stringifies every metadata value that is not a scalar,
    so a chapter number ingested as a JSON number comes back as ``"7"``, and a
    nested object comes back as a stringified dict. Unquoting scalars is
    safe and worth doing; a string that parses as a dict is worth parsing.
    Anything else is left exactly as it is.
    """
    if isinstance(value, dict):
        out = dict(value)
    elif isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        out = parsed if isinstance(parsed, dict) else {}
    else:
        return {}

    for key, val in list(out.items()):
        if not isinstance(val, str):
            continue
        if val.startswith('"') and val.endswith('"') and len(val) > 1:
            try:
                decoded = json.loads(val)
            except (ValueError, TypeError):
                continue
            if isinstance(decoded, (int, float, bool)) or decoded is None:
                out[key] = decoded
            continue
        # A locator that is really a number is stored as its str() form, which
        # would otherwise render as "chapter 12" only by luck of formatting and
        # would compare unequal to any numeric locator from another source.
        if val.strip().lstrip("-").isdigit():
            out[key] = int(val.strip())
        elif _is_plain_float(val):
            out[key] = float(val.strip())
    return out


def _is_plain_float(value: str) -> bool:
    try:
        float(value)
    except (ValueError, TypeError):
        return False
    return "." in value


def _citation_suffix(meta: dict) -> str:
    """Render a hit's locator so the model can attribute what it quotes.

    Without this, every non-lesson hit reaches the prompt as bare prose: the
    locator was stored at ingest and retrievable over the API, then dropped
    here, so two chunks from different chapters of the same book were
    indistinguishable. Kept compact because it is charged against the same
    character budget as the chunk itself.
    """
    if not isinstance(meta, dict):
        return ""
    if meta.get("is_metadata"):
        return ""
    doc = meta.get("title") or meta.get("name")
    if isinstance(doc, str) and doc.startswith("File/Folder:"):
        doc = None
    # An author turns a bare title into an attribution. A question naming an
    # author ("what did Macduff say about poverty") cannot be answered from
    # passages labelled only "Memories of Bethany, XIV": the model has no way to
    # know the book it is quoting is by the author being asked about, and it
    # answers that it found nothing rather than guessing.
    who = meta.get("author")
    if isinstance(who, str) and who.strip():
        who = who.strip()
        doc = f"{doc} by {who}" if doc else f"by {who}"
    loc = ""
    for key in ("chapter", "page", "section", "location"):
        val = meta.get(key)
        if val not in (None, "", 0, "0"):
            loc = f"{key} {val}"
            break
    if not loc:
        idx = meta.get("chunk_index")
        if idx not in (None, ""):
            loc = f"chunk {idx}"
    bits = [str(b) for b in (doc, loc) if b]
    if not bits:
        return ""
    return f" (Source: {', '.join(bits)})"




def _sync_main_constants(settings: dict[str, str]) -> None:
    """Update module-level constants in main.py for backward compatibility."""
    import services.gateway.main as main_mod
    mappings = {
        "identity_svc_url": "IDENTITY_SVC",
        "execution_svc_url": "EXECUTION_SVC",
        "rag_svc_url": "RAG_SVC",
        "storage_svc_url": "STORAGE_SVC",
        "logging_svc_url": "LOGGING_SVC",
        "workspace_runtime_svc_url": "WORKSPACE_RUNTIME_SVC",
        "control_plane_url": "CONTROL_PLANE_URL",
        "llm_local_url": "LLM_URL",
    }
    for key, attr in mappings.items():
        val = settings.get(key)
        if val and hasattr(main_mod, attr):
            setattr(main_mod, attr, val)


_MODEL_KEYS = {
    "active_llm_provider", "assistant_model", "coding_model", "librarian_model",
}

def _get(settings: dict[str, str], key: str, default: str = "") -> str:
    """Get setting with fallback to defaults (excludes model keys).

    Service URLs prefer the variant for this process's network
    (``host_rag_svc_url`` vs ``bridge_rag_svc_url``). The orchestrator also runs
    inside the host-networked execution service (Jarvis answering in Talk),
    where the plain ``rag_svc_url`` (``http://rag:8004``, a Docker-network name)
    does not resolve, so every Talk answer was given without RAG context.
    """
    if key.endswith("_svc_url"):
        from services.config import NETWORK_MODE

        scoped = settings.get(f"{NETWORK_MODE}_{key}", "")
        if scoped:
            return scoped
    val = settings.get(key, "")
    if not val:
        if key in _MODEL_KEYS:
            return default
        return _DEFAULTS.get(key, default)
    return val


def strip_json_from_response(text: str) -> str:
    """
    Extract natural language from LLM output.
    Handles: pure JSON, markdown-wrapped JSON, thinking tags, tool call artifacts.
    Returns the most human-readable portion of the text.
    """
    if not text or not text.strip():
        return text

    # Strip thinking tags content
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # If text doesn't look like JSON at all, return as-is
    if not text.strip().startswith(("{", "```")):
        return text.strip()

    # Try to extract natural language that appears BEFORE or AFTER JSON blocks
    # Many models output: "Sure, I'll do that.\n```json{...}```"
    # We want the "Sure, I'll do that." part
    # Remove markdown JSON blocks
    cleaned = re.sub(r"```json\s*\{.*?\}\s*```", "", text, flags=re.DOTALL).strip()
    # Remove bare JSON objects
    cleaned = re.sub(r"\{[^{}]*\}", "", cleaned, flags=re.DOTALL).strip()
    # Remove remaining braces-wrapped content
    cleaned = re.sub(r"\{.*?\}", "", cleaned, flags=re.DOTALL).strip()

    if cleaned.strip():
        return cleaned.strip()

    # If all we have is JSON, try to extract a human-readable message field
    try:
        # Try parsing the whole text as JSON
        parsed = json.loads(text)
        for key in ["message", "response", "answer", "text", "content", "reply", "summary"]:
            if key in parsed and isinstance(parsed[key], str):
                return parsed[key]
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from markdown-wrapped JSON
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            for key in ["message", "response", "answer", "text", "content", "reply"]:
                if key in parsed and isinstance(parsed[key], str):
                    return parsed[key]
        except (json.JSONDecodeError, ValueError):
            pass

    # Last resort: return text stripped
    return text.strip()

# Tool routing comes from services.gateway.tool_registry, the table Raven and
# the OpenAI tool surface also read. This path used to keep a hand-copied table
# that drifted from it (missing tools, a 404 path, a wrong service); only the
# two tools Raven has no row for are added here.
_SINGLE_TURN_ONLY_TOOLS: dict[str, tuple[str, str]] = {
    # Raven does not dispatch a mission from inside a mission.
    "ravenmissionrequest": ("identity", "/api/raven/missions"),
    # Location is answered for chat and voice; Raven has no use for it.
    "locationrequest": ("execution", "/execute/location"),
}
#: Service key of the tools the gateway runs itself (the Workspace Studio).
GATEWAY_IN_PROCESS = "gateway_in_process"

_SERVICE_SETTINGS_KEY = {
    "execution": "execution_svc_url",
    "workspace_runtime": "workspace_runtime_svc_url",
    "rag": "rag_svc_url",
    "storage": "storage_svc_url",
    "identity": "identity_svc_url",
    # The Workspace Studio lives in this process: _execute_single_tool runs its
    # rows in-process (studio/tools.py), never over HTTP. The key names no URL
    # setting, so a row that ever reached the HTTP path fails loudly.
    "gateway": GATEWAY_IN_PROCESS,
}
_SINGLE_TURN_TOOLS = {**single_turn_tools(), **_SINGLE_TURN_ONLY_TOOLS}

# Tool endpoint map (service base URL resolved at runtime from Identity)
SINGLE_TURN_TOOL_ENDPOINTS: dict[str, str] = {name: path for name, (_svc, path) in _SINGLE_TURN_TOOLS.items()}

# Tool -> service settings key, for every tool not on the execution service.
# Gateway rows (the Workspace Studio) map to GATEWAY_IN_PROCESS and are run
# in-process by _execute_single_tool.
_TOOL_SERVICE_MAP = {
    name: _SERVICE_SETTINGS_KEY[svc]
    for name, (svc, _path) in _SINGLE_TURN_TOOLS.items()
    if svc != "execution"
}

# Tools that legitimately run for minutes. Everything else gets 60 seconds.
_SLOW_TOOL_TIMEOUTS = {
    "sttrequest": 600.0,
    "podcastrenderrequest": 600.0,
    "audiobookregeneraterequest": 600.0,
    "imageeditrequest": 300.0,
    "ttsrequest": 300.0,
    "ocrrequest": 180.0,
    "webscraperrequest": 180.0,
    "browserautomationrequest": 180.0,
}


async def get_llm_settings() -> dict[str, str]:
    """Fetches full LLM settings from Identity service (cached)."""
    return await get_all_settings()


async def call_ollama(payload: dict[str, Any], use_chat: bool = True) -> dict[str, Any]:
    """
    Legacy-compatible inference seam for tests and direct provider calls.
    The underlying provider is still resolved dynamically from Identity settings.
    """
    settings = await get_llm_settings()
    provider = await get_provider(settings)
    content = await provider.generate(
        payload["model"],
        payload["messages"],
        options=payload.get("options", {}),
        chunk_callback=payload.get("chunk_callback"),
    )
    return {"message": {"content": content}}

async def process_full_orchestration(job_payload: dict[str, Any], chunk_callback: Callable[[str], Awaitable[None]] | None = None) -> str:
    """
    Handles the full Jarvis orchestration pipeline:
    Decompose -> Memory -> RAG -> Inference -> Tools -> Update.
    """
    query = job_payload["query"]
    user_id = job_payload["creds"]["user"]
    creds = ResolvedCredentials(**job_payload["creds"])
    model = job_payload["model"]
    show_thinking = job_payload.get("show_thinking", False)
    grounded = bool(job_payload.get("grounded"))

    log.info(f"[Orchestrator] Job model from payload: '{model}'")

    # 0. Query-based Model Override (e.g. "Raven use model qwen2.5:32b fix...")
    model_match = re.search(r"(?:use model|with model|run on model)\s+([a-zA-Z0-9.\-_:]+)", query, re.IGNORECASE)
    if model_match:
        model = model_match.group(1)
        log.info(f"[Orchestrator] Dynamic model override detected: {model}")

    log.info(f"[Orchestrator] Starting orchestration for query: {query[:50]}...")

    # Hardened intent: only flag a Raven mission when the prompt explicitly
    # invokes Raven AND issues a command. Bare action verbs (e.g. "fix the
    # app") without the Raven keyword no longer auto-route to the autonomous
    # queue / AgentLoop.
    #
    # BUT: a job carrying a `_mission_id` was explicitly submitted through the
    # Raven mission pipeline (POST /api/raven/missions). Such jobs MUST run the
    # autonomous AgentLoop regardless of the query prose; otherwise an
    # open-ended mission whose text lacks the literal "raven" keyword is
    # misrouted to the single-turn Librarian path, which cannot create
    # workspaces (observed: WorkspaceCreateRequest "not supported in the
    # standard path" -> 502/404 workspace-not-found).
    is_autonomous = not grounded and (bool(job_payload.get("_mission_id")) or is_raven_intent(query))

    # 1. Retrieve Memory
    # A queued chat turn has no request transcript to carry: the worker
    # attaches the recent turns to the payload - per-user history for chat
    # jobs, the room's own transcript for Nextcloud Talk turns. Jobs dispatched
    # directly, and jobs queued before the worker started attaching history,
    # fall back to the per-user history store. Missions are autonomous and keep
    # an empty short-term history; a Talk turn without an attached transcript
    # stays empty too rather than borrowing the speaker's personal history.
    if grounded or is_autonomous:
        short_term = []
    elif "history" in job_payload:
        short_term = job_payload.get("history") or []
    elif job_payload.get("_talk_token"):
        short_term = []
    else:
        short_term = await get_history(user_id)
    if job_payload.get("voice_context") and not is_autonomous:
        short_term = _voice_history(short_term, query)

    # The conversation this turn belongs to: what the tool cache keys follow-ups by.
    tool_cache.CONVERSATION.set(_conversation_of(job_payload))

    # 2. Context Injection (RAG + live HA state + workspace memory)
    workspace_id = job_payload.get("workspace_id")
    include_curriculum = job_payload.get("include_curriculum", True)
    rag_budget: dict[str, int | bool] = {}
    is_voice = bool(job_payload.get("voice_context")) and not is_autonomous
    if is_voice:
        # A voice satellite cannot use workspaces, the CLI toolchain or Raven's protocol lessons.
        include_curriculum = False
        rag_budget = {"search_chars": VOICE_RAG_CHARS, "max_hits": VOICE_RAG_HITS, "devices_first": True}
    # The FastPath classifier's verdict, when the chat handler had one: it routes the pre-fetch.
    intent = job_payload.get("intent")
    try:
        intent_confidence = float(job_payload.get("intent_confidence") or 0.0)
    except (TypeError, ValueError):
        intent_confidence = 0.0
    if include_curriculum and not is_autonomous and not workspace_id \
            and not context_router.wants_curriculum(query, intent=intent, confidence=intent_confidence):
        # Raven's furniture (protocol lessons, the CLI toolchain, resource inventories) is
        # about 11k characters; a chat question that is not about code or a workspace has no
        # use for it. A Raven mission and a workspace turn always get it.
        include_curriculum = False
    sections: dict[str, int] = {}
    # A question that names a chapter of a book ("chapter 4 of <Title> by <Author>") gets
    # that chapter read from the library before the model runs, alongside the search: a
    # book the search index does not hold yet can still be read, and a model told only
    # "not in the index" answered from memory instead of calling the tool.
    chapter_ref = None if (grounded or is_autonomous) else library_names.chapter_named(query)
    chapter_task: asyncio.Task[ChapterPrefetch] | None = (
        asyncio.create_task(_prefetch_named_chapter(chapter_ref, creds)) if chapter_ref else None
    )
    if grounded:
        # The caller already supplied the complete, authoritative passages;
        # unrelated household/workspace hits are what made Bible study answers
        # cite the wrong material.
        rag_context = ""
    else:
        extra: dict[str, Any] = {"chapter_read": chapter_task} if chapter_task else {}
        rag_context = await _fetch_rag_context(
            query, user_id, creds, workspace_id=workspace_id, include_curriculum=include_curriculum,
            intent=intent, confidence=intent_confidence, sections=sections, **rag_budget, **extra
        )
    book_chapter = (await chapter_task).block if chapter_task else ""
    if book_chapter:
        sections["book_chapter"] = len(book_chapter)

    # A Raven mission may arrive with context already retrieved at dispatch
    # time, so that the mission brief can carry the sources it was launched
    # from. It is prepended rather than substituted: _fetch_rag_context also
    # contributes live Home Assistant state and workspace memory, and
    # replacing its output with the prefetched string would silently drop
    # both. The prefetched block was already trimmed to the retrieval budget
    # when it was built.
    prefetched = (job_payload.get("rag_context") or "").strip()
    if prefetched and not grounded:
        rag_context = f"{prefetched}\n\n{rag_context}".strip() if rag_context else prefetched
        sections["mission_brief_context"] = len(prefetched)

    # 4. Final Inference
    full_system = job_payload.get("system", "")
    # What this one request adds to the instructions (the voice satellite's note, a
    # storage directive): with the question for a single turn, appended for Raven.
    turn_note = str(job_payload.get("turn_note") or "")
    if is_autonomous:
        from services.gateway.agent_loop import AgentLoop
        # Raven handles autonomous loops
        mission_id = job_payload.get("_mission_id")
        workspace_id = job_payload.get("workspace_id")
        history_log = job_payload.get("history_log")
        raven_system = f"{full_system}\n\n{turn_note.strip()}" if turn_note.strip() else full_system
        prompt_budget.record(
            "raven", {"system": len(raven_system), "history": _history_chars(short_term),
                      "question": len(query), **sections},
            intent=intent, mission_id=job_payload.get("_mission_id"),
        )
        ans = await AgentLoop(query, model, raven_system, short_term, user_id, creds, mission_id, rag_context=rag_context, show_thinking=show_thinking, workspace_id=workspace_id, history_log=history_log)
    else:
        # Librarian handles standard single-turn inference
        # A model that cannot answer is raised, never returned as the answer: the
        # worker fails the job with a plain sentence and an error status (the old
        # "I encountered an error ... Cannot connect to host <ip>" went out as a
        # SUCCESS and showed family members the socket error). LocalAIOffline
        # (the local AI's breaker is open) is told apart so the reply can say the
        # thinking brain is offline rather than that something broke.
        from services.gateway.messaging import infer_priority_from_payload

        job_priority = infer_priority_from_payload(job_payload)
        chapter_kw: dict[str, Any] = {"book_chapter": book_chapter} if book_chapter else {}
        ans = await _single_turn_inference(query, model, full_system, rag_context, short_term, creds, chunk_callback, show_thinking=show_thinking, grounded=grounded, turn_note=turn_note, speech_stream=bool(job_payload.get("speech_stream")), context_sections=sections, budget_path="voice" if is_voice else "chat", priority=job_priority, persona=bool(job_payload.get("persona")), **chapter_kw)

    return ans

#: Heads the note that replaces other books' passages when the question names a
#: book or author the library does not hold (``library_names``).
LIBRARY_NOT_INDEXED_HEADER = "LIBRARY: THE BOOK LIBRARY WAS SEARCHED FOR THIS QUESTION"

#: How long the gateway waits for a named chapter before the model runs without it.
CHAPTER_PREFETCH_TIMEOUT_S = 20.0


class ChapterPrefetch:
    """The outcome of reading a named chapter: ``ok`` when the text was read, and the
    block for the question's context (the chapter, or a note saying why not)."""

    __slots__ = ("block", "ok")

    def __init__(self, ok: bool, block: str) -> None:
        self.ok = ok
        self.block = block


def _chapter_not_read(ref: "library_names.ChapterRef", why: str) -> ChapterPrefetch:
    book = ref.book_query or "the book"
    block = (
        f"[BOOK CHAPTER NOT READ: chapter {ref.chapter} of {book}]\n"
        f"The library was asked for this chapter and answered: {why.strip()}\n"
        "Say so plainly. Anything you add from general knowledge must be marked as not "
        "from the library, never presented as the chapter's text.\n"
    )
    return ChapterPrefetch(False, block)


async def _prefetch_named_chapter(ref: "library_names.ChapterRef", creds: ResolvedCredentials) -> ChapterPrefetch:
    """Read the chapter a question names through execution's CalibreRequest, as the asker.

    Time-boxed to ``CHAPTER_PREFETCH_TIMEOUT_S``; never raises. On success the block
    carries the chapter (at most ``CHAPTER_PREFETCH_MAX_CHARS``), on failure the
    library's own message, so the model can say what went wrong.
    """
    payload = {
        "action": "fetch_text",
        "query": ref.book_query,
        "chapter": str(ref.chapter),
        "user_context": creds.model_dump(),
    }
    try:
        settings = await get_all_settings()
        base = _get(settings, "execution_svc_url")
        endpoint = SINGLE_TURN_TOOL_ENDPOINTS.get("calibrerequest", "/execute/calibre")
        from services.gateway.main import shared_http_client

        async def call() -> tuple[int, Any]:
            async with shared_http_client() as client:
                resp = await client.post(
                    f"{base}{endpoint}", json=payload,
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=CHAPTER_PREFETCH_TIMEOUT_S),
                )
                if resp.status != 200:
                    return resp.status, await resp.text()
                return resp.status, await resp.json()

        status, body = await asyncio.wait_for(call(), timeout=CHAPTER_PREFETCH_TIMEOUT_S)
    except TimeoutError:
        log.info("[Chapter prefetch] %r chapter %s: no answer in %.0f s", ref.book_query, ref.chapter,
                 CHAPTER_PREFETCH_TIMEOUT_S)
        return _chapter_not_read(ref, f"no answer within {CHAPTER_PREFETCH_TIMEOUT_S:.0f} seconds. "
                                      "CalibreRequest fetch_text with query and chapter can try again.")
    except Exception as exc:
        log.warning("[Chapter prefetch] %r chapter %s failed: %s", ref.book_query, ref.chapter, exc)
        return _chapter_not_read(ref, f"the library could not be reached ({exc}).")
    if status != 200 or not isinstance(body, dict):
        log.warning("[Chapter prefetch] %r chapter %s: HTTP %s", ref.book_query, ref.chapter, status)
        return _chapter_not_read(ref, f"the library service answered HTTP {status}.")
    message = str(body.get("message") or "")
    if body.get("status") != "SUCCESS":
        log.info("[Chapter prefetch] %r chapter %s not read: %s", ref.book_query, ref.chapter, message[:200])
        return _chapter_not_read(ref, message or "no reason given.")

    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    chapter = detail.get("chapter") if isinstance(detail.get("chapter"), dict) else {}
    # The tool's message is "<Title> by <Author>, chapter N: <name>:" then the text.
    _head, sep, text = message.partition("\n\n")
    if not sep:
        text = message
    title = str(detail.get("title") or ref.title or "the book")
    author = str(detail.get("author") or ref.author or "").strip()
    number = chapter.get("number") or ref.chapter
    name = str(chapter.get("title") or "").strip()
    label = f"chapter {number}" + (f": {name}" if name and name.lower() != f"chapter {number}".lower() else "")
    by = f" by {author}" if author else ""
    total = len(text)
    bound = CHAPTER_PREFETCH_MAX_CHARS
    if total > bound:
        text = (
            text[:bound].rstrip()
            + f"\n\n[Truncated: the first {bound:,} of {total:,} characters of this chapter. "
            "Say the summary covers only this part, or read the rest with CalibreRequest fetch_text.]"
        )
    parts = int(chapter.get("parts") or 1)
    if parts > 1:
        text += (
            f"\n\n[This is part {int(chapter.get('part') or 1)} of {parts} of the chapter; "
            "CalibreRequest fetch_text with the same chapter and part=2 reads the next.]"
        )
    block = (
        f"[BOOK CHAPTER, READ FROM YOUR LIBRARY: {title}{by}, {label}]\n"
        "This is the chapter the question asks for, read from the library just now. Answer from "
        "it; do not fetch it again.\n\n"
        f"{text.strip()}\n"
    )
    log.info("[Chapter prefetch] read %r chapter %s: %d chars", title, number, total)
    return ChapterPrefetch(True, block)


def _above_floor(hits: list, floor: float | None, collection: str) -> list:
    """Keep only hits whose cosine ``similarity`` clears ``floor``.

    ``/rag/search`` applies the floor itself; this is the same rule on the
    gateway's side, so no pre-fetch path can put a passage under the floor into a
    prompt (an older RAG that ignores ``min_similarity``, a caller that forgot to
    send it). The floor is compared with ``similarity``, never with ``score``:
    ``score`` is a reciprocal-rank fusion (about 1/61 for a top hit) and says
    nothing about relevance. A hit RAG kept as a whole-query keyword match
    (``phrase_match``) stays, as it does there. A hit with no ``similarity`` at
    all cannot be checked here; it is kept on RAG's word and logged.
    """
    if floor is None:
        return list(hits)
    kept: list = []
    unchecked = 0
    for hit in hits:
        sim = hit.get("similarity") if isinstance(hit, dict) else None
        if sim is None:
            unchecked += 1
            kept.append(hit)
        elif hit.get("phrase_match") or float(sim) >= floor:
            kept.append(hit)
    if unchecked:
        log.warning("[RAG prefetch] %d %s hit(s) carried no similarity; the %.2f floor was not re-checked",
                    unchecked, collection, floor)
    dropped = len(hits) - len(kept)
    if dropped:
        log.info("[RAG prefetch] dropped %d %s hit(s) under the %.2f similarity floor", dropped, collection, floor)
    return kept


async def _passages_from_named_works(
    client, rag_svc: str, user_id: str, named: list[str], hits: list, k: int, floor: float | None,
) -> list:
    """Only passages from the book or author the question names.

    When none of the question's hits is from it, each name is looked up on its own
    (a title or an author is a phrase the book's rows carry), so a named book that
    is indexed but was outranked still answers. An empty result means the library
    does not hold it, and the caller says so instead of quoting other books.
    """
    own = [h for h in hits if library_names.hit_is_from(_as_meta_dict(h.get("metadata")), named)]
    if own:
        return own
    found: list = []
    seen: set[str] = set()
    for name in named:
        body: dict[str, Any] = {"collection_name": "calibre_files", "query": name, "user_id": user_id, "k": k}
        if floor is not None:
            body["min_similarity"] = floor
        try:
            resp = await client.post(
                f"{rag_svc}/rag/search", json=body,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status != 200:
                continue
            results = (await resp.json()).get("results", [])
        except Exception as exc:  # RAG is optional context
            log.warning("[RAG prefetch] library lookup for %r failed: %s", name, exc)
            continue
        for hit in _above_floor(results, floor, "calibre_files"):
            meta = _as_meta_dict(hit.get("metadata"))
            key = str(hit.get("content"))
            if meta.get("is_metadata") or key in seen or not library_names.hit_is_from(meta, named):
                continue
            seen.add(key)
            found.append(hit)
    return found[:k]


async def _fetch_rag_context(
    query: str,
    user_id: str,
    creds: ResolvedCredentials | None = None,
    workspace_id: str | None = None,
    *,
    include_curriculum: bool = True,
    include_library: bool = False,
    search_chars: int = RAG_PREFETCH_MAX_CHARS,
    max_hits: int = 12,
    devices_first: bool = False,
    intent: str | None = None,
    confidence: float = 0.0,
    k: int = RAG_PREFETCH_K,
    min_similarity: float | None = RAG_PREFETCH_MIN_SIMILARITY,
    hit_chars: int = RAG_HIT_MAX_CHARS,
    sections: dict[str, int] | None = None,
    chapter_read: Awaitable[ChapterPrefetch] | None = None,
) -> str:
    """Assemble the retrieved context for one turn.

    ``include_curriculum=False`` drops the workspace protocol lessons, the CLI
    toolchain inventory, the Nextcloud/Home Assistant inventories and the DREAM
    validation checklist, and the answer must then be grounded in the retrieved
    passages alone.

    It exists because ``TOTAL_CHARS_LIMIT`` bounds only the search hits: the
    always-on curriculum is appended after that budget is spent, so a turn could
    assemble 26k characters and still die on an 8k-token model with ``exceeds the
    available context size``. That failure surfaces as an error *answer* rather
    than as an error, which is the worst place for it to appear.

    ``include_library`` forces the book collection in for a caller that has
    already established books are wanted; see ``_collections_for_query``.

    The search part pre-fetches only from the collections the question's intent
    routes to (``intent``/``confidence`` from the FastPath classifier, else its
    wording), ``k`` hits each above ``min_similarity``, de-duplicated, inside
    ``search_chars``. Nothing below the floor is included "just in case": the
    knowledge map tells the model where to fetch anything else.

    ``chapter_read`` (the named chapter being read, ``_prefetch_named_chapter``) is
    awaited before saying a named book is not in the index: a book whose chapter was
    read gets no such note.

    ``sections`` (when given) receives the characters each part contributed, keyed
    ``prefetch:<collection>``, ``curriculum:<block>``, ``weather`` and so on, for the
    prompt-size record (``prompt_budget``).
    """
    if sections is None:
        sections = {}
    rag_context = ""
    settings = await get_all_settings()
    rag_svc = _get(settings, "rag_svc_url")
    try:
        # Prioritize collections based on query intent
        q = query.lower()
        collections = _collections_for_query(
            query, include_library=include_library, devices_first=devices_first,
            intent=intent, confidence=confidence,
        )
        log.info(f"[RAG prefetch] intent={intent} ({confidence:.2f}) collections={collections or 'none'}")

        # Context constraints
        MAX_TOTAL_HITS = max_hits
        MAX_HITS_PER_COLL = max(1, min(k, max_hits))
        MAX_CHARS_PER_HIT = hit_chars
        TOTAL_CHARS_LIMIT = search_chars
        seen_hits: set[str] = set()

        total_hits = 0
        total_chars = 0
        # The library curriculum below is Raven's furniture, and a read-only
        # research turn has no use for it: it cannot create a workspace, run a
        # shell command, or dispatch a mission. Feeding it the protocol lessons
        # and the CLI toolchain inventory costs ~11k characters that are then
        # charged against nothing, because the limit above only ever covered the
        # search hits. Skipping the block is what leaves room for the passages
        # the question was actually about.
        skip_curriculum = not include_curriculum

        from services.gateway.main import shared_http_client
        # RAG is a soft dependency: when it is down, a plain answer without
        # retrieved context is better than making every turn wait out the
        # timeout. Three consecutive failures open the breaker and the rest of
        # the collections are skipped until the reset window passes.
        breaker = get_breaker("rag", fail_max=3, reset_timeout=30.0)
        async with shared_http_client() as client:
            for coll in collections:
                if total_hits >= MAX_TOTAL_HITS or total_chars >= TOTAL_CHARS_LIMIT:
                    break
                coll_start = len(rag_context)
                search_body: dict[str, Any] = {
                    "collection_name": coll,
                    "query": query,
                    "user_id": user_id,
                    "k": MAX_HITS_PER_COLL,
                }
                if min_similarity is not None:
                    search_body["min_similarity"] = min_similarity

                try:
                    resp = await breaker.call(
                        lambda coll=coll, search_body=search_body: client.post(
                            f"{rag_svc}/rag/search",
                            json=search_body,
                            headers={"X-Internal-Secret": INTERNAL_SECRET},
                            timeout=aiohttp.ClientTimeout(total=10.0),
                        ),
                        is_failure=lambda response: response.status >= 500,
                    )
                except CircuitOpenError:
                    log.warning(
                        "RAG circuit is open; continuing without retrieved context"
                    )
                    break
                except Exception as exc:  # RAG is optional context
                    log.warning("RAG search failed; continuing without context: %s", exc)
                    break
                if resp.status == 200:
                    hits = _above_floor((await resp.json()).get("results", []), min_similarity, coll)
                    if coll == "calibre_files" and (named := library_names.named_works(query)):
                        hits = await _passages_from_named_works(
                            client, rag_svc, user_id, named, hits, MAX_HITS_PER_COLL, min_similarity,
                        )
                        if not hits and chapter_read is not None and (await chapter_read).ok:
                            log.info("[RAG prefetch] %s not in the index; its chapter was read directly", named)
                        elif not hits:
                            note = library_names.not_indexed_note(named)
                            rag_context += f"\n[{LIBRARY_NOT_INDEXED_HEADER}]\n{note}"
                            total_chars += len(note)
                            log.info("[RAG prefetch] named work(s) %s not in the indexed library", named)
                    if hits:
                        # For home devices, enrich with live state from the hub
                        if coll == HOME_DEVICES and creds:
                            hits = await _enrich_entities_with_live_state(hits, creds)

                        coll_added = False
                        for h in hits:
                            content = h["content"]
                            _key = context_router.normalized(str(content))
                            if _key in seen_hits:
                                continue  # the same row from another collection
                            seen_hits.add(_key)
                            if coll == "system_learnings" and names_withheld_hub_tool(str(content)):
                                # A lesson that sends the model to a hub tool while no hub is set up
                                # would only point it at a tool it is not offered.
                                continue
                            _meta = _as_meta_dict(h.get("metadata"))
                            if len(content) > MAX_CHARS_PER_HIT:
                                content = content[:MAX_CHARS_PER_HIT] + "... [TRUNCATED]"

                            if total_chars + len(content) > TOTAL_CHARS_LIMIT:
                                break

                            if not coll_added:
                                if coll == "system_learnings":
                                    # Surface Raven's own past lessons prominently, but
                                    # COMPACTLY: the transferable RULE + id + confidence,
                                    # NOT the full prose (which would bloat the 15k-char
                                    # budget). The model applies the rule, not the story.
                                    rag_context += (
                                        "\n[SYSTEM_LEARNINGS: PAST LESSONS. Each is a "
                                        "reusable rule. When you apply one, cite it once as "
                                        "`Apply: [id]` in your plan so it is recorded as used.]\n"
                                    )
                                else:
                                    rag_context += f"\n[{_collection_header(coll)}]\n"
                                coll_added = True

                            # Compact lesson rendering: prefer the structured RULE
                            # (the reusable takeaway) over verbose content. Falls
                            # back to content when no rule was captured. This only
                            # applies to lessons, whose content IS the JSON: doing
                            # it unconditionally threw away the _as_meta_dict above
                            # and left every prose hit with an empty citation, so a
                            # book passage reached the model with no title, author
                            # or chapter to attribute it to.
                            if coll == "system_learnings":
                                _meta = {}
                                try:
                                    _meta = json.loads(content) if isinstance(content, str) and content.strip().startswith("{") else (content if isinstance(content, dict) else {})
                                except Exception:
                                    _meta = {}
                                _lid = _meta.get("id") or ""
                                _rule = _meta.get("rule") or content
                                _conf = _meta.get("confidence", _meta.get("confidence", ""))
                                _conf_s = f" (conf {_conf})" if _conf != "" else ""
                                _out = _meta.get("outcome", "")
                                _out_s = f" [{_out}]" if _out else ""
                                rag_context += f"- [{_lid}]{_out_s}{_conf_s} {_rule}\n"
                                total_chars += len(content)
                            else:
                                rag_context += f"- {content}{_citation_suffix(_meta)}\n"
                                total_chars += len(content) + len(_citation_suffix(_meta))
                            total_hits += 1

                            if total_hits >= MAX_TOTAL_HITS:
                                break
                if len(rag_context) > coll_start:
                    sections[f"prefetch:{coll}"] = len(rag_context) - coll_start
    except Exception as e:
        log.error(f"RAG search failed: {e}")

    # ALWAYS-ON CURRICULUM + ENVIRONMENT AWARENESS
    # Terse prompts only work when Raven can fill in the gaps itself. These
    # pinned blocks teach the transferable protocol (dedicated workspace,
    # WebSearch for facts, answer.md, `Apply: [id]` citations), list the CLI
    # tools the workspace container actually has, and surface the resources it
    # can pull in (Nextcloud files, Home Assistant entities). All fetches are
    # best-effort with short timeouts; never block a mission on them.
    before_curriculum = len(rag_context)
    if not skip_curriculum:
        try:
            from services.gateway.main import shared_http_client
            async with shared_http_client() as client:
                try:
                    # Every seeded lesson, by id: a newest-first page of
                    # `tag=protocol` dropped the oldest, most basic ones.
                    _pr = await client.get(
                        f"{rag_svc}/rag/learning/protocol",
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    if _pr.status != 200:
                        log.error(
                            f"[Orchestrator] protocol curriculum unavailable: HTTP {_pr.status} "
                            f"{(await _pr.text())[:300]}"
                        )
                    else:
                        _proto = (await _pr.json()).get("items") or []
                        if _proto:
                            rag_context += (
                                "\n[PROTOCOL: ALWAYS-ON CURRICULUM. Follow these conventions on "
                                "EVERY task, even when the prompt does not mention them. When you "
                                "apply one, cite it once as `Apply: [id]` in your plan.]\n"
                            )
                            for _l in _proto:
                                _m = _l.get("metadata") or {}
                                _prule = _m.get("rule") or _l.get("content") or ""
                                if names_withheld_hub_tool(str(_prule)):
                                    continue  # a hub lesson while no hub is set up
                                _pid = _m.get("id") or _l.get("id") or ""
                                _pout = _m.get("outcome", "")
                                _pconf = _m.get("confidence", "")
                                _ps = f" [{_pout}]" if _pout else ""
                                _cs = f" (conf {_pconf})" if _pconf != "" else ""
                                rag_context += f"- [{_pid}]{_ps}{_cs} {_prule}\n"
                except Exception as e:
                    log.warning(f"[Orchestrator] protocol curriculum fetch skipped: {e}")

                try:
                    _tr = await client.get(
                        f"{rag_svc}/rag/toolchain",
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    if _tr.status == 200:
                        _tools = (await _tr.json()).get("tools") or []
                        if _tools:
                            rag_context += (
                                "\n[WORKSPACE TOOLCHAIN: CLI tools available in the workspace "
                                "shell; use them instead of assuming tools exist]\n"
                            )
                            for _t in _tools:
                                _tv = f" {_t.get('version')}" if _t.get("version") else ""
                                rag_context += f"- {_t.get('name')}{_tv}: {_t.get('description', '')}\n"
                except Exception as e:
                    log.warning(f"[Orchestrator] toolchain fetch skipped: {e}")

                try:
                    _nr = await client.get(
                        f"{rag_svc}/rag/resources/nextcloud",
                        params={"limit": 10},
                        headers={"X-Internal-Secret": INTERNAL_SECRET},
                        timeout=aiohttp.ClientTimeout(total=5.0),
                    )
                    if _nr.status == 200:
                        _files = (await _nr.json()).get("files") or []
                        if _files:
                            rag_context += (
                                "\n[NEXTCLOUD RESOURCES: files you can pull into the workspace "
                                "via StorageFileReadRequest then WorkspaceFileWriteRequest]\n"
                            )
                            for _f in _files:
                                rag_context += f"- {_f.get('path') or _f.get('name')}\n"
                except Exception as e:
                    log.warning(f"[Orchestrator] nextcloud resources fetch skipped: {e}")

                from services import config as _config

                # No home hub set up: no device snapshot, just as no hub tools are offered.
                if _config.any_home_hub():
                    try:
                        _hr = await client.get(
                            f"{rag_svc}/rag/resources/ha",
                            params={"limit": 10},
                            headers={"X-Internal-Secret": INTERNAL_SECRET},
                            timeout=aiohttp.ClientTimeout(total=5.0),
                        )
                        if _hr.status == 200:
                            _ents = (await _hr.json()).get("entities") or []
                            if _ents:
                                rag_context += (
                                    "\n[HOME ASSISTANT SNAPSHOT: entities you can read state from "
                                    "or control]\n"
                                )
                                for _e in _ents:
                                    _en = _e.get("friendly_name") or _e.get("entity_id") or ""
                                    rag_context += f"- {_e.get('entity_id')}: {_e.get('state')} ({_en})\n"
                    except Exception as e:
                        log.warning(f"[Orchestrator] HA resources fetch skipped: {e}")
        except Exception as e:
            log.warning(f"[Orchestrator] awareness blocks skipped: {e}")
    if len(rag_context) > before_curriculum:
        sections["curriculum"] = len(rag_context) - before_curriculum

    # BRIDGE (read path): surface THIS workspace's own raven_memory.md journal so
    # repeatable tasks in the same workspace start from accumulated experience,
    # not just the global system_learnings store. system_learnings covers
    # cross-workspace reuse; this covers same-workspace reinforcement. Reads are
    # best-effort and never block the mission if the file is missing/unreadable.
    before_memory = len(rag_context)
    if workspace_id:
        try:
            from services.gateway.main import shared_http_client
            async with shared_http_client() as client:
                _user_context = {"user": user_id, "is_admin": bool(creds and creds.is_admin)}
                if creds:
                    for _field in (
                        "ha_url", "ha_token", "nextcloud_url", "nextcloud_user", "nextcloud_pass",
                        "github_token", "gitlab_token", "git_token", "api_key",
                        "audiobookshelf_url", "audiobookshelf_user", "audiobookshelf_pass",
                    ):
                        _val = getattr(creds, _field, None)
                        if _val:
                            _user_context[_field] = _val
                _wr = await client.post(
                    f"{EXECUTION_SVC}/execute/workspace_file_read",
                    json={"workspace_id": workspace_id, "path": "raven_memory.md", "user_context": _user_context},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=10.0),
                )
                if _wr.status == 200:
                    _data = await _wr.json()
                    _mem = (_data.get("result") or {}).get("content") or _data.get("content") or ""
                    if _mem and _mem.strip():
                        rag_context += (
                            "\n[WORKSPACE_MEMORY: this workspace's prior lessons. Read and "
                            "APPLY these to avoid repeating past work]\n"
                            + _mem.strip()[:8000]
                            + "\n"
                        )
        except Exception as _me:
            log.warning(f"[Orchestrator] raven_memory.md read skipped: {_me}")
    if len(rag_context) > before_memory:
        sections["workspace_memory"] = len(rag_context) - before_memory

    # Proactively inject weather context for weather-related queries
    weather_keywords = ["weather", "forecast", "rain", "snow", "temperature", "outside", "humid", "wind", "storm", "sunny", "cloudy", "cold", "hot", "warm"]
    if any(kw in q for kw in weather_keywords):
        weather_ctx = await _fetch_weather_context(creds)
        if weather_ctx:
            rag_context += f"\n[WEATHER]\n{weather_ctx}\n"
            sections["weather"] = len(weather_ctx) + 12

    # DREAM FEEDBACK: RSI self-test loop
    # After dreaming consolidates lessons, this injects two things:
    # 1. VALIDATION CHECKLIST: lessons that have been applied (used) but
    #    never tested; Raven should run them as verification tasks
    #    before completing the current mission. Results go to
    #    POST /rag/dream/validate so future dreaming can weight them.
    # 2. PRIORITY HITS: top-scoring lessons (by applied_count +
    #    confidence + recency) for quick reference during the mission.
    # This creates a verify→learn→re-verify RSI cycle. It is curriculum, so it
    # answers to skip_curriculum like the protocol lessons do: a research turn
    # has no mission to validate on, and returning this as its only context
    # while grounding removed the search tools is how a question about a
    # library became a confident answer about Macbeth.
    before_dream = len(rag_context)
    if not skip_curriculum and (workspace_id or workspace_id == ""):
        try:
            from services.gateway.main import shared_http_client as _gw_client
            async with _gw_client() as _dc:
                _dp = await _dc.get(
                    f"{RAG_SVC}/rag/dream/pending",
                    params={"user_id": user_id},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                    timeout=aiohttp.ClientTimeout(total=5.0),
                )
                if _dp.status == 200:
                    _pending = (await _dp.json()).get("validations_pending") or []
                    if _pending:
                        rag_context += (
                            "\n[DREAM: VALIDATE ON NEXT MISSION. "
                            "Before completing, self-test each of these "
                            "consolidated lessons by running its rule as "
                            "a command/test in the workspace. Report "
                            "results via POST /rag/dream/validate]\n"
                        )
                        for _v in _pending[:5]:
                            rag_context += (
                                f"- Validate {_v['id']}: {_v['rule']} "
                                f"(applied {_v['applied_count']}x)"
                                + (f" | last: {_v['last_validated']}" if _v.get("last_validated") else "")
                                + "\n"
                            )
        except Exception as _de:
            log.debug(f"[Orchestrator] dream feedback skipped: {_de}")
    if len(rag_context) > before_dream:
        sections["dream"] = len(rag_context) - before_dream

    return rag_context.strip()


async def _fetch_weather_entities(creds: ResolvedCredentials) -> list[dict]:
    """Discover HA's weather entities, with live state and attributes."""
    settings = await get_all_settings()
    exec_svc = _get(settings, "execution_svc_url")

    # The caller's own HA login, else none and execution reads with the hub's own (hub_access.py).
    # Weather is household information: the weather entities are not trimmed per person.
    from services.gateway.hub_access import discovery_params

    try:
        # Fetch all entities to find weather domain
        from services.gateway.main import shared_http_client
        async with shared_http_client() as client:
            resp = await client.get(
                f"{exec_svc}/discovery/entities",
                params=discovery_params(creds),
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status != 200:
                return []
            data = await resp.json()
            entities = data.get("entities", []) if isinstance(data, dict) else []

        # Find weather domain entities
        weather_entities = [e for e in entities if e.get("entity_id", "").startswith("weather.")]
        if weather_entities:
            # Cache all states for future lookups
            from services.gateway.ha_state_cache import cache_all_states
            cache_all_states(entities)
        return weather_entities
    except Exception as e:
        log.error(f"Failed to fetch weather entities: {e}")
        return []


async def _open_meteo_context() -> dict:
    """The household forecast from execution's Open-Meteo cache (docs/WEATHER.md).

    ``{"text", "summary"}``; both empty when execution has no forecast, so the
    callers fall back to Home Assistant's weather entities.
    """
    settings = await get_all_settings()
    exec_svc = _get(settings, "execution_svc_url")
    try:
        from services.gateway.main import shared_http_client
        async with shared_http_client() as client:
            resp = await client.get(
                f"{exec_svc}/weather/context",
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=10.0),
            )
            if resp.status != 200:
                return {"text": "", "summary": ""}
            data = await resp.json()
            return data if isinstance(data, dict) else {"text": "", "summary": ""}
    except Exception as e:
        log.warning(f"Open-Meteo weather context unavailable: {type(e).__name__}: {e}")
        return {"text": "", "summary": ""}


async def _fetch_weather_context(creds: ResolvedCredentials) -> str:
    """The Open-Meteo forecast, else live forecast data from HA's weather entities."""
    text = (await _open_meteo_context()).get("text") or ""
    if text:
        return text
    weather_entities = await _fetch_weather_entities(creds)
    if not weather_entities:
        return ""

    parts = []
    for e in weather_entities:
        eid = e.get("entity_id", "")
        friendly = e.get("attributes", {}).get("friendly_name", eid)
        state = e.get("state", "unknown")
        attrs = e.get("attributes", {})

        details = [f"{friendly} ({eid}): {state}"]
        for key in ["temperature", "humidity", "forecast", "wind_speed", "pressure", "dew_point", "uv_index", "precipitation"]:
            if key in attrs:
                details.append(f"  {key}: {attrs[key]}")

        # Include forecast if available
        forecast = attrs.get("forecast", [])
        if forecast:
            forecast_items = []
            for f in forecast[:3]:  # Next 3 forecast periods
                f_parts = []
                if "datetime" in f:
                    f_parts.append(f["datetime"][:10])
                if "condition" in f:
                    f_parts.append(f["condition"])
                if "temperature" in f:
                    f_parts.append(f"{f['temperature']}°")
                if "precipitation" in f:
                    f_parts.append(f"rain: {f['precipitation']}mm")
                if f_parts:
                    forecast_items.append(" ".join(f_parts))
            if forecast_items:
                details.append(f"  forecast: {' | '.join(forecast_items)}")

        parts.append("\n".join(details))

    return "\n".join(parts)


async def watch_weather_answer(creds: ResolvedCredentials) -> str:
    """A one-line weather answer for the watch, without the LLM.

    The assistant path took 35-73 s and could exceed the model's context; the
    watch's HTTP timeout is shorter than that. The Open-Meteo summary comes
    first; HA's weather entity is the fallback.
    """
    summary = (await _open_meteo_context()).get("summary") or ""
    if summary:
        return summary
    weather_entities = await _fetch_weather_entities(creds)
    if not weather_entities:
        return "I couldn't read the weather right now."
    e = weather_entities[0]
    attrs = e.get("attributes", {}) or {}
    state = str(e.get("state") or "unknown").replace("_", " ")
    temp = attrs.get("temperature")
    if temp is None:
        return f"{state.capitalize()}."
    unit = str(attrs.get("temperature_unit") or "°")
    if not unit.startswith("°"):
        unit = "°" + unit
    return f"{state.capitalize()}, {round(float(temp))}{unit}."


def is_watch_weather_query(query: str) -> bool:
    return re.sub(r"\s+", " ", query.strip().lower()) in {
        "in one short sentence, what is the weather right now?",
        "what is the weather right now?",
    }


async def watch_calendar_answer(creds: ResolvedCredentials) -> str:
    """Read today's events directly and answer with the next one.

    Asking the model to choose CalendarRequest(action=read) was unreliable: it
    picked ``list`` (calendar names, not events) and the prompt could overflow
    the small model's 8192-token context. The execution handler already knows
    how to read; this just formats its first event.
    """
    now = datetime.now().astimezone()
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    payload = {
        "user_context": creds.model_dump(),
        "action": "read",
        "range_start": now.isoformat(),
        "range_end": end_of_day.isoformat(),
    }
    try:
        from services.gateway.main import shared_http_client
        async with shared_http_client() as client:
            resp = await client.post(
                f"{EXECUTION_SVC}/execute/calendar",
                json=payload,
                headers={"X-Internal-Secret": INTERNAL_SECRET},
                timeout=aiohttp.ClientTimeout(total=20.0),
            )
            if resp.status != 200:
                log.warning(f"[WatchCalendar] execution returned {resp.status}")
                return "I couldn't read your calendar right now."
            data = await resp.json()
    except Exception as e:
        log.warning(f"[WatchCalendar] read failed: {e}")
        return "I couldn't read your calendar right now."

    events = data.get("events", []) if isinstance(data, dict) else []
    for event in sorted(events, key=lambda e: e.get("start_time") or ""):
        start_raw = event.get("start_time")
        if not start_raw:
            continue
        try:
            start = datetime.fromisoformat(str(start_raw))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.astimezone()
        start = start.astimezone()
        end_raw = event.get("end_time")
        end = None
        if end_raw:
            try:
                end = datetime.fromisoformat(str(end_raw))
                if end.tzinfo is None:
                    end = end.astimezone()
                end = end.astimezone()
            except ValueError:
                end = None
        if end and end <= now:
            continue
        summary = str(event.get("summary") or "An event")
        all_day = bool(event.get("all_day")) or (
            start.hour == 0 and start.minute == 0
            and end is not None and end.hour >= 23
        )
        if all_day:
            return f"{summary} all day."
        if start <= now and end:
            return f"{summary} until {end.strftime('%-I:%M %p')}."
        return f"{summary} at {start.strftime('%-I:%M %p')}."
    return "Nothing else on your calendar today."


def is_watch_calendar_query(query: str) -> bool:
    return re.sub(r"\s+", " ", query.strip().lower()) in {
        "read my calendar events for today and tell me the next one in one short sentence.",
        "in one short sentence, what is next on my calendar today?",
    }

async def _enrich_entities_with_live_state(hits: list, creds: ResolvedCredentials) -> list:
    """Fetch live HA state (Redis-cached) and merge with RAG entity metadata.

    entity_id is the stable join key between RAG metadata and live state.
    Even if friendly_name changes in HA, entity_id remains constant.
    """
    from services.common.hub_login import own_login
    from services.gateway.ha_state_cache import fetch_live_states, get_cached_state

    # The caller's own HA login (none while HA is off), else empty and execution reads with the
    # hub's own: only the states of entities already in the caller's hits are added.
    ha_url, ha_token = own_login(creds) or ("", "")

    # Collect entity_ids from RAG hits
    entity_ids = [h.get("entity_id", "") for h in hits if h.get("entity_id")]
    if not entity_ids:
        return hits

    # Try Redis cache first
    live_states: dict[str, str] = {}
    cache_misses = []
    for eid in entity_ids:
        cached = get_cached_state(eid)
        if cached is not None:
            live_states[eid] = cached
        else:
            cache_misses.append(eid)

    # On cache miss, fetch all live states and repopulate cache
    if cache_misses:
        settings = await get_all_settings()
        exec_svc = _get(settings, "execution_svc_url")
        entities = await fetch_live_states(exec_svc, ha_url, ha_token, INTERNAL_SECRET)
        for e in entities:
            eid = e.get("entity_id", "")
            state = e.get("state", "unknown")
            if eid:
                live_states[eid] = state

    # Merge live state into RAG hits
    ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}

    for h in hits:
        eid = h.get("entity_id", "")
        live_state = live_states.get(eid)
        if live_state:
            is_active = live_state.lower() in ACTIVE_STATES
            h["is_active"] = is_active
            h["live_state"] = live_state
            # Rewrite content with live state
            base = h["content"]
            if "Current State:" in base:
                state_label = f"[ACTIVE] {live_state}" if is_active else live_state
                base = base.replace("Current State:", f"Current State (live): {state_label} |")
            else:
                base += f" | Current State (live): {live_state}"
            h["content"] = base

    # Sort: active devices first
    hits = sorted(hits, key=lambda h: not h.get("is_active", False), reverse=True)
    return hits

_SEARCH_RESULT_MAX_LINES = 30
_DETAIL_MAX_CHARS = 6000


def _compact_detail(detail: Any) -> str:
    """A tool's ``detail`` as JSON, cut to a size a small model can still read."""
    text = json.dumps(detail, indent=1, default=str, ensure_ascii=False)
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[:_DETAIL_MAX_CHARS] + f"\n... ({len(text) - _DETAIL_MAX_CHARS} more characters not shown)"
    return text


def _format_workspace_search(result: dict) -> str:
    """Render a workspace search with its hits, not just how many there were.

    Only the ``message`` used to reach the model, so it read "Found 3 matches for
    'mkv'" with no paths and searched again, three times, never learning a file
    name.
    """
    detail = result.get("detail") or {}
    files = detail.get("files") or []
    matches = detail.get("matches") or []
    lines = [result.get("message", "")]
    if files:
        lines.append("File names that match:")
        lines.extend(f"- {f}" for f in files[:_SEARCH_RESULT_MAX_LINES])
    if matches:
        lines.append("Lines that match (path:line: text):")
        for m in matches[:_SEARCH_RESULT_MAX_LINES]:
            text = str(m.get("text", ""))[:200]
            lines.append(f"- {m.get('path')}:{m.get('line')}: {text}")
        if len(matches) > _SEARCH_RESULT_MAX_LINES:
            lines.append(f"- ... {len(matches) - _SEARCH_RESULT_MAX_LINES} more")
    return "\n".join(lines)


def _format_context_search(result: dict, payload: dict) -> str:
    """A knowledge search's hits, each cut to a readable size and attributed."""
    results = result.get("results") or []
    coll = payload.get("collection_name") or ""
    if not results:
        return (
            f"Nothing in {coll} matched closely enough. Try other words, or another source "
            "from the knowledge map."
        )
    lines = []
    for r in results:
        text = context_router.clip(" ".join(str(r.get("content", "")).split()), RAG_HIT_MAX_CHARS)
        lines.append(f"- {text}{_citation_suffix(_as_meta_dict(r.get('metadata')))}")
    return f"Found {len(results)} relevant results in {coll}:\n" + "\n".join(lines)


#: Garden tools run in-process as the turn's user (garden_assist), normalized name -> tool.
_GARDEN_ACTIONS = {re.sub(r"[\s_]+", "", name).lower(): name for name in SINGLE_TURN_GARDEN_TOOLS}


async def _run_garden_tool(tool: str, tool_data: dict, creds: ResolvedCredentials) -> str:
    """garden_summary / garden_log_care / garden_log_harvest, through the same helpers
    the garden assistant routes use. A garden that cannot be read says so."""
    from services.gateway import garden_assist

    payload = tool_data.get("payload", tool_data)
    payload = payload if isinstance(payload, dict) else {}
    if tool == "garden_summary":
        answer = await garden_assist.summarize(creds)
    elif tool == "garden_log_care":
        target = str(payload.get("target") or "").strip()
        if not target:
            return "Name a plant, a species or a room to log care for."
        answer = await garden_assist.log_care(
            creds, target,
            action=str(payload.get("action") or "water"),
            still_damp=bool(payload.get("still_damp")),
            source="chat",
            postpone_days=payload.get("postpone_days"),
        )
    else:
        target = str(payload.get("target") or "").strip()
        try:
            quantity = float(payload.get("quantity"))
        except (TypeError, ValueError):
            return "A harvest needs a quantity."
        if not target:
            return "Name the plant that was picked."
        answer = await garden_assist.log_harvest(
            creds, target, quantity=quantity,
            unit=str(payload.get("unit") or "lb"),
            disposition=str(payload.get("disposition") or "fresh"),
            notes=payload.get("notes"),
        )
    message = str(answer.get("message") or "").strip()
    detail = {k: v for k, v in answer.items() if k not in ("message",)}
    return f"{message}\n\nDetail:\n{_compact_detail(detail)}" if detail else message


def _registry_names() -> dict[str, str]:
    """Normalized tool name -> the registry's spelling (the key freshness is declared under)."""
    from services.gateway import knowledge_map

    names = {**knowledge_map.single_turn_tools(), **knowledge_map.raven_tools()}
    return {re.sub(r"[\s_]+", "", n).lower(): n for n in names}


_REGISTRY_NAMES = _registry_names()


async def _execute_single_tool(action: str, tool_data: dict, query: str, creds: ResolvedCredentials, workspace_id: str | None = None) -> str:
    """Run one tool call, reusing a result this conversation already fetched while its
    declared freshness allows (``tool_cache``; live tools always go to the source)."""
    norm = re.sub(r"[\s_]+", "", action or "").lower()
    tool = _REGISTRY_NAMES.get(norm, action)
    payload = tool_data.get("payload", tool_data)
    return await tool_cache.call(
        tool,
        dict(payload) if isinstance(payload, dict) else {},
        user=str(getattr(creds, "user", "") or ""),
        fetch=lambda: _run_single_tool(action, tool_data, query, creds, workspace_id=workspace_id),
    )


async def _run_single_tool(action: str, tool_data: dict, query: str, creds: ResolvedCredentials, workspace_id: str | None = None) -> str:
    """Execute a single tool call and return the result string.

    ``workspace_id``, when the turn belongs to a workspace, overrides whatever
    workspace the model named or the query implied for workspace file tools.
    """
    settings = await get_all_settings()
    control_plane = _get(settings, "control_plane_url")

    # Normalize action: strip underscores/spaces, lowercase → canonical form
    action = re.sub(r'[\s_]+', '', action).lower()

    if action == "controlplanerequest":
        payload = tool_data.get("payload", tool_data)
        service_name = payload.get("service_name")
        sub_action = payload.get("action", "restart")
        if not service_name:
            return "Error: service_name is required"
        try:
            from services.gateway.main import shared_http_client
            async with shared_http_client() as client:
                if sub_action == "restart":
                    resp = await client.post(f"{control_plane}/api/restart/{service_name}", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=30.0))
                else:
                    resp = await client.get(f"{control_plane}/api/status/{service_name}", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=30.0))

                if resp.status == 200:
                    body = await resp.text()
                    return f"Control Plane '{sub_action}' succeeded on {service_name}: {body}"
                body = await resp.text()
                return f"Control Plane error {resp.status}: {body}"
        except Exception as e:
            log.error(f"Control Plane execution error: {e}")
            return f"Control Plane execution failed: {e}"

    elif studio_tools.tool_for(action):
        # The Workspace Studio runs in this process, as the turn's user, through
        # the same handlers as /api/studio/* (services/gateway/studio/tools.py).
        payload = tool_data.get("payload", tool_data)
        try:
            result = await studio_tools.call(
                action, payload if isinstance(payload, dict) else {},
                user=creds.user, workspace_id=workspace_id,
            )
        except studio_actions.StudioActionError as exc:
            return f"Sorry, I couldn't complete that action: {exc.detail} (HTTP {exc.status})"
        return studio_tools.summarize(action, result)

    elif action in _GARDEN_ACTIONS:
        return await _run_garden_tool(_GARDEN_ACTIONS[action], tool_data, creds)

    elif action in SINGLE_TURN_TOOL_ENDPOINTS:
        endpoint = SINGLE_TURN_TOOL_ENDPOINTS[action]
        svc_key = _TOOL_SERVICE_MAP.get(action, "execution_svc_url")
        if svc_key == GATEWAY_IN_PROCESS:
            raise RuntimeError(f"{action} is a gateway tool and must run in-process, not over HTTP")
        svc_base = _get(settings, svc_key)

        try:
            payload = tool_data.get("payload", tool_data)
            payload["user_context"] = creds.model_dump()

            if action in ("lightcontrolrequest", "climaterequest", "haservicerequest"):
                act_val = str(payload.get("action", "")).lower().replace("_", "")
                if act_val in SINGLE_TURN_TOOL_ENDPOINTS or not payload.get("action"):
                    sub = payload.pop("sub_action", None) or payload.pop("device_action", None) or tool_data.get("sub_action") or tool_data.get("device_action")
                    if sub:
                        payload["action"] = sub

            if action == "storageindexrequest":
                payload = {
                    "provider": {
                        "kind": "nextcloud",
                        "settings": {
                            "url": creds.nextcloud_url,
                            "username": creds.nextcloud_user,
                            "password": creds.nextcloud_pass
                        }
                    },
                    "path": payload.get("path") or payload.get("relative_path") or payload.get("file_path") or "/",
                    "recursive": True,
                    "user_context": creds.model_dump()
                }

            if action == "contextsearchrequest":
                payload["user_id"] = creds.user or "default"
                # A search with no collection is not a search, it is a guess:
                # /rag/search defaults to nextcloud_files, so an under-specified
                # call quietly reports "no relevant context found" while the
                # material the caller wanted sits unread in another collection.
                # Observed live: the Librarian was handed Macduff passages in
                # its context, called this without a collection three times, and
                # answered "No relevant context found" about a book it was
                # already holding. So refuse, and say what to pass.
                if payload.get("collection_name"):
                    payload["collection_name"] = canonical_collection(str(payload["collection_name"]))
                # A few close hits, not a page of nearest ones: the model can ask again,
                # with other words or a larger k, if they do not answer the question.
                try:
                    _k = int(payload.get("k") or KNOWLEDGE_SEARCH_DEFAULT_K)
                except (TypeError, ValueError):
                    _k = KNOWLEDGE_SEARCH_DEFAULT_K
                payload["k"] = max(1, min(_k, KNOWLEDGE_SEARCH_MAX_K))
                payload.setdefault("min_similarity", RAG_SEARCH_MIN_SIMILARITY)
                if not str(payload.get("collection_name") or "").strip():
                    return (
                        "collection_name is required and was not supplied, so the "
                        "search was not run. /rag/search would default to "
                        "nextcloud_files and report no results regardless of what "
                        "was asked. Pass collection_name explicitly -- "
                        "calibre_files for the book library, nextcloud_files for "
                        "files, system_learnings for past lessons, home_devices for "
                        "the house, system_capabilities for tools, user_facts for "
                        "remembered user facts, conversation_memory for intercom "
                        "transcripts, mission_history for past Raven runs, "
                        "network_topology for Docker/LAN topology, or "
                        "telemetry_alerts for device alerts."
                    )

            if action == "talkrequest" and not payload.get("action"):
                if payload.get("message") or payload.get("text_to_voice"):
                    payload["action"] = "send"
                elif payload.get("token"):
                    payload["action"] = "messages"
                else:
                    payload["action"] = "list"

            if action == "announcementrequest" and not payload.get("entity_id") and not payload.get("device_name"):
                device_match = re.search(r"(?:on|to|via|at|using)\s+(?:the\s+)?([A-Z][A-Za-z\s]+?)(?:\s+(?:speaker|tv|device|display|chromecast|cast))", query)
                device_name = device_match.group(1).strip() if device_match else _trailing_place(query)
                if device_name:
                    if not any(t in device_name.lower() for t in ["tv", "speaker", "display", "cast"]):
                        type_match = re.search(r"(speaker|tv|display|chromecast|cast)", query, re.IGNORECASE)
                        if type_match:
                            device_name += " " + type_match.group(1)
                    payload["device_name"] = device_name
                    log.info(f"[_execute_single_tool] Auto-resolved device_name='{device_name}' from query")

            if action == "mediaplayrequest":
                if not payload.get("query"):
                    m = re.search(r"(?:play|put on|listen to)\s+(?:podcast\s+)?['\"`]([^'\"`]+)['\"`]", query, re.IGNORECASE)
                    if m:
                        payload["query"] = m.group(1).strip()
                if "podcast" in query.lower() and not payload.get("media_type"):
                    payload["media_type"] = "podcast"
                if not payload.get("entity_id") and not payload.get("device_name"):
                    device_match = re.search(r"(?:on|to|via|at|using)\s+(?:the\s+)?([A-Za-z\s]+?)\s+\b(speaker|tv|device|display|chromecast|cast)\b", query, re.IGNORECASE)
                    if device_match:
                        device_base = device_match.group(1).strip().title()
                        device_type = device_match.group(2).strip().title()
                        payload["device_name"] = f"{device_base} {device_type}"
                    elif place := _trailing_place(query):
                        payload["device_name"] = place.title()

            if action == "noterequest" and not payload.get("action"):
                if payload.get("content") or payload.get("title"):
                    payload["action"] = "create"
                else:
                    payload["action"] = "list"

            if action == "locationrequest":
                # Fill only what the model left out (it may have chosen better).
                parsed = parse_location_query(query, creds.user)
                if not payload.get("user") and not payload.get("person"):
                    payload["user"] = parsed["user"]
                if not payload.get("detail"):
                    payload["detail"] = parsed["detail"]
                if payload.get("detail") == "eta" and not payload.get("to"):
                    payload["to"] = parsed["to"] or "home"
                if payload.get("detail") == "closest" and not payload.get("to"):
                    payload["to"] = parsed["to"]

            _ws_file_actions = {
                "workspacefilereadrequest",
                "workspacefilewriterequest",
                "workspacefilepatchrequest",
                "workspacesearchrequest",
                "workspaceshellrequest",
                "workspacelintrequest",
            }
            if workspace_id and action != "workspacecreaterequest":
                # A workspace turn acts on that workspace, whatever the model
                # named: transcription, OCR, TTS output and git all land there.
                # (Creating a workspace reads workspace_id as the NEW id.)
                payload["workspace_id"] = workspace_id
            elif action in _ws_file_actions:
                if not payload.get("workspace_id"):
                    try:
                        from services.gateway.main import shared_http_client
                        async with shared_http_client() as ws_client:
                            ws_url = f"{_get(settings, 'workspace_runtime_svc_url')}/workspaces"
                            resp = await ws_client.get(
                                ws_url,
                                params={"rag_user": creds.user},
                                headers={"X-Internal-Secret": INTERNAL_SECRET},
                                timeout=aiohttp.ClientTimeout(total=5.0),
                            )
                            if resp.status == 200:
                                ws_list = (await resp.json()).get("workspaces", [])
                                for w in ws_list:
                                    wid = w.get("id", "")
                                    wdisp = (w.get("display_name") or "").lower()
                                    if (wid and wid in query.lower()) or (wdisp and wdisp in query.lower()):
                                        payload["workspace_id"] = wid
                                        break
                                if not payload.get("workspace_id") and ws_list:
                                    user_ws = next((w for w in ws_list if w.get("scope") == "user"), ws_list[0])
                                    payload["workspace_id"] = user_ws.get("id")
                    except Exception as ex:
                        log.debug(f"Failed to auto-resolve workspace_id: {ex}")
                elif payload.get("workspace_id"):
                    payload["workspace_id"] = re.sub(r"[^a-zA-Z0-9_\-]+", "-", str(payload["workspace_id"]).strip().lower()).strip("-")

            if action == "workspacecreaterequest":
                payload.pop("user_context", None)
                _wid = payload.get("id") or payload.get("workspace_id")
                _display = (
                    payload.get("display_name")
                    or payload.get("name")
                    or payload.get("displayName")
                    or str(_wid or "")
                )
                if not _wid:
                    _wid = _display
                if _wid:
                    _slug = re.sub(r"[^a-zA-Z0-9_\-]+", "-", str(_wid).strip().lower()).strip("-")
                    if _slug:
                        _wid = _slug
                if not _wid:
                    _wid = uuid.uuid4().hex[:16]
                payload = {
                    "id": str(_wid),
                    "display_name": str(_display) if _display else str(_wid),
                    "scope": payload.get("scope") or "user",
                    "owner_user": creds.user,
                    "description": payload.get("description") or "",
                    "is_default": False,
                }

            from services.gateway.main import shared_http_client
            async with shared_http_client() as client:
                resp = await client.post(f"{svc_base}{endpoint}", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=aiohttp.ClientTimeout(total=_SLOW_TOOL_TIMEOUTS.get(action, 60.0)))
                if resp.status == 200:
                    result = await resp.json()
                    if action == "workspacecreaterequest":
                        ws_id = result.get("id")
                        ws_name = result.get("display_name", ws_id)
                        return f"Created workspace environment '{ws_name}' (id: `{ws_id}`) successfully."
                    if action == "workspacefilereadrequest":
                        detail = result.get("detail") or {}
                        content = detail.get("content", "")
                        return f"File content of '{payload.get('path', '')}':\n\n{content}"
                    if action == "workspacesearchrequest" and result.get("status") != "FAILURE":
                        return _format_workspace_search(result)
                    if action == "noterequest":
                        detail = result.get("detail") or {}
                        if "notes" in detail:
                            notes_list = detail["notes"]
                            lines = [f"- {n.get('title', 'Untitled')}: {n.get('content', '')[:100]}" for n in notes_list]
                            return "Notes:\n" + "\n".join(lines) if lines else "No notes found."
                        if "content" in detail:
                            return f"Note '{detail.get('title', '')}':\n{detail['content']}"
                    if action == "ravenmissionrequest":
                        mid = result.get("id")
                        mtitle = result.get("title", f"Mission #{mid}")
                        return f"Raven mission #{mid} ('{mtitle}') queued successfully."
                    if action == "executionlogrequest":
                        detail = result.get("detail") or {}
                        logs = detail.get("logs", "")
                        if logs:
                            return f"{result.get('message', '')}\n\n```\n{logs}\n```"
                    if action == "contextsearchrequest":
                        return _format_context_search(result, payload)
                    if action == "entitysearchrequest":
                        detail = result.get("detail", {})
                        entities = detail.get("entities") or result.get("entities") or []
                        if not entities:
                            return result.get("message", "No matching entities found.")
                        lines = [f"- {e.get('name', e.get('entity_id', ''))} ({e.get('entity_id', '')}): {e.get('state', '')}" for e in entities[:10]]
                        return f"Found {len(entities)} matching entities:\n" + "\n".join(lines)
                    if action == "haconfigrequest":
                        detail = result.get("detail", {})
                        if detail:
                            return f"{result.get('message', '')}\n\nDetail:\n{json.dumps(detail, indent=2)}"
                        return result.get("message", "Action completed successfully.")
                    msg = result.get("message", "Action completed successfully.")
                    status = result.get("status", "SUCCESS")
                    if status == "FAILURE":
                        return f"Sorry, I couldn't complete that action: {msg}"
                    if workspace_id and result.get("detail"):
                        # In a workspace the result IS the work (a transcript, the
                        # OCR text, the voices available); a bare "Transcribed x"
                        # leaves the model nothing to answer with.
                        return f"{msg}\n\nDetail:\n{_compact_detail(result['detail'])}"
                    return msg
                else:
                    err_body = await resp.text()
                    log.warning(f"[_execute_single_tool] HTTP {resp.status} from {endpoint}: {err_body[:300]}")
                    try:
                        err_json = json.loads(err_body)
                        detail = err_json.get("detail", {})
                        err_msg = (detail.get("message") or detail.get("error") or err_body) if isinstance(detail, dict) else (str(detail) or err_body)
                        return f"Sorry, I couldn't complete that action: {err_msg}"
                    except Exception:
                        return f"Sorry, I couldn't complete that action. The service returned an error (HTTP {resp.status})."
        except Exception as e:
            log.error(f"Single-turn tool execution error: {e}")
            return f"Sorry, I encountered an error while trying to perform that action: {e}"
    else:
        log.warning(f"[_execute_single_tool] Unsupported tool for single-turn: {action}")
        return f"I don't have a handler for the '{action}' action yet. Could you try rephrasing, or let me know what you'd like me to do?"

class InferenceUnavailable(RuntimeError):
    """The model server could not take the turn.

    Raised rather than returned as a sentence, because a caller that ships the
    sentence in an ``answer`` field reports a failure as a success: the
    workspace composer showed "I encountered an error while trying to generate a
    response" with HTTP 200 and a success toast, so the only visible symptom was
    that the chat never worked. The queued chat path raises it too now: the
    worker fails the job with a plain sentence and an error id (the detail stays
    in the log), and the voice assistant speaks that sentence.
    """


class LocalAIOffline(InferenceUnavailable):
    """The local AI is known to be offline (``local_ai.LLM``'s breaker is open).

    ``str()`` is the plain sentence for a person, never the socket error;
    ``error_id`` names the one log line that has the detail.
    """

    def __init__(self, cause: BackendUnavailable):
        self.cause = cause
        self.error_id = cause.error_id
        self.backend = cause.backend
        self.message = cause.message
        super().__init__(cause.message)
        self.__cause__ = cause


def _is_capacity_refusal(error: Exception) -> bool:
    """True when the model server said it has no room, not that it failed.

    A queue timeout means the server held the request for its whole queue
    window. Retrying that three times multiplies both the load on a server that
    is already full and the caller's wait: the observed turn took over seven
    minutes and returned nothing but the timeout it already knew about.
    """
    text = str(error).lower()
    return (
        "queue_timeout" in text
        or "no slots available" in text
        or "no llama-server slots" in text
    )


_SECRET_INPUT_KEYS = ("pass", "token", "secret", "api_key", "apikey", "credential")


def _public_tool_input(tool_data: dict) -> dict:
    """A tool call's arguments as safe to show in a transcript.

    The payload carries the caller's resolved credentials (user_context) and
    may carry a password a tool was given; neither belongs in a chat log.
    """
    payload = tool_data.get("payload", tool_data)
    if not isinstance(payload, dict):
        return {}
    return {
        k: v for k, v in payload.items()
        if k not in ("user_context", "action", "tool") and not any(s in k.lower() for s in _SECRET_INPUT_KEYS)
    }


def _tool_preamble(raw: str) -> str:
    """The prose a model wrote around a tool call, without the call itself."""
    from services.gateway.llm_providers import strip_thinking_blocks

    text = re.sub(r"```(?:json)?\s*\{.*?\}\s*```", "", raw or "", flags=re.DOTALL)
    text = re.sub(r"\{[^{}]*\}", "", text)
    return strip_thinking_blocks(text).strip()


_CLOSING_TURN_INSTRUCTION = (
    "You have used all of your tool calls for this request; no more tools will run. "
    "Answer the user now in plain sentences, using only what the tool results above "
    "showed. Say what you found and what you did. If the request is not finished, "
    "say so plainly, say what is still needed, and name the exact files or details "
    "involved. Do not output JSON or a tool call."
)


async def _closing_turn(
    messages: list[dict[str, str]],
    model: str,
    options: dict[str, Any],
    chunk_callback: Callable[[str], Awaitable[None]] | None,
    last_tool_result: str,
) -> str:
    """Ask the model for a final answer once the tool budget is spent."""
    from services.gateway.agent_loop import extract_action_json
    from services.gateway.llm_providers import strip_thinking_blocks

    closing = [*messages, {"role": "user", "content": _CLOSING_TURN_INSTRUCTION}]
    try:
        data = await call_ollama(
            {"model": model, "messages": closing, "options": options, "chunk_callback": chunk_callback},
            use_chat=True,
        )
        raw = data.get("message", {}).get("content", "")
        # "Let me search once more: {tool call}" is another step, not an answer.
        answer = "" if extract_action_json(raw) else strip_thinking_blocks(strip_json_from_response(raw)).strip()
    except BackendUnavailable as exc:
        raise LocalAIOffline(exc) from exc
    except Exception as exc:
        if _is_capacity_refusal(exc):
            raise InferenceUnavailable(str(exc)) from exc
        log.warning(f"[_single_turn_inference] closing turn failed: {exc}")
        answer = ""
    if answer:
        return answer
    # The model would not conclude. Say so, rather than presenting a tool's
    # status line as though it were the answer.
    return (
        "I ran out of steps before I could finish this. The last thing I did "
        f"reported: {last_tool_result.strip()[:500]}"
    )


def _speaker_line(creds: ResolvedCredentials | None) -> str:
    """Who is asking, for the model: a person, or plainly that nobody was recognised.

    A shared voice satellite or kiosk asks as the household. Told nothing, the model
    answered "Who am I?" with an invented name ("You're Alex").
    """
    user = (getattr(creds, "user", "") or "").strip()
    if not user or user == "household":
        return ("Speaker: not identified (a shared device such as a voice satellite or kiosk). If they ask "
                "who they are, say you could not tell from their voice and they can tell you their name. "
                "Never guess or invent a name.")
    how = " (recognised by their voice)" if getattr(creds, "identified_by", None) == "voice" else ""
    return f"Speaker: {user}{how}."


_PERSONA_WARNED = False


def _assistant_persona() -> str:
    """The editable ``assistant_persona`` prompt (Settings), or "" with one warning per process."""
    global _PERSONA_WARNED
    try:
        return load_prompt_sync(PROMPT_ASSISTANT_PERSONA).strip()
    except ValueError:
        if not _PERSONA_WARNED:
            _PERSONA_WARNED = True
            log.warning("[_single_turn_inference] setting %r is missing or empty; answering without the persona",
                        PROMPT_ASSISTANT_PERSONA)
        return ""


async def _single_turn_inference(query: str, model: str, system_prompt: str, rag_context: str, history: list[dict[str, str]], creds: ResolvedCredentials, chunk_callback: Callable[[str], Awaitable[None]] | None = None, show_thinking: bool = False, grounded: bool = False, workspace_id: str | None = None, workspace_context: str = "", event_callback: Callable[[dict], Awaitable[None]] | None = None, turn_note: str = "", speech_stream: bool = False, context_sections: dict[str, int] | None = None, budget_path: str = "chat", priority: int | None = None, persona: bool = False, book_chapter: str = "") -> str:
    """Answer ``query`` in at most MAX_TURNS tool calls plus one closing turn.

    ``book_chapter`` is a chapter the gateway read for this question
    (``_prefetch_named_chapter``), or the note that it could not: it travels with the
    question, after the retrieved context and outside its size cap.

    The system message carries the knowledge map (``knowledge_map``): where every
    kind of information lives and the tool that fetches it, since only a small,
    intent-routed pre-fetch travels with the question. ``context_sections`` is that
    pre-fetch's size by collection, for the prompt-size record (``prompt_budget``,
    under ``budget_path``).

    ``speech_stream`` (a voice satellite) sends ``chunk_callback`` only whole sentences that are safe
    to say before the answer is finished (``speech_stream.SpeechGate``), never raw model words.

    ``event_callback`` receives the turn as it happens, in the part shapes a
    session transcript stores (OpenCode's message parts): ``step``,
    ``thinking`` and ``text`` deltas, ``tool_call`` and ``tool_result``. With it
    every step streams; without it nothing about the old behaviour changes.

    ``workspace_id`` pins every workspace tool call to the workspace the user is
    asking from; without it the executor guesses from the wording of the query.
    ``workspace_context`` (that workspace's file listing) is shown to the model
    so it can name files instead of searching their contents for them.
    """
    if len(rag_context) > MAX_RAG_CONTEXT_CHARS:
        log.warning(
            "[_single_turn_inference] RAG context %s chars exceeds %s; truncating",
            len(rag_context),
            MAX_RAG_CONTEXT_CHARS,
        )
    rag_context = _truncate_rag_context(rag_context)
    now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p %Z")
    single_turn_guide = hub_tool_guide(load_prompt_sync(PROMPT_SINGLE_TURN_TOOL_GUIDE))
    if grounded:
        single_turn_guide = _grounded_tool_guide(single_turn_guide)
        log.info("[_single_turn_inference] grounded turn: retrieval tools omitted from the tool guide")
    # The system message is the same for every question (the persona and the tool
    # guide, ~3,500 tokens), and everything that changes (the time, who is speaking,
    # the voice satellite's note, the retrieved context) travels with the question.
    # The model server keeps a checkpoint of that fixed start warm (alpaca-proxy's
    # prompt keeper), so a new question reads only its own part: with the time and
    # the context inside the system message, every voice question re-read the whole
    # guide (about 27 s on the household's card).
    # Where everything else lives, and the tool that fetches it. Built from the registry
    # for this configuration and role, so it is the same text every time. A grounded
    # turn is answered from the passages it was given and is offered no retrieval.
    kmap = "" if grounded else knowledge_map_for(
        "single_turn", is_admin=bool(getattr(creds, "is_admin", False)), guide=single_turn_guide
    )
    system = f"{system_prompt.strip()}\n\nSystem Capability Context:\n{single_turn_guide}"
    if kmap:
        system += f"\n\n{kmap}"
    before_workspace = len(system)
    if workspace_context.strip():
        system += (
            f"\n\nWorkspace:\n{workspace_context.strip()}\n"
            "This turn allows only a few quick tool calls. A long job -- transcribing "
            "audio or video, converting media, a multi-step build -- cannot finish "
            "here: say so plainly, name the file it would use, and suggest sending "
            "it to Raven as a mission."
        )
    ws_chars = len(system) - before_workspace
    if persona:
        # Last on purpose: at the top of a ~20K-character system message the model
        # followed the tool guide and ignored the character ("I don't have a favorite
        # color"). Same text for every question, so the cached prefix still holds.
        persona_text = _assistant_persona()
        if persona_text:
            system += f"\n\n{persona_text}"
    context_parts = [f"Current Date/Time: {now}", _speaker_line(creds)]
    if turn_note.strip():
        context_parts.append(turn_note.strip())
    context_parts.append(f"Retrieved Context:\n{rag_context}")
    if book_chapter.strip():
        context_parts.append(book_chapter.strip())
    question = "[Context for this question]\n" + "\n\n".join(context_parts) + f"\n\n[Question]\n{query}"
    log.info(f"[_single_turn_inference] RAG context length: {len(rag_context)} chars")
    if rag_context:
        log.info(f"[_single_turn_inference] RAG context preview: {rag_context[:300]}")
    messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": question}]

    prefetch = dict(context_sections or {})
    if not prefetch and rag_context:
        prefetch = {"retrieved": len(rag_context)}
    budget = prompt_budget.record(
        budget_path,
        {
            # The four fixed parts add up to the system message exactly (separators in "system").
            "system": len(system) - len(single_turn_guide) - len(kmap) - ws_chars,
            "tool_guide": len(single_turn_guide),
            "knowledge_map": len(kmap),
            "workspace": ws_chars,
            "history": _history_chars(history),
            "question": len(question) - len(rag_context) - len(book_chapter.strip()),
            **prefetch,
        },
        model=model,
    )

    log.info(f"[_single_turn_inference] Executing for model {model}")

    from services.gateway.messaging import PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_NORMAL

    effective_priority = priority if priority is not None else (PRIORITY_HIGH if budget_path == "voice" else PRIORITY_NORMAL)
    if effective_priority >= PRIORITY_CRITICAL:
        req_source = "shared-llm/emergency"
    elif budget_path == "voice" or effective_priority >= PRIORITY_HIGH:
        req_source = "voice/assistant"
    else:
        req_source = "shared-llm/app"
    options = {
        "temperature": 0.0,
        "num_predict": 2048,
        "show_thinking": show_thinking,
        "priority": effective_priority,
        "request_source": req_source,
    }

    def _step_stream(step: int | str):
        """Forward a step's thinking and text deltas as events."""
        async def forward(chunk):
            if not isinstance(chunk, dict) or not chunk.get("text"):
                return
            if chunk.get("type") == "thinking" and not show_thinking:
                return
            if chunk.get("type") in ("thinking", "content"):
                await event_callback({"type": "thinking" if chunk["type"] == "thinking" else "text", "text": chunk["text"], "step": step})
        return forward

    MAX_INFERENCE_RETRIES = 3
    MAX_TURNS = 3

    from services.gateway.agent_loop import extract_action_json

    #: Every device tool run in this turn, with its result: what an answer may claim (device_claims).
    device_runs: list[tuple[str, str]] = []
    gate = None
    if speech_stream and chunk_callback and not event_callback:
        from services.gateway.speech_stream import SpeechGate

        gate = SpeechGate(chunk_callback, query)

    for turn in range(MAX_TURNS):
        log.info(f"[_single_turn_inference] Turn {turn + 1}/{MAX_TURNS}")
        ans = ""
        if event_callback:
            await event_callback({"type": "step", "n": turn + 1})

        # In single-turn mode, do not stream intermediate tool emission (Turn 1) live
        # because the user/voice-assistant should never hear raw JSON tool calls.
        # If show_thinking is True, thinking chunks are streamed to the UI.
        if turn == 0:
            if show_thinking and chunk_callback:
                async def turn_callback(chunk):
                    if (isinstance(chunk, dict) and chunk.get("type") == "thinking") or (isinstance(chunk, str) and chunk.startswith('{"type": "thinking"')):
                        await chunk_callback(chunk)
            else:
                turn_callback = None
        else:
            turn_callback = chunk_callback
        if event_callback:
            turn_callback = _step_stream(turn + 1)
        if gate is not None:
            turn_callback = gate.feed

        for retry_count in range(MAX_INFERENCE_RETRIES):
            if gate is not None:
                await gate.begin_turn()
            try:
                data = await call_ollama(
                    {
                        "model": model,
                        "messages": messages,
                        "options": options,
                        "chunk_callback": turn_callback,
                    },
                    use_chat=True,
                )
                ans = data.get("message", {}).get("content", "")
                if gate is not None:
                    await gate.end_call()
                break
            except BackendUnavailable as e:
                # The local AI cannot be reached (its breaker is open, or this call
                # just opened it). Retrying in 5 and 10 s against a machine that is
                # off is what made an offline answer take 37 s.
                raise LocalAIOffline(e) from e
            except Exception as e:
                log.warning(f"[_single_turn_inference] Inference attempt {retry_count + 1} failed: {e}")
                if _is_capacity_refusal(e):
                    log.error(
                        "[_single_turn_inference] The model server is at capacity and refused the "
                        f"turn ({e}); not retrying, because a retry adds load to a full server and "
                        "makes the caller wait the queue timeout again."
                    )
                    raise InferenceUnavailable(str(e)) from e
                if retry_count < MAX_INFERENCE_RETRIES - 1:
                    wait_time = 5 * (retry_count + 1)
                    log.info(f"[_single_turn_inference] Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    log.error(f"[_single_turn_inference] FATAL: All inference retries failed: {e}")
                    raise InferenceUnavailable(str(e)) from e

        tool_data = extract_action_json(ans)
        if not tool_data:
            # FALLBACK: recover a tool call from numbered-prose model output
            # (the local 35B model often emits that instead of JSON).
            from services.gateway.prose_tools import extract_action_prose

            _prose = extract_action_prose(ans)
            if _prose:
                tool_data = _prose[0]
                log.info(f"[_single_turn_inference] Recovered prose tool call: {tool_data.get('action')}")
        if not tool_data:
            # Detect empty tool_code tags: model tried but produced nothing
            if "<tool_code>" in ans and "</tool_code>" in ans:
                import re as _re
                _tc_inner = _re.search(r"<tool_code>\s*(.*?)\s*</tool_code>", ans, _re.DOTALL)
                if not _tc_inner or not _tc_inner.group(1).strip():
                    log.warning("[_single_turn_inference] Empty tool_code detected, retrying with explicit format reminder")
                    messages.append({"role": "assistant", "content": ans})
                    messages.append({"role": "user", "content": "You output empty tool tags. You MUST emit a JSON object like {\"tool\": \"LightControlRequest\", \"entity_id\": \"light.example_lamp\", \"action\": \"turn_off\"}. Do not output anything else."})
                    continue

            # Detect thinking-only response where content was empty outside <think>
            clean_text = strip_json_from_response(ans).strip()
            if not clean_text and turn < MAX_TURNS - 1:
                log.warning("[_single_turn_inference] Thinking-only response without tool call, prompting model to conclude")
                messages.append({"role": "assistant", "content": ans})
                messages.append({"role": "user", "content": "You must emit your tool call as a JSON object now, or provide a brief final answer."})
                continue

            # No tool call; this is our final answer
            if chunk_callback and turn == 0 and gate is None:
                clean_ans = strip_json_from_response(ans)
                from services.gateway.llm_providers import strip_thinking_blocks
                clean_ans = honest_reply(query, strip_thinking_blocks(clean_ans), device_runs)
                if clean_ans:
                    await chunk_callback({"type": "content", "text": clean_ans})
            break

        # Normalize tool_data keys
        if "arguments" in tool_data and "payload" not in tool_data:
            tool_data["payload"] = tool_data.pop("arguments")
        if "parameters" in tool_data and "payload" not in tool_data:
            tool_data["payload"] = tool_data.pop("parameters")
        if "function" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data["function"].get("name", "")
            tool_data["payload"] = tool_data["function"].get("arguments", {})
        if "operation" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data.pop("operation")
        if "command" in tool_data and "action" not in tool_data:
            tool_data["action"] = tool_data.pop("command")

        # Normalize "state" to "action" for light/climate/HA payloads
        payload_for_state = tool_data.get("payload", tool_data)
        if isinstance(payload_for_state, dict) and "state" in payload_for_state and "action" not in payload_for_state:
            state_val = payload_for_state.pop("state")
            payload_for_state["action"] = state_val
        if "state" in tool_data and "action" not in tool_data and "payload" not in tool_data:
            tool_data["action"] = tool_data.pop("state")

        # Smart routing: separate tool-name (for endpoint lookup) from HA action (for payload)
        # The model emits several shapes:
        #   {"name": "LightControlRequest", "arguments": {"action": "turn_off"}}
        #   {"tool": "LightControlRequest", "action": "turn_off", "entity_id": "..."}
        #   {"name": "LightControlRequest", "arguments": {"state": "off"}}
        # We need: action = normalized tool name for routing, payload.action = HA action.
        # A garden tool's own "action" field (water, mist, ...) is a care verb, not a tool
        # name: route by the tool and keep every other field as its payload.
        _named = re.sub(r'[\s_]+', '', str(tool_data.get("tool") or tool_data.get("name") or "")).lower()
        if _named in _GARDEN_ACTIONS:
            _gp = tool_data.get("payload")
            if not isinstance(_gp, dict):
                _gp = {k: v for k, v in tool_data.items() if k not in ("tool", "name", "payload")}
            tool_data = {"action": _named, "payload": _gp}

        # When a discriminator names a known tool but action is a verb (e.g. TimerRequest+add),
        # promote the tool name to the routing action and keep the verb for payload dispatch.
        promote_tool_discriminator(
            tool_data,
            lambda n: (
                n in SINGLE_TURN_TOOL_ENDPOINTS
                or n == "controlplanerequest"
                or bool(studio_tools.tool_for(n))
                or n in _GARDEN_ACTIONS
                or n.endswith("request")
            ),
        )

        _candidate = str(tool_data.get("action", "")).strip()
        _cand_norm = re.sub(r'[\s_]+', '', _candidate).lower()
        _is_ha_action = _cand_norm in ("turnon", "turnoff", "toggle", "play", "pause", "stop", "send", "messages", "list")

        _META_KEYS = {"action", "tool", "name", "@type", "type", "payload", "function", "id", "operation", "command"}

        if _is_ha_action:
            # action is an HA action; look for a tool/name/@type to route with
            for _k in ("tool", "name", "@type"):
                if _k in tool_data:
                    _tn = tool_data.pop(_k)
                    _tn_norm = re.sub(r'[\s_]+', '', str(_tn)).lower()
                    if _tn_norm in SINGLE_TURN_TOOL_ENDPOINTS or _tn_norm == "controlplanerequest":
                        _pl = tool_data.get("payload", {})
                        if not isinstance(_pl, dict):
                            _pl = {}
                        _pl["action"] = _candidate
                        # Merge remaining top-level fields (entity_id, brightness, etc.) into payload
                        for _fk, _fv in list(tool_data.items()):
                            if _fk not in _META_KEYS:
                                _pl.setdefault(_fk, _fv)
                        tool_data["payload"] = _pl
                        tool_data["action"] = _tn_norm
                        log.info(f"[_single_turn_inference] Remapped routing: {tool_data['action']} payload.action={_candidate}")
                        break
        else:
            # action is a tool name (routing key); ensure payload has valid HA action
            _is_tool_route = (
                _cand_norm in SINGLE_TURN_TOOL_ENDPOINTS
                or _cand_norm == "controlplanerequest"
                or bool(studio_tools.tool_for(_cand_norm))
                or _cand_norm in _GARDEN_ACTIONS
                or _cand_norm.endswith("request")
            )
            if _is_tool_route:
                _pl = tool_data.get("payload")
                if not isinstance(_pl, dict):
                    _pl = {}
                _pl_action_norm = re.sub(r"[\s_]+", "", str(_pl.get("action", ""))).lower()
                if "action" in _pl and (
                    _pl_action_norm in SINGLE_TURN_TOOL_ENDPOINTS
                    or _pl_action_norm == "controlplanerequest"
                    or _pl_action_norm.endswith("request")
                ):
                    # payload.action is also a tool name, not an HA action; remove it
                    _pl.pop("action")
                if "action" not in _pl:
                    sub = _pl.get("sub_action") or _pl.get("device_action") or tool_data.get("sub_action") or tool_data.get("device_action")
                    if sub:
                        _pl["action"] = sub
                # Merge any top-level fields (e.g. hoisted by _normalize_tool) into payload
                for _fk, _fv in list(tool_data.items()):
                    if _fk not in _META_KEYS and _fk not in _pl:
                        _pl[_fk] = _fv
                tool_data["payload"] = _pl

        # Last normalization: tool/name → action (only if action still unset)
        if "action" not in tool_data:
            if "tool" in tool_data:
                tool_data["action"] = tool_data.pop("tool")
            elif "name" in tool_data:
                tool_data["action"] = tool_data.pop("name")

        # Normalize hallucinated/shortened action names
        action_aliases = {
            "filewriterequest": "workspacefilewriterequest",
            "filereadrequest": "workspacefilereadrequest",
            "filepatchrequest": "workspacefilepatchrequest",
        }
        raw_action = tool_data.get("action", "").lower().strip()
        if raw_action in action_aliases:
            tool_data["action"] = action_aliases[raw_action]

        action = tool_data.get("action", "").lower().strip()
        log.info(f"[_single_turn_inference] Tool call detected: {action}")
        log.info(f"[_single_turn_inference] Raw LLM output: {ans[:500]}")
        log.info(f"[_single_turn_inference] Extracted tool_data: {tool_data}")

        # Append LLM's response to conversation
        messages.append({"role": "assistant", "content": ans})
        if event_callback:
            await event_callback({
                "type": "tool_call",
                "id": f"step{turn + 1}",
                "name": tool_data.get("action") or action,
                "input": _public_tool_input(tool_data),
                # What the model said before the call ("Let me find the file...").
                "preamble": _tool_preamble(ans),
            })

        # Execute the tool
        if grounded and re.sub(r'[\s_]+', '', str(action or '')).lower() in _GROUNDED_OMITTED_ACTIONS:
            tool_result = (
                "That tool is not available for this turn. The retrieved context "
                "for this question is already in your prompt, so answer from it "
                "and cite the bracketed source and chapter instead of searching "
                "again."
            )
            log.info("[_single_turn_inference] grounded turn refused a retrieval tool call")
        else:
            tool_result = await _execute_single_tool(action, tool_data, query, creds, workspace_id=workspace_id)
        log.info(f"[_single_turn_inference] Tool result: {tool_result[:300] if tool_result else 'empty'}")
        if re.sub(r'[\s_]+', '', str(action or '')).lower() in DEVICE_TOOLS:
            device_runs.append((action, tool_result or ""))

        # Post-write lint hook: auto-lint after file write/patch to catch syntax errors
        lintable_actions = {"workspacefilewriterequest", "workspacefilepatchrequest"}
        if action.lower() in lintable_actions and isinstance(tool_data.get("payload"), dict):
            payload = tool_data["payload"]
            file_path = payload.get("file_path", "") or payload.get("path", "") or payload.get("relative_path", "")
            if file_path:
                from services.gateway.agent_loop import run_post_write_lint
                settings = await get_all_settings()
                exec_svc = _get(settings, "execution_svc_url")
                user_ctx = payload.get("user_context") or creds.model_dump()
                lint_feedback = await run_post_write_lint(file_path, exec_svc, INTERNAL_SECRET, log, user_ctx)
                if lint_feedback:
                    tool_result = f"{tool_result}\n\n{lint_feedback}"

        # Direct physical/media control actions already yield an actionable, human-friendly message.
        # Returning it immediately saves 2 minutes of second-turn LLM inference and prevents voice assistant JSON reading.
        action_norm = action.lower()
        if action_norm in ("mediaplayrequest", "mediatransportrequest", "lightcontrolrequest", "climatecontrolrequest"):
            try:
                res_obj = json.loads(tool_result)
                if isinstance(res_obj, dict) and res_obj.get("message"):
                    return res_obj["message"]
            except Exception:
                pass

        # Append tool result to conversation for next turn
        messages.append({"role": "user", "content": f"Tool result:\n{tool_result}"})
        prompt_budget.add_tool_result(budget, len(tool_result or ""))
        if event_callback:
            await event_callback({"type": "tool_result", "id": f"step{turn + 1}", "output": (tool_result or "")[:4000]})

        # Out of tool calls: one closing turn turns what the tools found into an
        # answer. Returning the last tool result as the answer is what showed the
        # user "Found 25 matches for 'sermon'" for "transcribe the mkv" -- a status
        # line from the third of three searches, not a reply to anything asked.
        if turn == MAX_TURNS - 1:
            if event_callback:
                await event_callback({"type": "step", "n": "final"})
            if gate is not None:
                await gate.begin_turn()
            closing = await _closing_turn(messages, model, options,
                                          _step_stream("final") if event_callback
                                          else (gate.feed if gate is not None else chunk_callback), tool_result)
            if gate is not None:
                await gate.end_call()
            return honest_reply(query, closing, device_runs)

    # Final answer processing: strip JSON/thinking artifacts for clean natural language
    log.info(f"[_single_turn_inference] Final answer length: {len(ans)} chars, preview: {ans[:200]}")
    final_clean = strip_json_from_response(ans)
    from services.gateway.llm_providers import strip_thinking_blocks
    cleaned = honest_reply(query, strip_thinking_blocks(final_clean), device_runs)
    if not cleaned.strip() and 'tool_result' in locals() and tool_result:
        try:
            res_obj = json.loads(tool_result)
            if isinstance(res_obj, dict) and res_obj.get("message"):
                return res_obj["message"]
        except Exception:
            return tool_result
    return cleaned
