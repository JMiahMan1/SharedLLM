# app/logic/pipeline.py
import json
import time
import re
import asyncio
from datetime import datetime
from typing import AsyncGenerator, List, Union, Dict

# Import Settings
from settings import (
    log, run_blocking, get_user_creds, GlobalResources, 
    DEFAULT_MODEL, load_system_prompt,
    CONTEXT_REWRITE_PROMPT, RAG_TEMPLATE
)
from intent_engine import engine as intent_engine

# --- Import Split Modules ---
from .utils import (
    clean_llm_output, update_history, get_history_context, 
    call_ollama_generate, call_openai_chat, 
    get_ha_context, get_rag_context, safe_similarity_search
)
from .media_ops import (
    handle_media_command, 
    execute_ha_service, 
    get_entity_state,
    get_last_entity
)
from .calendar_ops import (
    tool_calendar_list, tool_calendar_add, 
    tool_calendar_delete, tool_calendar_update, tool_calendar_read
)
from .web_search import tool_web_search

class StreamResponseBuilder:
    def __init__(self, model: str, format_type: str):
        self.model = model
        self.format_type = format_type
        self.req_id = f"chatcmpl-{int(time.time())}"
        self.created = int(time.time())

    def chunk(self, content=None, role=None, finish_reason=None):
        if self.format_type == "openai":
            delta = {}
            if role: delta["role"] = role
            if content is not None: delta["content"] = content
            data = {
                "id": self.req_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
            }
            return f"data: {json.dumps(data)}\n\n"
        else:
            data = {
                "model": self.model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": role or "assistant", "content": content or ""},
                "done": False
            }
            if finish_reason == "stop": data["done"] = True
            return json.dumps(data) + "\n"

    def done(self):
        return "data: [DONE]\n\n" if self.format_type == "openai" else ""

async def decompose_command_query(query: str, model: str) -> List[str]:
    """
    Splits compound commands using Regex (Fast).
    Example: "Turn on lights and play music" -> ["Turn on lights", "play music"]
    """
    # Optimization: Skip decomposition if no conjunction
    if " and " not in query.lower(): return [query]
    
    # Regex Fast Split
    parts = re.split(r'\s+(?:and|then)\s+|,\s+', query, flags=re.IGNORECASE)
    if len(parts) > 1:
        clean_parts = []
        # Verb Carryover logic (if second part has no verb, use first part's verb)
        first_verb = None
        
        for i, part in enumerate(parts):
            p = part.strip()
            if not p: continue
            
            # Identify Verb using Regex
            verb_match = re.match(r"^(turn on|turn off|toggle|play|stop|schedule|list|open|launch|scroll|move)\b", p.lower())
            if verb_match:
                first_verb = verb_match.group(1)
            elif first_verb and i > 0:
                # If no verb found, prepend the last known verb
                p = f"{first_verb} {p}"
            
            clean_parts.append(p)
            
        return clean_parts
        
    return [query]

async def contextualize_query(query, user, model):
    """
    Rewrites query based on history using externalized prompt.
    """
    # Optimization: If intent engine is confident, skip contextualization
    intent, score = await intent_engine.classify(query)
    
    stateless_intents = [
        "turn_on", "turn_off", "toggle", "play_media", "stop_media",
        "calendar_add", "calendar_delete", "calendar_list", "calendar_update",
        "time_query", "intent_learn", "open_app", "media_next", "media_previous"
    ]
    
    if score > 0.85 and intent in stateless_intents:
        return query

    verbs = ["turn", "play", "stop", "toggle", "schedule", "add", "delete", "remove", "cancel", "remind", "list", "learn", "teach", "map"]
    if any(query.lower().lstrip().startswith(v) for v in verbs): return query
    
    hist = get_history_context(user)
    if not hist: return query
    
    if len(query) > 150: return query

    # Use Externalized Prompt from Settings
    prompt = CONTEXT_REWRITE_PROMPT.format(history=hist, query=query)
    
    r = await call_ollama_generate(prompt, model)
    # Use is_voice=False to preserve content during rewrite
    refined = clean_llm_output(r.get("text", query), is_voice=False) 
    
    if len(refined) > len(query) * 3: return query
    return refined

async def _handle_single_command(query: Union[str, Dict], user_creds: Dict[str, str], model: str = None):
    """
    Routes a single, atomic command to the correct module (Calendar vs Media vs Generic).
    """
    if isinstance(query, dict):
        query = str(query.get("response", query.get("text", str(query))))
    if not isinstance(query, str): query = str(query)
    
    if len(query) > 150:
        log.warning(f"Ignoring overly long command ({len(query)} chars): {query[:50]}...")
        return None

    q_low = query.lower().strip()
    
    # --- CRITICAL: REGEX INTENT OVERRIDES ---
    # Bypass vector engine for obvious app/nav commands to ensure smart routing triggers
    regex_intent = None
    if re.search(r"\b(open|launch|start)\s+(netflix|youtube|disney|hulu|plex|prime|spotify)", q_low):
        regex_intent = "open_app"
    elif re.search(r"\b(play)\b", q_low):
        regex_intent = "play_media"
    elif re.search(r"\b(scroll|move|go)\s+(up|down|left|right|back|home)", q_low):
        if "up" in q_low: regex_intent = "nav_up"
        elif "down" in q_low: regex_intent = "nav_down"
        elif "left" in q_low: regex_intent = "nav_left"
        elif "right" in q_low: regex_intent = "nav_right"
        elif "back" in q_low: regex_intent = "nav_back"
        elif "home" in q_low: regex_intent = "nav_home"
    elif "select" in q_low or "enter" in q_low or "ok" in q_low:
        regex_intent = "nav_enter"

    if regex_intent:
        intent = regex_intent
    else:
        intent, score = await intent_engine.classify(query)

    # --- INTENT ROUTING ---
    if intent == "intent_learn":
        # Basic Regex for learning phrases
        match = re.search(r"(?:learn|teach|map).+(?:that|phrase)\s+[\"']?(.+?)[\"']?\s+(?:means|to|is)\s+[\"']?([a-z_]+)[\"']?", q_low)
        if match:
            phrase, target_intent = match.groups()
            valid_intents = intent_engine.get_valid_intents()
            if target_intent not in valid_intents:
                clean_target = target_intent.replace(" ", "_")
                if clean_target in valid_intents: target_intent = clean_target
                else: return f"I can't learn that. '{target_intent}' is not a valid capability."
            await intent_engine.learn(phrase, target_intent)
            return f"Understood. I have learned that '{phrase}' means '{target_intent}'."
        return "I didn't catch the phrase and intent."

    if intent == "time_query":
        now = datetime.now()
        return f"It is currently {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}."

    # --- CALENDAR ROUTING ---
    elif intent == "calendar_list":
        return await tool_calendar_list(user_creds, GlobalResources.redis_client)
    elif intent == "calendar_add":
        return await tool_calendar_add(query, user_creds, model or DEFAULT_MODEL, GlobalResources.redis_client)
    elif intent == "calendar_delete":
        return await tool_calendar_delete(query, user_creds, model or DEFAULT_MODEL, GlobalResources.redis_client)
    elif intent == "calendar_update":
        return await tool_calendar_update(query, user_creds, model or DEFAULT_MODEL, GlobalResources.redis_client)
    elif intent == "content_query":
         return None 
    elif intent == "general_query":
         return None

    # CALENDAR REGEX FALLBACK (If vectors fail)
    if not intent:
        if any(x in q_low for x in ["schedule", "add event", "new appointment", "remind me"]):
             return await tool_calendar_add(query, user_creds, model or DEFAULT_MODEL, GlobalResources.redis_client)
        elif any(x in q_low for x in ["delete event", "cancel meeting", "remove appointment"]):
             return await tool_calendar_delete(query, user_creds, model or DEFAULT_MODEL, GlobalResources.redis_client)
        elif any(x in q_low for x in ["list calendar", "show schedule", "check agenda"]):
             return await tool_calendar_list(user_creds, GlobalResources.redis_client)
        elif any(x in q_low for x in ["reschedule", "move event", "change time", "update event"]):
             return await tool_calendar_update(query, user_creds, model or DEFAULT_MODEL, GlobalResources.redis_client)
    
    # --- MEDIA / HA ROUTING ---
    media_intents = [
        "turn_on", "turn_off", "toggle", 
        "stop_media", "play_media", "open_app",
        "media_next", "media_previous",
        "nav_up", "nav_down", "nav_left", "nav_right", 
        "nav_enter", "nav_back", "nav_home"
    ]
    
    # Power Regex Fallback
    if not intent:
        if "turn on" in q_low: intent = "turn_on"
        elif "turn off" in q_low: intent = "turn_off"
        elif "play" in q_low: intent = "play_media"
        elif "stop" in q_low: intent = "stop_media"
        elif "open" in q_low: intent = "open_app"

    if intent in media_intents:
        # Delegate to media_ops for Smart Routing (TV vs Music)
        # Passing None for entity_id lets the handler invoke smart_resolve_entity
        return await handle_media_command(
            intent, 
            query, 
            None, 
            user_creds, 
            GlobalResources.ha_collection, 
            GlobalResources.redis_client
        )
    
    # --- GENERIC HA FALLBACK ---
    # CRITICAL FIX: Prevent "Search the web" from falling into HA Control
    if any(x in q_low for x in ["search", "find", "who", "what", "when", "where", "how", "explain"]):
        return None

    clean_q = q_low
    for phrase in ["turn on", "turn off", "toggle", "play", "stop", "the", "please", " on ", "open"]:
        clean_q = clean_q.replace(phrase, " ")
    clean_q = clean_q.strip()

    eid = None
    # CASE 1: Partial Command (No object specified) -> Check Redis
    if not clean_q: 
        eid = get_last_entity(GlobalResources.redis_client, user_creds.get("user"))
        if not eid:
            # If it's a question like "Who is he?", don't fail, just return None to let RAG handle it
            return None 
        log.info(f"Using Cached Last Entity: {eid} for command: {q_low}")

    # CASE 2: Explicit Command -> Vector Search
    else:
        if GlobalResources.ha_collection:
            # Uses safe_similarity_search from utils
            docs = await run_blocking(lambda: safe_similarity_search(GlobalResources.ha_collection, clean_q, k=1))
            if docs:
                eid = docs[0].metadata.get("entity_id")

    if not eid: return None

    service = "turn_on"
    if "turn off" in q_low: service = "turn_off"
    elif "toggle" in q_low: service = "toggle"
    
    domain = eid.split(".")[0]
    target_dom = "homeassistant" if service in ["turn_on", "turn_off", "toggle"] else domain
    
    # Uses execute_ha_service from media_ops (shared)
    return await execute_ha_service(target_dom, service, eid, user_creds, {}, GlobalResources.redis_client)

async def try_handle_compound_command(query, user_creds, model):
    """
    Manages decomposition and execution of (potentially multiple) commands.
    """
    # META-PROMPT BYPASS (OpenWebUI)
    if "### Task:" in query or "JSON format" in query or "<chat_history>" in query:
        return None

    # Questions bypass tools
    if re.match(r"^(what|who|when|how|why)\b", query.lower().strip()): 
        if " and " not in query.lower():
             if "what time" in query.lower() or "current time" in query.lower():
                 now = datetime.now()
                 return f"It is currently {now.strftime('%I:%M %p')}."
             return None
    
    # FIRST PASS: Check if main query is a single strong intent
    primary_intent, score = await intent_engine.classify(query)
    if primary_intent in ["time_query", "calendar_list", "intent_learn", "general_query"]:
        if " and " not in query.lower():
             return await _handle_single_command(query, user_creds, model)

    # DECOMPOSITION: Split "Turn on X and Y"
    cmds = await decompose_command_query(query, model)
    tasks = []
    for c in cmds:
        if isinstance(c, str) and len(c.strip()) > 2:
            tasks.append(_handle_single_command(c, user_creds, model))
            
    if not tasks: return None
    
    # Run parallel
    results = await asyncio.gather(*tasks)
    valid = [r for r in results if r]
    
    if valid:
        return "\n".join(valid)
    return None

async def generate_rag_stream(query, user, model, use_openai, format_type) -> AsyncGenerator[str, None]:
    """
    Main Pipeline Entry Point.
    """
    # Start Timer
    t0 = time.time()
    
    builder = StreamResponseBuilder(model, format_type)
    is_voice = (format_type != "openai")

    # 0. META-PROMPT BYPASS
    if "### Task:" in query or "JSON format" in query or "<chat_history>" in query:
        yield builder.chunk(role="assistant")
        r = await call_ollama_generate(query, model, stream=True)
        if "iterable" in r:
            async for c in r["iterable"]():
                token = c.get("response", "")
                yield builder.chunk(content=token)
        elif "text" in r:
            yield builder.chunk(content=r["text"])
        yield builder.chunk(finish_reason="stop")
        yield builder.done()
        return

    # 1. Contextualize (Rewrite) Query
    refined = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    creds = get_user_creds(user)
    
    # 2. Action Layer (Tools)
    t_action = time.time()
    action_result = await try_handle_compound_command(refined, creds, model)
    log.debug(f"Action Layer took: {time.time() - t_action:.4f}s")
    
    if action_result:
        update_history(user, "assistant", action_result)
        log.info(f"FINAL RESPONSE TO CLIENT: {action_result}")
        log.info(f"Total Request Time: {time.time() - t0:.4f}s")
        yield builder.chunk(role="assistant")
        yield builder.chunk(content=action_result)
        yield builder.chunk(finish_reason="stop")
        yield builder.done()
        return

    # 3. Knowledge Layer (RAG)
    t_rag = time.time()
    
    # Launch retrievers in parallel
    ha_future = get_ha_context(user, refined)
    nc_future = get_rag_context(refined)
    search_future = tool_web_search(refined)
    
    ha_ctx, nc_ctx, search_ctx = await asyncio.gather(ha_future, nc_future, search_future)
    
    # Calendar Context Injection
    cal_ctx = ""
    if any(x in refined.lower() for x in ["calendar", "schedule", "meeting", "today", "tomorrow"]):
        if not any(x in refined.lower() for x in ["schedule a", "add", "remind", "cancel", "delete", "move", "reschedule"]):
            cal_ctx = await tool_calendar_read(creds, GlobalResources.redis_client)
            
    log.debug(f"Context Retrieval took: {time.time() - t_rag:.4f}s")

    from settings import load_system_prompt
    base_sys_prompt = load_system_prompt()
    
    now = datetime.now()
    sys_info = f"Current Date: {now.strftime('%A, %B %d, %Y')}\nCurrent Time: {now.strftime('%I:%M %p')}"
    
    # Use Externalized Prompt Template from Settings
    prompt = RAG_TEMPLATE.format(
        system_prompt=base_sys_prompt,
        sys_info=sys_info,
        ha_ctx=ha_ctx,
        nc_ctx=nc_ctx,
        search_ctx=search_ctx,
        cal_ctx=cal_ctx,
        query=refined
    )
    
    yield builder.chunk(role="assistant")
    
    # 4. LLM Generation
    r = None
    if use_openai and openai_client:
        messages = [{"role": "system", "content": prompt}]
        r = await call_openai_chat(messages, model, stream=True)
    else:
        r = await call_ollama_generate(prompt, model, stream=True)
    
    full_text = ""
    if "iterable" in r:
        async for c in r["iterable"]():
            token = c.get("response", "")
            # CLEAN OUTPUT (Token-Safe)
            token = clean_llm_output(token, is_voice) 
            full_text += token
            yield builder.chunk(content=token)
    elif "text" in r:
        full_text = clean_llm_output(r["text"], is_voice)
        yield builder.chunk(content=full_text)
    
    log.info(f"FINAL RESPONSE TO CLIENT: {full_text}")
    log.info(f"Total Request Time: {time.time() - t0:.4f}s")
    update_history(user, "assistant", full_text)
    yield builder.chunk(finish_reason="stop")
    yield builder.done()
