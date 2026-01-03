# app/logic/pipeline.py
import json
import time
import re
import asyncio
from datetime import datetime
from typing import AsyncGenerator, List, Union, Dict, Optional, Any

# Import Settings
from app.settings import (
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

SILENT_SUCCESS_TOKEN = "Done."

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

# --- ARCHITECTURE REFACTOR IMPORTS ---
from .execution.registry import ActionDispatcher
from .execution.fast_path import FastPathExecutor
from .intents.classifier import IntentClassifier
from .web_search import tool_web_search

# Ensure handlers are registered
import app.logic.execution.handlers
from app.logic.calendar_ops import tool_calendar_read


class StreamResponseBuilder:
    def __init__(self, model: str, format_type: str):
        self.model = model
        self.format_type = format_type
        self.req_id = f"chatcmpl-{int(time.time())}"
        self.created = int(time.time())

    def chunk(self, content=None, role=None, finish_reason=None, tool_results=None):
        if self.format_type == "openai":
            delta = {}
            if role:
                delta["role"] = role
            if content is not None:
                delta["content"] = content
            if tool_results is not None:
                delta["tool_results"] = tool_results
                
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
            if tool_results is not None:
                data["tool_results"] = tool_results
                
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
    split_pattern = r'\s+(?:and|then)\s+(?=(?:[^\'"]*[\'"][^\'"]*[\'"])*[^\'"]*$)'

    try:
        parts = re.split(split_pattern, query, flags=re.IGNORECASE)
    except:
        parts = re.split(r"\s+(?:and|then)\s+", query, flags=re.IGNORECASE)

    if len(parts) > 1:
        clean_parts = []
        first_verb = None
        for i, part in enumerate(parts):
            p = part.strip()
            if not p:
                continue

            # Detect verb carryover
            verb_match = re.match(
                r"^(turn on|turn off|toggle|play|stop|schedule|list|open|launch|scroll|move|set|start)\b",
                p.lower(),
            )
            if verb_match:
                first_verb = verb_match.group(1)
            elif first_verb and i > 0:
                if not re.match(r"^(what|who|how|when|where|is|are)\b", p.lower()):
                    p = f"{first_verb} {p}"

            clean_parts.append(p)
        return clean_parts

    return [query]


async def contextualize_query(query, user, model):
    """
    Contextualizes query and returns both refined query and intent.
    Returns: (refined_query, intent, score, is_high_confidence, intent_locked)
    """
    # Use Modular Classifier
    intent, score, is_high_confidence, intent_locked = await IntentClassifier.get_intent(query)

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
        "volume_up",
        "volume_down",
        "volume_set",
        "volume_mute",
    ]
    if is_high_confidence and intent in stateless_intents:
        return query, intent, score, is_high_confidence, intent_locked
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
        return query, intent, score, is_high_confidence, intent_locked
    hist = get_history_context(user)
    if not hist:
        return query, intent, score, is_high_confidence, intent_locked

    # Explicit handling for confirmations
    if query.lower().strip().strip("!.") in ["yes", "sure", "please", "please do", "ok", "yep", "do it"]:
         special_prompt = f"History:\n{hist}\nThe user confirmed 'Yes' to the Assistant's last question. Rewrite 'Yes' into a full, explicit command (e.g., 'Turn on the TV' or 'Turn on TV and play music').\nRefined Command (No JSON, Just Text):"
         r = await call_ollama_generate(special_prompt, model)
         refined = clean_llm_output(r.get("text", query), is_voice=False)
         log.info(f"[CONTEXT REWRITE] Confirmation '{query}' -> '{refined}'")
         return refined, intent, score, is_high_confidence, intent_locked

    # [OPTIMIZATION] Skip rewrite if intent is already locked/high confidence
    # This avoids unnecessary LLM calls (and potential timeouts) for clear commands
    if intent_locked and is_high_confidence:
        log.info(f"[CONTEXT REWRITE] Skipping rewrite for locked intent: {intent}")
        return query, intent, score, is_high_confidence, intent_locked

    if len(query) > 150:
        return query, intent, score, is_high_confidence, intent_locked
    prompt = CONTEXT_REWRITE_PROMPT.format(history=hist, query=query)
    r = await call_ollama_generate(prompt, model)
    refined = clean_llm_output(r.get("text", query), is_voice=False)
    if len(refined) > len(query) * 3:
        return query, intent, score, is_high_confidence, intent_locked
    return refined, intent, score, is_high_confidence, intent_locked


async def _llm_orchestrator(
    query: str, intent: str, score: float, model: str, conversation_history: str = "", intent_locked: bool = False
) -> Dict[str, Any]:
    # If intent is locked (regex-detected), instruct LLM to preserve it
    lock_instruction = ""
    if intent_locked:
        lock_instruction = f"\n\nCRITICAL: The intent '{intent}' is LOCKED and was explicitly detected from the user's verb choice. You MUST use this intent in your response. DO NOT change it to any other intent. Extract only the device_name and media_title parameters."
    
    orchestrator_prompt = ORCHESTRATOR_PROMPT.format(
        query=query, intent_name=intent, intent_score=score, conversation_history=conversation_history
    ) + lock_instruction
    last_error = ""
    for attempt in range(2):
        if attempt > 0:
            correction_prompt = f"CRITICAL ERROR: Your previous JSON output failed validation: '{last_error}'. You must output ONLY a single, valid JSON object (DO NOT use markdown backticks). Review your plan and try again. User Query: {query} Best Vector Intent: {intent}."
            r = await call_ollama_generate(
                correction_prompt
                + "\n"
                + ORCHESTRATOR_PROMPT.format(
                    query=query, intent_name=intent, intent_score=score, conversation_history=conversation_history
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
    """Delegates execution to the ActionDispatcher registry."""
    tool_name = action_plan.get("tool_name")
    params = action_plan.get("parameters", {})

    return await ActionDispatcher.dispatch(
        tool_name, query=query, user_creds=user_creds, model=model, params=params
    )


async def _handle_single_command(
    query: Union[str, Dict], user_creds: Dict[str, str], model: str = None, 
    intent: Optional[str] = None, score: float = 0.0, is_high_confidence: bool = False, intent_locked: bool = False
) -> Optional[List[Dict[str, Any]]]:
    if isinstance(query, dict):
        query = str(query.get("response", query.get("text", str(query))))
    if not isinstance(query, str):
        query = str(query)
    if len(query) > 150:
        return None

    # 1. Attempt Fast Path (Direct HA Command)
    action_result = await FastPathExecutor.attempt_fast_command(
        query, user_creds, GlobalResources.ha_collection
    )
    if action_result:
        return [action_result]

    # 2. Intent Classification (only if not provided)
    intent_locked = False
    if intent is None:
        try:
            intent, score, is_high_confidence, intent_locked = await IntentClassifier.get_intent(query)
            if intent:
                log.info(f"[PIPELINE DEBUG] Intent detected: {intent} (Score: {score}, Locked: {intent_locked})")
        except Exception as e:
            log.exception(f"[PIPELINE ERROR] IntentClassifier failed: {e}")
            intent, score, is_high_confidence, intent_locked = None, 0.0, False, False
    else:
        log.info(f"[PIPELINE DEBUG] Using provided intent: {intent} (Score: {score})")

    # --- FAST PATH ORCHESTRATION ---
    # Skip LLM for simple, high-confidence intents to avoid timeouts
    print(f"[DEBUG] FAST PATH ORCHESTRATION Starting for intent={intent}, is_high_confidence={is_high_confidence}")
    log.info(f"[FAST PATH ORCHESTRATION] Starting for intent={intent}, is_high_confidence={is_high_confidence}")
    if is_high_confidence and intent:
        print(f"[DEBUG] FAST PATH Condition met, processing {intent}")
        log.info(f"[FAST PATH] Condition met, processing {intent}")
        action_plan = None
        if intent in ["volume_set", "volume_up", "volume_down", "volume_mute"]:
             # Extract volume from query using regex here or in the tool?
             # The tool logic already handles extraction from query.
             # We just need to route it.
             action_plan = {
                "action": "tool_call",
                "tool_name": "media_command",
                "parameters": {"intent": intent, "device_name": None} # None enables context lookup or query parsing
             }
        elif intent in ["media_next", "media_previous", "stop_media", "play_media", "watch_media", "turn_on", "turn_off", "toggle"]:
            log.info(f"[FAST PATH] Matched transport intent: {intent}")
            # For transport commands, only set device_name if query contains a device reference
            device_in_query = bool(re.search(r"\b(on|in)\s+(the\s+)?(office|tv|bedroom|kitchen|speaker|remote|media)\b", query.lower()))
            log.info(f"[FAST PATH] device_in_query: {device_in_query}, query: {query}")

            device_name = None
            if device_in_query:
                # Extract device name from query by finding text after "on" or "in"
                # Improved regex to stop at common end-of-sentence or connector words if needed, but keeping simple for now
                match = re.search(r"\b(on|in)\s+(the\s+)?(.+)", query.lower())
                if match:
                    device_part = match.group(3).strip()
                    # Take first few words as device name (avoid including the media title)
                    words = device_part.split()[:3]  # Limit to 3 words
                    device_name = " ".join(words).title()
            
            # For play/watch, we should pass the query as media_title if specific params aren't extracted
            # The handler will clean it up.
            params = {"intent": intent, "device_name": device_name}
            if intent in ["play_media", "watch_media"]:
                params["media_title"] = query # Pass full query lets handler/integration clean it

            action_plan = {
                "action": "tool_call",
                "tool_name": "media_command",
                "parameters": params
            }
            log.info(f"[FAST PATH] action_plan set: {action_plan}")
        elif intent in ["pause_media", "media_play", "timer_pause", "timer_resume", "timer_delete"]:
            # Special handling for pause/resume - call dedicated handlers
            log.info(f"[FAST PATH] Matched control intent: {intent}")
            action_plan = {
                "action": "tool_call",
                "tool_name": intent,  # Call handler by intent name directly
                "parameters": {}
            }
            log.info(f"[FAST PATH] action_plan set: {action_plan}")
        elif intent in ["open_app"]:
             action_plan = {
                "action": "tool_call",
                "tool_name": "media_command",
                "parameters": {"intent": intent, "device_name": query, "media": query}
             }
        elif intent in ["set_brightness", "dim", "brighten", "set_color"]:
            log.info(f"[FAST PATH] Matched lighting intent: {intent}")
            action_plan = {
                "action": "tool_call",
                "tool_name": intent,  # Route to the registered handler
                "parameters": {}
            }
            log.info(f"[FAST PATH] action_plan set: {action_plan}")
        
        if action_plan:
            log.info(f"[FAST ORCHESTRA] Bypassing LLM for {intent}")
            try:
                result = await _execute_tool_action(
                    action_plan, query, user_creds, model
                )
                # Result may already be a list (for handlers that return multiple results)
                if isinstance(result, list):
                    return result
                else:
                    return [result] if result else None
            except Exception as e:
                log.error(f"[FAST ORCHESTRA] Failed: {e}")
                # Fallback to LLM if fast path fails?
                pass

    # 3. LLM Orchestration
    try:
        # Get conversation history for contextualization
        from .utils import get_history_context
        user = user_creds.get("user", "default")
        conversation_history = get_history_context(user)
        history_text = ""
        if conversation_history:
            history_text = f"\nConversation History:\n{conversation_history}"

        orchestration_plan = await _llm_orchestrator(
            query, intent or "unknown", score, model, history_text, intent_locked
        )
        log.info(f"[PIPELINE DEBUG] Orchestration Plan: {orchestration_plan}")
        
        # [INTENT LOCK ENFORCEMENT] If intent was locked, restore it if LLM changed it
        log.info(f"[INTENT LOCK DEBUG] intent_locked={intent_locked}, action={orchestration_plan.get('action')}, original_intent={intent}")
        if intent_locked and orchestration_plan.get("action") == "tool_call":
            plan_intent = orchestration_plan.get("parameters", {}).get("intent")
            log.info(f"[INTENT LOCK DEBUG] plan_intent={plan_intent}, checking if != {intent}")
            if plan_intent and plan_intent != intent:
                log.warning(f"[INTENT LOCK] LLM tried to change locked intent '{intent}' to '{plan_intent}'. Overriding back.")
                orchestration_plan["parameters"]["intent"] = intent
    except Exception as e:
        log.exception(f"[PIPELINE ERROR] Orchestrator failed: {e}")
        return None

    action_type = orchestration_plan.get("action")
    if action_type == "CONVERSE":
        return None
    if action_type == "tool_call":
        try:
            result = await _execute_tool_action(
                orchestration_plan, query, user_creds, model
            )
            return [result] if result else None
        except Exception as e:
            log.exception(f"[PIPELINE ERROR] Tool Execution failed: {e}")
            return [
                {
                    "status": "FAILURE",
                    "message": f"Tool execution crashed: {e}",
                    "service": "unknown",
                }
            ]
    return None


async def try_handle_compound_command(
    query, user_creds, model, intent: Optional[str] = None, 
    score: float = 0.0, is_high_confidence: bool = False, intent_locked: bool = False
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
                # For compound commands, don't pass intent (each sub-command needs its own)
                tasks.append(_handle_single_command(c, user_creds, model))
    else:
        # For single command, pass the intent to avoid re-classification
        tasks.append(_handle_single_command(query, user_creds, model, intent, score, is_high_confidence, intent_locked))
    if not tasks:
        return None
    results = await asyncio.gather(*tasks)
    log.info(f"[COMPOUND] asyncio.gather returned: {results}")
    valid_results = []
    for r_list in results:
        log.info(f"[COMPOUND] Processing r_list: {r_list}, type: {type(r_list)}")
        if isinstance(r_list, list):
            # Handle nested lists (flatten them)
            for item in r_list:
                if isinstance(item, list):
                    valid_results.extend([r for r in item if isinstance(r, dict)])
                elif isinstance(item, dict):
                    valid_results.append(item)
        elif isinstance(r_list, dict):
            valid_results.append(r_list)
    log.info(f"[COMPOUND] valid_results: {valid_results}")
    if not valid_results:
        log.info("[COMPOUND] Returning None - no valid results")
        return None
    log.info(f"[COMPOUND] Returning: {valid_results}")
    print(f"[DEBUG] try_handle_compound_command returning: {valid_results}")
    return valid_results


async def generate_rag_stream(
    query, user, model, use_openai, format_type, include_tool_results: bool = False
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

    refined, intent, score, is_high_confidence, intent_locked = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    creds = get_user_creds(user)
    # Intent already obtained from contextualize_query, no need to re-classify
    t_action = time.time()
    action_results = await try_handle_compound_command(refined, creds, model, intent, score, is_high_confidence, intent_locked)

    # Handle failed no-search intents directly (don't call LLM)
    # NOTE: We only skip LLM for core ACTION intents that failed to resolve.
    # Informational intents (list, read) should ALWAYS fall through to LLM/RAG if tool execution was skipped.
    no_search_intents = [
        "turn_on", "turn_off", "toggle", "play_media", "stop_media",
        "media_next", "media_previous", "open_app", "volume_up",
        "volume_down", "volume_set", "volume_mute", "timer_add",
        "alarm_add", "timer_delete", "timer_pause", "timer_resume",
        "calendar_add", "calendar_delete", "calendar_update",
        "dim", "brighten", "set_brightness", "set_color",
        "note_add", "note_append", "note_delete"
    ]
    if action_results is None and intent in no_search_intents:
        # All actions failed for a no-search intent, return a generic failure message
        log.info(f"[NO LLM] All actions failed for no-search intent '{intent}', returning direct failure")
        yield f"I couldn't perform that action. The device may not support this feature or there might be a connection issue."

    # --- EMIT TOOL RESULTS FOR TESTS/CLIENT ---
    if action_results and include_tool_results:
        yield builder.chunk(tool_results=action_results)

    action_context = ""
    run_knowledge_retrieval = True

    if action_results:
        # 1. Determine if the action was purely informational (Search, List, Read)
        is_informational_tool = False
        for res in action_results:
            svc = res.get("service", "")
            if svc in [
                "web_search",
                "calendar_list",
                "timer_list",
                "calendar_read",
                "note_read",
                "note_list",
                "music_search",
                "music_list",
            ]:
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
                new_state = res.get("new_state", None)
                friendly_name = res.get("friendly_name", res.get("entity_id", "N/A"))
                service = res.get("service", "N/A")
                state_msg = f" Verified New State: {new_state}" if new_state else ""
                action_context += f"- SUCCESS: Command '{service}' sent to {friendly_name}.{state_msg}\n"
                if service in [
                    "web_search",
                    "timer_list",
                    "calendar_list",
                    "calendar_read",
                    "note_read",
                    "note_list",
                ]:
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
        # AND if the intent is not a simple action that already succeeded
        already_searched = any(
            r.get("service") == "web_search" for r in (action_results or [])
        )
        
        # Define intents that should NOT trigger web search after success
        no_search_intents = [
            "turn_on", "turn_off", "toggle", "play_media", "stop_media",
            "media_next", "media_previous", "open_app", "volume_up",
            "volume_down", "volume_set", "volume_mute", "timer_add",
            "alarm_add", "timer_delete", "timer_pause", "timer_resume",
            "calendar_add", "calendar_delete", "calendar_update",
            "dim", "brighten", "set_brightness", "set_color",
            "note_add", "note_append", "note_delete",
            "timer_list", "calendar_list", "calendar_read", "note_list", "note_read", 
            "music_list", "list_playlists", "list_radio"
        ]
        
        # Skip web search if:
        # 1. Already searched in action results
        # 2. OR action succeeded and intent is a simple command
        # 3. OR action was executed (even if failed) for transport/media commands
        action_succeeded = action_results and all(r.get("status") == "SUCCESS" for r in action_results)
        is_no_search_intent = intent in no_search_intents

        # For transport commands, consider them executed even if they fail (e.g., no active devices)
        transport_executed = action_results and any(
            "media_stop" in str(r.get("service", "")) or
            "media_play" in str(r.get("service", "")) or
            "media_pause" in str(r.get("service", "")) or
            r.get("service") in ["stop_media", "media_play", "media_pause"]
            for r in action_results
        )

        log.debug(f"[SEARCH DECISION] intent={intent}, action_succeeded={action_succeeded}, is_no_search_intent={is_no_search_intent}, transport_executed={transport_executed}")
        should_search = not already_searched and not is_no_search_intent and not transport_executed
        log.debug(f"[SEARCH DECISION] should_search={should_search}")

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

    from app.settings import load_system_prompt

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
        "volume_up",
        "volume_down",
        "volume_set",
        "volume_mute",
        "open_app",
        "timer_add",
        "alarm_add",
        "timer_delete",
    ]
    use_simple = False
    if action_results and intent in simple_intents:
        log.info(
            f"DEBUG: Checking Simple Intent '{intent}' for silent mode. Results: {len(action_results)}"
        )
        if all(r.get("status") == "SUCCESS" for r in action_results) and not any(
            r.get("service") == "web_search" for r in action_results
        ):
            # Check if we should be silent (Physical actions only)
            silent_candidates = [
                "turn_on",
                "turn_off",
                "toggle",
                "play_media",
                "stop_media",
                "media_next",
                "media_previous",
                "open_app",
                "volume_up",
                "volume_down",
                "volume_set",
                "volume_mute",
                "timer_add",
                "timer_delete", 
                "timer_pause",
                "timer_resume",
                "alarm_add",
                "calendar_add",
                "calendar_delete",
                "calendar_update",
                "note_add",
                "note_delete",
                "note_append"
            ]
            if intent in silent_candidates:
                log.info(f"DEBUG: Entering Silent Mode for intent '{intent}'")
                # Yield Silent Token and return
                yield builder.chunk(content=SILENT_SUCCESS_TOKEN)
                yield builder.chunk(finish_reason="stop")
                yield builder.done()
                return

            use_simple = True

    # Retrieve history for the final prompts
    from .utils import get_history_context
    history_text = get_history_context(user) or ""

    template_to_use = SIMPLE_RAG_TEMPLATE if use_simple else RAG_TEMPLATE
    
    # Format based on which template is used (SIMPLE doesn't use history/context)
    if use_simple:
         prompt = template_to_use.format(
            sys_info=sys_info,
            query=refined,
            action_context=action_context
        )
    else:
        prompt = template_to_use.format(
            system_prompt=base_sys_prompt,
            sys_info=sys_info,
            ha_ctx=ha_ctx,
            nc_ctx=nc_ctx,
            search_ctx=search_ctx,
            cal_ctx=cal_ctx,
            history=history_text,
            query=refined,
            action_context=action_context,
        )
        # Append action context for non-simple templates if available
        if action_context:
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
    log.debug(
        f"[RESPONSE] Final response to user ({len(full_text)} chars): {full_text[:300]}"
    )
    yield builder.chunk(finish_reason="stop")
    yield builder.done()
