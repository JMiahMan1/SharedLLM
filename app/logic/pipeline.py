# app/logic/pipeline.py
import json
import time
import re
import asyncio
from datetime import datetime
from typing import AsyncGenerator, List, Union, Dict, Optional, Any

# Import Settings
from settings import (
    log,
    run_blocking,
    get_user_creds,
    GlobalResources,
    DEFAULT_MODEL,
    load_system_prompt,
    CONTEXT_REWRITE_PROMPT,
    RAG_TEMPLATE,
    ORCHESTRATOR_PROMPT,
    SIMPLE_RAG_TEMPLATE,
    ACTION_TOOL_CONFIDENCE_THRESHOLD,
    INFORMATIONAL_INTENTS,
)
from intent_engine import engine as intent_engine
from .media_ops import REGEX_INTENT_MAP

from .utils import (
    clean_llm_output,
    update_history,
    get_history_context,
    call_ollama_generate,
    call_openai_chat,
    get_ha_context,
    get_rag_context,
    safe_similarity_search,
)
from .media_ops import (
    handle_media_command,
    execute_ha_service,
    get_entity_state,
    get_last_entity,
)
from .calendar_ops import (
    tool_calendar_list,
    tool_calendar_add,
    tool_calendar_delete,
    tool_calendar_update,
    tool_calendar_read,
)
from .timer_ops import (
    tool_timer_add,
    tool_timer_list,
    tool_timer_delete,
    tool_timer_pause,
    tool_timer_resume,
)
from .note_ops import (
    tool_note_add,
    tool_note_append,
    tool_note_read,
    tool_note_delete,
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
            if role:
                delta["role"] = role
            if content is not None:
                delta["content"] = content
            data = {
                "id": self.req_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": finish_reason}
                ],
            }
            return f"data: {json.dumps(data)}\n\n"
        else:
            data = {
                "model": self.model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": role or "assistant", "content": content or ""},
                "done": False,
            }
            if finish_reason == "stop":
                data["done"] = True
            return json.dumps(data) + "\n"

    def done(self):
        return "data: [DONE]\n\n" if self.format_type == "openai" else ""


async def decompose_command_query(query: str, model: str) -> List[str]:
    """
    Splits compound commands using Regex, but respects quotes to avoid
    breaking titles like "Middy and Ensign".
    """
    # 1. Quick check: If no "and"/"then", return immediately
    if " and " not in query.lower() and " then " not in query.lower():
        return [query]

    # 2. Smart Split: Split on 'and'/'then' ONLY if NOT inside quotes
    # Matches 'and' only if followed by an even number of quotes (meaning it's outside)
    # Handles both single (') and double (") quotes
    split_pattern = r'\s+(?:and|then)\s+(?=(?:[^\'"]*[\'"][^\'"]*[\'"])*[^\'"]*$)'
    
    try:
        parts = re.split(split_pattern, query, flags=re.IGNORECASE)
    except:
        # Fallback to simple split if complex regex fails
        parts = re.split(r"\s+(?:and|then)\s+", query, flags=re.IGNORECASE)

    if len(parts) > 1:
        clean_parts = []
        first_verb = None
        for i, part in enumerate(parts):
            p = part.strip()
            if not p:
                continue
            
            # Detect verb carryover (e.g., "Turn on X and Y" -> "Turn on X", "Turn on Y")
            verb_match = re.match(
                r"^(turn on|turn off|toggle|play|stop|schedule|list|open|launch|scroll|move|set|start)\b",
                p.lower(),
            )
            if verb_match:
                first_verb = verb_match.group(1)
            elif first_verb and i > 0:
                # Only prepend verb if it's not a new natural language query (like "what is...")
                if not re.match(r"^(what|who|how|when|where|is|are)\b", p.lower()):
                    p = f"{first_verb} {p}"
            
            clean_parts.append(p)
        return clean_parts
    
    # 3. Detect "Open/Start [App]" pattern and prevent Fast HA Path from grabbing it as "Turn On"
    # Actually, we let decomposition happen, but we should ensure downstream handles it.
    return [query]


def apply_regex_intent_override(query: str) -> Optional[str]:
    """
    Apply regex-based intent detection before LLM classification.
    Returns intent name if pattern matches, None otherwise.
    """
    q_low = query.lower()
    for pattern, intent in REGEX_INTENT_MAP.items():
        if re.search(pattern, q_low):
            log.debug(f"[REGEX OVERRIDE] Matched '{intent}' via pattern: {pattern[:50]}...")
            return intent
    return None


async def contextualize_query(query, user, model):
    # Try regex override first
    regex_intent = apply_regex_intent_override(query)
    if regex_intent:
        log.info(f"[INTENT] Regex override: '{query}' → {regex_intent}")
        intent = regex_intent
        score = 1.0  # Perfect confidence for regex matches
        is_high_confidence = True
    else:
        # Fall back to LLM classification
        intent, score, is_high_confidence = await intent_engine.classify(
            query, high_confidence_threshold=ACTION_TOOL_CONFIDENCE_THRESHOLD
        )
    
    stateless_intents = [
        "turn_on",
        "turn_off",
        "toggle",
        "play_media",
        "stop_media",
        "calendar_add",
        "calendar_delete",
        "calendar_list",
        "calendar_update",
        "timer_add",
        "alarm_add",
        "timer_delete",
        "timer_list",
        "timer_pause",
        "timer_resume",
        "time_query",
        "intent_learn",
        "open_app",
        "media_next",
        "media_previous",
    ]
    if is_high_confidence and intent in stateless_intents:
        return query
    verbs = [
        "turn",
        "play",
        "stop",
        "toggle",
        "schedule",
        "add",
        "delete",
        "remove",
        "cancel",
        "remind",
        "list",
        "learn",
        "teach",
        "map",
        "set",
        "start",
        "wake",
    ]
    if any(query.lower().lstrip().startswith(v) for v in verbs):
        return query
    hist = get_history_context(user)
    if not hist:
        return query
    if len(query) > 150:
        return query
    prompt = CONTEXT_REWRITE_PROMPT.format(history=hist, query=query)
    r = await call_ollama_generate(prompt, model)
    refined = clean_llm_output(r.get("text", query), is_voice=False)
    if len(refined) > len(query) * 3:
        return query
    return refined


async def _attempt_fast_ha_command(
    query: str, user_creds: Dict[str, str], ha_collection
) -> Optional[Dict[str, Union[str, bool]]]:
    q_low = query.lower().strip()
    if not any(x in q_low for x in ["turn on", "turn off", "toggle", "open", "close"]):
        return None
    
    # 3. Detect "Open/Start [App]" pattern and prevent Fast HA Path from grabbing it as "Turn On"
    if "open" in q_low or "start" in q_low or "launch" in q_low:
        from .media_ops import APP_PACKAGES
        if any(app in q_low for app in APP_PACKAGES):
             log.info(f"Fast HA Path Aborted: Detected App Launch intent in '{q_low}'")
             return None

    if any(
        x in q_low
        for x in ["search", "find", "who", "what", "when", "where", "how", "explain"]
    ):
        return None

    # Determine Service/Intent early
    service = None
    if "turn off" in q_low or "close" in q_low:
        service = "turn_off"
    elif "turn on" in q_low or "open" in q_low:
        service = "turn_on"
    elif "toggle" in q_low:
        service = "toggle"
    
    if not service:
        return None

    # Clean query for search
    clean_q = q_low
    for phrase in [
        "turn on", "turn off", "toggle", "play", "stop", "the", "please", " on ", "open", "close"
    ]:
        clean_q = clean_q.replace(phrase, " ")
    clean_q = clean_q.strip()
    
    if not clean_q:
        return None

    # Use unified smart resolution logic
    from .media_ops import smart_resolve_entity
    
    # We pass the cleaned query (device name) and the intent (service)
    eid, integration = await smart_resolve_entity(clean_q, service, ha_collection, is_music=False)
    
    if not eid:
        # Fallback to LLM if no entity found
        return None

    log.info(f"Fast HA Path: Resolved '{clean_q}' -> {eid} ({integration}) via smart_resolve_entity")

    domain = eid.split(".")[0]
    target_dom = (
        "homeassistant" if service in ["turn_on", "turn_off", "toggle"] else domain
    )
    return await execute_ha_service(
        target_dom, service, eid, user_creds, {}, GlobalResources.redis_client
    )


async def _llm_orchestrator(
    query: str, intent: str, score: float, model: str
) -> Dict[str, Any]:
    orchestrator_prompt = ORCHESTRATOR_PROMPT.format(
        query=query, intent_name=intent, intent_score=score
    )
    last_error = ""
    for attempt in range(2):
        if attempt > 0:
            correction_prompt = f"CRITICAL ERROR: Your previous JSON output failed validation: '{last_error}'. You must output ONLY a single, valid JSON object (DO NOT use markdown backticks). Review your plan and try again. User Query: {query} Best Vector Intent: {intent}."
            r = await call_ollama_generate(
                correction_prompt
                + "\n"
                + ORCHESTRATOR_PROMPT.format(
                    query=query, intent_name=intent, intent_score=score
                ),
                model,
            )
        else:
            r = await call_ollama_generate(orchestrator_prompt, model)
        text = clean_llm_output(r.get("text", ""), is_voice=False).strip()
        try:
            text = text.strip().strip("`").strip()
            if text.startswith("json\n"):
                text = text[5:].strip()
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                parsed_json = json.loads(match.group(0))
                if "action" not in parsed_json:
                    raise ValueError("Missing 'action' key in JSON.")
                return parsed_json
            else:
                raise json.JSONDecodeError("No JSON object found in output.", text, 0)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            continue
    return {
        "action": "CONVERSE",
        "error": "Orchestrator failed to generate valid plan.",
    }


async def _execute_tool_action(
    action_plan: Dict[str, Any], query: str, user_creds: Dict[str, str], model: str
) -> Optional[Dict[str, Union[str, bool]]]:
    tool_name = action_plan.get("tool_name")
    params = action_plan.get("parameters", {})

    if tool_name == "calendar_add":
        res = await tool_calendar_add(
            query, user_creds, model, GlobalResources.redis_client
        )
        return {
            "status": "SUCCESS" if "Scheduled" in res.get("message", "") else "FAILURE",
            "message": res.get("message", "Calendar operation failed."),
            "service": "calendar_add",
        }
    elif tool_name == "calendar_list":
        res = await tool_calendar_list(user_creds, GlobalResources.redis_client)
        return {
            "status": "SUCCESS",
            "message": res.get("message", "Calendar list failed."),
            "service": "calendar_list",
        }
    elif tool_name == "calendar_delete":
        return await tool_calendar_delete(
            query, user_creds, model, GlobalResources.redis_client
        )
    elif tool_name == "calendar_update":
        return await tool_calendar_update(
            query, user_creds, model, GlobalResources.redis_client
        )
    elif tool_name == "timer_add":
        return await tool_timer_add(
            query,
            user_creds,
            model,
            GlobalResources.redis_client,
            GlobalResources.ha_collection,
        )
    elif tool_name == "alarm_add":
        from .timer_ops import tool_alarm_add
        return await tool_alarm_add(
            query,
            user_creds,
            model,
            GlobalResources.redis_client,
            GlobalResources.ha_collection,
        )
    elif tool_name == "timer_list":
        # FIX: Pass redis_client
        return await tool_timer_list(user_creds, GlobalResources.redis_client)
    elif tool_name == "timer_delete":
        # FIX: Pass redis_client
        return await tool_timer_delete(query, user_creds, GlobalResources.redis_client)
    elif tool_name == "timer_pause":
        return await tool_timer_pause(query)
    elif tool_name == "timer_resume":
        return await tool_timer_resume(query)
    elif tool_name == "media_command":
        intent = params.get("intent", "turn_on")
        return await handle_media_command(
            intent,
            query,
            None,
            user_creds,
            GlobalResources.ha_collection,
            GlobalResources.redis_client,
        )
    elif tool_name == "intent_learn":
        res = f"Cannot learn '{params.get('phrase')}' with current prompt context."
        return {"status": "FAILURE", "message": res, "service": "intent_learn"}
    elif tool_name.strip() == "web_search":
        log.info(f"Executing Tool: web_search for query: {query}")
        res = await tool_web_search(query)
        return {"status": "SUCCESS", "message": res, "service": "web_search"}
    elif tool_name == "note_add":
        res = await tool_note_add(params.get("title", "New Note"), params.get("content", query), params.get("category", "General"))
        return {"status": "SUCCESS" if res.get("status") == "success" else "FAILURE", "message": res.get("msg", ""), "service": "note_add"}
    elif tool_name == "note_append":
        res = await tool_note_append(params.get("title", "Shopping List"), params.get("content", query))
        return {"status": "SUCCESS" if res.get("status") == "success" else "FAILURE", "message": res.get("msg", ""), "service": "note_append"}
    elif tool_name == "note_read":
        res = await tool_note_read(params.get("title", ""))
        return {"status": "SUCCESS" if "Note Content" in res else "FAILURE", "message": res, "service": "note_read"}
    elif tool_name == "note_delete":
        res = await tool_note_delete(params.get("title", ""))
        return {"status": "SUCCESS" if "deleted" in res.lower() else "FAILURE", "message": res, "service": "note_delete"}
    return {
        "status": "FAILURE",
        "message": f"Action requested unhandled tool: {tool_name}",
        "service": tool_name,
    }


async def _handle_single_command(
    query: Union[str, Dict], user_creds: Dict[str, str], model: str = None
) -> Optional[List[Dict[str, Any]]]:
    if isinstance(query, dict):
        query = str(query.get("response", query.get("text", str(query))))
    if not isinstance(query, str):
        query = str(query)
    if len(query) > 150:
        return None
    action_result = await _attempt_fast_ha_command(
        query, user_creds, GlobalResources.ha_collection
    )
    if action_result:
        return [action_result]
    
    # Try regex override first for color/brightness commands
    regex_intent = apply_regex_intent_override(query)
    if regex_intent:
        log.info(f"[INTENT] Regex override in single_command: '{query}' → {regex_intent}")
        intent = regex_intent
        score = 1.0
        is_high_confidence = True
    else:
        intent, score, is_high_confidence = await intent_engine.classify(
            query, high_confidence_threshold=ACTION_TOOL_CONFIDENCE_THRESHOLD
        )
    
    orchestration_plan = await _llm_orchestrator(
        query, intent or "unknown", score, model
    )
    action_type = orchestration_plan.get("action")
    if action_type == "CONVERSE":
        return None
    if action_type == "tool_call":
        result = await _execute_tool_action(
            orchestration_plan, query, user_creds, model
        )
        return [result] if result else None
    return None


async def try_handle_compound_command(
    query, user_creds, model
) -> Optional[List[Dict[str, Any]]]:
    if "### Task:" in query or "JSON format" in query or "<chat_history>" in query:
        return None
    if re.match(r"^(what|who|when|how|why)\b", query.lower().strip()):
        if " and " not in query.lower():
            if "what time" in query.lower() or "current time" in query.lower():
                now = datetime.now()
                return [
                    {
                        "status": "SUCCESS",
                        "message": f"It is currently {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}.",
                    }
                ]
            return None
    cmds = await decompose_command_query(query, model)
    tasks = []
    if len(cmds) > 1:
        for c in cmds:
            if isinstance(c, str) and len(c.strip()) > 2:
                tasks.append(_handle_single_command(c, user_creds, model))
    else:
        tasks.append(_handle_single_command(query, user_creds, model))
    if not tasks:
        return None
    results = await asyncio.gather(*tasks)
    valid_results = []
    for r_list in results:
        if isinstance(r_list, list):
            valid_results.extend([r for r in r_list if isinstance(r, dict)])
        elif isinstance(r_list, dict):
            valid_results.append(r_list)
    if not valid_results:
        return None
    return valid_results


async def generate_rag_stream(
    query, user, model, use_openai, format_type
) -> AsyncGenerator[str, None]:
    t0 = time.time()
    builder = StreamResponseBuilder(model, format_type)
    is_voice = format_type != "openai"
    if "### Task:" in query or "JSON format" in query or "<chat_history>" in query:
        yield builder.chunk(role="assistant")
        r = await call_ollama_generate(query, model, stream=True)
        if "iterable" in r:
            async for c in r["iterable"]():
                yield builder.chunk(content=c.get("response", ""))
        elif "text" in r:
            yield builder.chunk(content=r["text"])
        yield builder.chunk(finish_reason="stop")
        yield builder.done()
        return

    refined = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    creds = get_user_creds(user)
    intent, score, _ = await intent_engine.classify(refined)
    t_action = time.time()
    action_results = await try_handle_compound_command(refined, creds, model)

    action_context = ""
    run_knowledge_retrieval = True
    
    if action_results:
        # 1. Determine if the action was purely informational (Search, List, Read)
        is_informational_tool = False
        for res in action_results:
            svc = res.get("service", "")
            if svc in ["web_search", "calendar_list", "timer_list", "calendar_read"]:
                is_informational_tool = True
        
        # 2. Only disable RAG if it's NOT an informational intent AND NOT an informational tool
        if intent not in INFORMATIONAL_INTENTS and not is_informational_tool:
            run_knowledge_retrieval = False
            
        action_context = "### PREVIOUS ACTIONS (Use to inform your response. Do not hallucinate success/failure):\n"
        log.debug(f"[ACTION RESULTS] {len(action_results)} results: {action_results}")
        for res in action_results:
            status = res.get("status", "FAILURE")
            msg = res.get("message", "Unknown action.")
            if status == "SUCCESS":
                new_state = res.get("new_state", "N/A")
                friendly_name = res.get("friendly_name", "N/A")
                service = res.get("service", "N/A")
                action_context += f"- SUCCESS: Command '{service}' sent to {friendly_name}. Verified New State: {new_state}\n"
                if service in ["web_search", "timer_list", "calendar_list", "calendar_read"]:
                    action_context += f"TOOL OUTPUT:\n{msg}\n"
            else:
                entity = res.get("entity_id", "N/A")
                service = res.get("service", "N/A")
                action_context += f"- FAILURE: Command '{service}' on '{entity}' failed. Reason: {msg}\n"
        
        log.debug(f"[ACTION CONTEXT] Sending to LLM:\n{action_context}")

    ha_ctx, nc_ctx, search_ctx, cal_ctx = "", "", "", ""
    if run_knowledge_retrieval:
        fetch_ha = True
        fetch_nc = True
        if intent == "content_query":
            fetch_ha = False
        elif intent in [
            "turn_on",
            "turn_off",
            "toggle",
            "play_media",
            "stop_media",
            "open_app",
        ]:
            fetch_nc = False
        elif intent and (intent.startswith("calendar") or intent.startswith("timer")):
            fetch_ha = False
            fetch_nc = False

        tasks = []
        if fetch_ha:
            tasks.append(get_ha_context(user, refined))
        else:
            tasks.append(asyncio.sleep(0))
        if fetch_nc:
            tasks.append(get_rag_context(refined))
        else:
            tasks.append(asyncio.sleep(0))
        
        # Optimized: Only run Web Search if no other tools were executed
        already_searched = any(r.get("service") == "web_search" for r in (action_results or []))
        should_search = not action_results and not already_searched
        
        if should_search:
             tasks.append(tool_web_search(refined))
        else:
             tasks.append(asyncio.sleep(0))

        results = await asyncio.gather(*tasks)
        ha_ctx = results[0] if fetch_ha else ""
        nc_ctx = results[1] if fetch_nc else ""
        if not already_searched:
            search_ctx = results[2]
        
        if any(
            x in refined.lower()
            for x in ["calendar", "schedule", "meeting", "today", "tomorrow"]
        ):
            if not action_results:
                cal_ctx = await tool_calendar_read(creds, GlobalResources.redis_client)

    from settings import load_system_prompt

    base_sys_prompt = load_system_prompt()
    now = datetime.now()
    sys_info = f"Current Date: {now.strftime('%A, %B %d, %Y')}\nCurrent Time: {now.strftime('%I:%M %p')}"
    simple_intents = [
        "turn_on",
        "turn_off",
        "toggle",
        "play_media",
        "stop_media",
        "media_next",
        "media_previous",
        "open_app",
        "timer_add",
        "alarm_add",
        "timer_delete",
    ]
    use_simple = False
    # Only use simple template if success AND NOT searching
    if action_results and intent in simple_intents:
        if all(r.get("status") == "SUCCESS" for r in action_results) and not any(r.get("service") == "web_search" for r in action_results):
            use_simple = True
            
    template_to_use = SIMPLE_RAG_TEMPLATE if use_simple else RAG_TEMPLATE
    prompt = template_to_use.format(
        system_prompt=base_sys_prompt,
        sys_info=sys_info,
        ha_ctx=ha_ctx,
        nc_ctx=nc_ctx,
        search_ctx=search_ctx,
        cal_ctx=cal_ctx,
        query=refined,
        action_context=action_context,
    )
    if action_context and not use_simple:
        prompt += f"\n{action_context}"

    yield builder.chunk(role="assistant")
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
            token = clean_llm_output(token, is_voice)
            full_text += token
            yield builder.chunk(content=token)
    elif "text" in r:
        full_text = clean_llm_output(r["text"], is_voice)
        yield builder.chunk(content=full_text)
    update_history(user, "assistant", full_text)
    yield builder.chunk(finish_reason="stop")
    yield builder.done()
