# app/logic/pipeline.py
import json
import time
import re
import asyncio
from datetime import datetime
# FIX: Explicitly import necessary type hints for the execution layer
from typing import AsyncGenerator, List, Union, Dict, Optional, Any 

# Import Settings
from settings import (
    log, run_blocking, get_user_creds, GlobalResources, 
    DEFAULT_MODEL, load_system_prompt,
    CONTEXT_REWRITE_PROMPT, RAG_TEMPLATE, ORCHESTRATOR_PROMPT, 
    SIMPLE_RAG_TEMPLATE, # Added: New template for concise responses
    ACTION_TOOL_CONFIDENCE_THRESHOLD, INFORMATIONAL_INTENTS 
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
# --- Import Timer Ops (NEW) ---
from .timer_ops import (
    tool_timer_add, tool_timer_list, tool_timer_delete,
    tool_timer_pause, tool_timer_resume
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
            verb_match = re.match(r"^(turn on|turn off|toggle|play|stop|schedule|list|open|launch|scroll|move|set|start)\b", p.lower())
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
    # NOTE: Updated to match new intent_engine.classify return signature
    intent, score, is_high_confidence = await intent_engine.classify(query, high_confidence_threshold=ACTION_TOOL_CONFIDENCE_THRESHOLD)
    
    stateless_intents = [
        "turn_on", "turn_off", "toggle", "play_media", "stop_media",
        "calendar_add", "calendar_delete", "calendar_list", "calendar_update",
        "timer_add", "timer_delete", "timer_list", "timer_pause", "timer_resume",
        "time_query", "intent_learn", "open_app", "media_next", "media_previous"
    ]
    
    # Use the new is_high_confidence flag
    if is_high_confidence and intent in stateless_intents:
        return query

    verbs = ["turn", "play", "stop", "toggle", "schedule", "add", "delete", "remove", "cancel", "remind", "list", "learn", "teach", "map", "set", "start"]
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

async def _attempt_fast_ha_command(query: str, user_creds: Dict[str, str], ha_collection) -> Optional[Dict[str, Union[str, bool]]]:
    """
    Attempts to execute a Home Assistant command directly by string matching
    a verb and performing a vector search for the device name.
    Returns the structured result if successful, or None.
    """
    q_low = query.lower().strip()

    # 1. Simple verb check (Only proceed if it looks like a command)
    if not any(x in q_low for x in ["turn on", "turn off", "toggle", "open", "close"]):
        return None
    
    # CRITICAL: Prevent "Search the web" from falling into HA Control
    if any(x in q_low for x in ["search", "find", "who", "what", "when", "where", "how", "explain"]):
        return None

    # 2. Clean query to isolate entity name
    clean_q = q_low
    for phrase in ["turn on", "turn off", "toggle", "play", "stop", "the", "please", " on ", "open", "close"]:
        clean_q = clean_q.replace(phrase, " ")
    clean_q = clean_q.strip()

    if not clean_q:
        return None
    
    eid = None
    # 3. Vector Search for entity match (Enhanced Domain Prioritization)
    if ha_collection:
        # Search top 5 results to find a controllable entity
        docs = await run_blocking(lambda: safe_similarity_search(ha_collection, clean_q, k=5))
        
        # New Domain Priority Scoring: Higher score = more likely to be the control entity
        domain_priority = {
            "cover": 5,   # Garage doors/blinds
            "switch": 4, 
            "light": 3, 
            "media_player": 3,
            "remote": 2,
            "automation": 1,
            "script": 1,
            "camera": 0 # Explicitly zero out low-priority/non-controllable domains
        }
        
        best_eid = None
        best_score = 0
        
        # Filter: Find the highest scoring, relevant entity
        for d in docs:
            potential_eid = d.metadata.get("entity_id")
            if potential_eid:
                domain = potential_eid.split(".")[0]
                priority = domain_priority.get(domain, 0)
                
                # Check for explicit automation/script command
                is_explicit_secondary = domain in ["automation", "script"] and domain in q_low
                
                # Logic 1: Immediate execution if Automation/Script is explicitly named (e.g., "disable garage automation")
                if is_explicit_secondary:
                    eid = potential_eid
                    log.info(f"FAST HA PATH: Executing explicit secondary control: {eid}")
                    break
                        
                # Logic 2: For general actions (turn_on/open), prioritize functional devices.
                # Only check for core control domains (score > 2)
                if priority > best_score:
                    best_score = priority
                    best_eid = potential_eid
        
        # If we broke the loop due to explicit secondary action, use that eid. Otherwise, use the best scored one.
        if eid:
            pass # Use the explicitly selected eid
        else:
            eid = best_eid # Use the best device found by priority scoring
        
        if not eid:
            return None # No suitable controllable entity found

    if not eid: return None

    # 4. Determine service based on original query
    service = None
    if "turn off" in q_low or "close" in q_low: service = "turn_off"
    elif "turn on" in q_low or "open" in q_low: service = "turn_on"
    elif "toggle" in q_low: service = "toggle"
    
    if not service:
        log.warning(f"FAST HA PATH: Entity {eid} found, but could not determine service from query: {q_low}")
        return None

    domain = eid.split(".")[0]
    target_dom = "homeassistant" if service in ["turn_on", "turn_off", "toggle"] else domain
    
    log.info(f"FAST HA PATH: Executing service {service} on {eid} due to string match bypass.")
    
    # CRITICAL FIX: execute_ha_service now returns a dictionary, so this function should just return it.
    ha_result_dict = await execute_ha_service(target_dom, service, eid, user_creds, {}, GlobalResources.redis_client)
    
    # Since execute_ha_service is updated to return a dict, we ensure all necessary keys are present
    # in case of an unexpected return type (though this is primarily for safety).
    if isinstance(ha_result_dict, dict):
        return ha_result_dict
    
    # Fallback for unexpected non-dict return (should not happen with updated media_ops.py)
    log.error(f"FAST HA PATH: execute_ha_service returned unexpected type: {type(ha_result_dict)}")
    return {
        "status": "FAILURE", 
        "message": f"Execution failed internally or returned non-dict type: {ha_result_dict}",
        "service": f"{target_dom}.{service}",
        "entity_id": eid,
        "friendly_name": eid.split(".")[-1].replace("_", " ").title(),
        "new_state": "N/A"
    }


async def _llm_orchestrator(query: str, intent: str, score: float, model: str) -> Dict[str, Any]:
    """
    Forces the LLM to generate a structured JSON action plan (tool_call or CONVERSE).
    Includes a self-correction loop for invalid JSON (Hallucination Mitigation).
    """
    orchestrator_prompt = ORCHESTRATOR_PROMPT.format(
        query=query,
        intent_name=intent,
        intent_score=score
    )
    
    last_error = ""
    for attempt in range(2):
        if attempt > 0:
            # Self-Correction Loop
            log.warning("LLM Orchestrator failed JSON validation. Attempting self-correction.")
            # Inject error message into the prompt to guide correction
            correction_prompt = f"CRITICAL ERROR: Your previous JSON output failed validation: '{last_error}'. You must output ONLY a single, valid JSON object (DO NOT use markdown backticks). Review your plan and try again. User Query: {query} Best Vector Intent: {intent}."
            
            # Send the base prompt with the correction instruction
            r = await call_ollama_generate(correction_prompt + "\n" + ORCHESTRATOR_PROMPT.format(query=query, intent_name=intent, intent_score=score), model)
        else:
            r = await call_ollama_generate(orchestrator_prompt, model)
            
        # Use is_voice=False to preserve JSON structure for parsing
        text = clean_llm_output(r.get("text", ""), is_voice=False).strip()
        
        try:
            # Clean up potential markdown wrapping
            text = text.strip().strip('`').strip()
            if text.startswith('json\n'):
                text = text[5:].strip()
            
            # Find and parse the JSON block
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                parsed_json = json.loads(match.group(0))
                
                # Validation: Ensure core keys are present
                if "action" not in parsed_json:
                     raise ValueError("Missing 'action' key in JSON.")
                
                return parsed_json
            else:
                raise json.JSONDecodeError("No JSON object found in output.", text, 0)
            
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            log.error(f"Orchestrator JSON Error: {last_error}")
            continue # Retry loop

    # Final Failure: Default to conversation to prevent pipeline crash
    log.critical("LLM Orchestrator failed self-correction. Defaulting to CONVERSE.")
    return {"action": "CONVERSE", "error": "Orchestrator failed to generate valid plan."}


async def _execute_tool_action(action_plan: Dict[str, Any], query: str, user_creds: Dict[str, str], model: str) -> Optional[Dict[str, Union[str, bool]]]:
    """
    Executes a structured action plan generated by the LLM Orchestrator.
    Returns structured result or None.
    """
    tool_name = action_plan.get("tool_name")
    params = action_plan.get("parameters", {})
    
    # --- CALENDAR TOOLS ---
    if tool_name == "calendar_add":
        # Calendar tools return simple strings on success/failure, wrap them.
        res = await tool_calendar_add(query, user_creds, model, GlobalResources.redis_client)
        return {"status": "SUCCESS" if "Scheduled" in res.get("message", "") else "FAILURE", "message": res.get("message", "Calendar operation failed."), "service": "calendar_add"}
    
    elif tool_name == "calendar_list":
        res = await tool_calendar_list(user_creds, GlobalResources.redis_client)
        return {"status": "SUCCESS", "message": res.get("message", "Calendar list failed."), "service": "calendar_list"}

    elif tool_name == "calendar_delete":
        # FIX: Added Missing Handler
        return await tool_calendar_delete(query, user_creds, model, GlobalResources.redis_client)
        
    elif tool_name == "calendar_update":
        # FIX: Added Missing Handler
        return await tool_calendar_update(query, user_creds, model, GlobalResources.redis_client)

    # --- TIMER / ALARM TOOLS ---
    elif tool_name == "timer_add":
        return await tool_timer_add(query, user_creds, model, GlobalResources.redis_client)
    
    elif tool_name == "timer_list":
        return await tool_timer_list(user_creds)

    elif tool_name == "timer_delete":
        return await tool_timer_delete(query, user_creds)
    
    elif tool_name == "timer_pause":
        return await tool_timer_pause(query)

    elif tool_name == "timer_resume":
        return await tool_timer_resume(query)

    # --- MEDIA/HA COMMANDS ---
    elif tool_name == "media_command":
        # NOTE: We rely on handle_media_command for smart resolution (entity_id=None)
        # and it already returns the structured Dict for verification.
        intent = params.get("intent", "turn_on")
        # CRITICAL FIX: handle_media_command returns a string on failure, which causes the crash.
        # It should return a structured dict. We'll handle its string return here for now,
        # but media_ops.py should be updated to return dicts.
        return await handle_media_command(
            intent, 
            query, 
            None, # Let handle_media_command resolve entity
            user_creds, 
            GlobalResources.ha_collection, 
            GlobalResources.redis_client
        )
    
    # --- Other Tools (e.g., learn) ---
    elif tool_name == "intent_learn":
        res = f"Cannot learn '{params.get('phrase')}' with current prompt context." # Logic handled better by external endpoint
        return {"status": "FAILURE", "message": res, "service": "intent_learn"}
    
    # --- Web Search ---
    elif tool_name == "web_search":
        res = await tool_web_search(query)
        return {"status": "SUCCESS", "message": res, "service": "web_search"}
    
    # --- Fallback for unhandled/internal tools ---
    return {"status": "FAILURE", "message": f"Action requested unhandled tool: {tool_name}", "service": tool_name}


async def _handle_single_command(query: Union[str, Dict], user_creds: Dict[str, str], model: str = None) -> Optional[List[Dict[str, Any]]]:
    """
    Routes a single command. Returns a list of structured action results or None to trigger RAG.
    """
    if isinstance(query, dict):
        query = str(query.get("response", query.get("text", str(query))))
    if not isinstance(query, str): query = str(query)
    
    if len(query) > 150:
        log.warning(f"Ignoring overly long command ({len(query)} chars): {query[:50]}...")
        return None

    q_low = query.lower().strip()
    
    # --- STAGE 2A: FAST PATH CHECK (The Speed Lane) ---
    action_result = await _attempt_fast_ha_command(
        query, 
        user_creds, 
        GlobalResources.ha_collection
    )
    if action_result:
        log.debug("FAST PATH executed, returning action result.")
        # Wrap single result in a list for compatibility with compound handler
        # _attempt_fast_ha_command now returns a dict, so wrap it.
        return [action_result] 

    # --- STAGE 2B: LLM ORCHESTRATION ---
    
    # 1. Vector Classification
    intent, score, is_high_confidence = await intent_engine.classify(query, high_confidence_threshold=ACTION_TOOL_CONFIDENCE_THRESHOLD)
    
    # 2. LLM Planning (Structured JSON output)
    orchestration_plan = await _llm_orchestrator(query, intent or "unknown", score, model)
    
    action_type = orchestration_plan.get("action")
    
    # 3. CONVERSE Decision (Balance Point)
    if action_type == "CONVERSE":
        log.info("LLM Orchestrator chose CONVERSE. Proceeding to RAG.")
        # Return None to trigger Stage 3 (RAG)
        return None 
    
    # 4. TOOL_CALL Decision (Execution)
    if action_type == "tool_call":
        log.info(f"LLM Orchestrator chose tool_call: {orchestration_plan.get('tool_name')}")
        # Execute tool and wrap result in a list
        result = await _execute_tool_action(orchestration_plan, query, user_creds, model)
        return [result] if result else None

    # 5. Fallback - Should not be reached if orchestration is robust
    return None 
    

async def try_handle_compound_command(query, user_creds, model) -> Optional[List[Dict[str, Any]]]:
    """
    Manages decomposition and execution of (potentially multiple) commands.
    Returns a list of structured results or None.
    """
    # META-PROMPT BYPASS (OpenWebUI)
    if "### Task:" in query or "JSON format" in query or "<chat_history>" in query:
        return None

    # Questions bypass tools
    if re.match(r"^(what|who|when|how|why)\b", query.lower().strip()): 
        if " and " not in query.lower():
             if "what time" in query.lower() or "current time" in query.lower():
                 now = datetime.now()
                 # Simple time query is handled and returned as a SUCCESS context for synthesis
                 return [{"status": "SUCCESS", "message": f"It is currently {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}."}]
             return None # Trigger RAG for informational question
    
    # DECOMPOSITION: Split "Turn on X and Y"
    cmds = await decompose_command_query(query, model)
    tasks = []
    
    # If decomposition resulted in multiple commands, we handle them as actions
    if len(cmds) > 1:
        for c in cmds:
            if isinstance(c, str) and len(c.strip()) > 2:
                # Run single command handler for each part (can return results or None)
                tasks.append(_handle_single_command(c, user_creds, model))
    else:
        # If single command, run the main handler
        tasks.append(_handle_single_command(query, user_creds, model))
            
    if not tasks: return None
    
    # Run parallel/sequential commands
    results = await asyncio.gather(*tasks)
    
    # Aggregate results for LLM Synthesis
    # Flatten the list of lists/results and filter out the None values (conversational path)
    valid_results = []
    for r_list in results:
        # Check if the result is a list (from compound commands) or a single dictionary (from single command)
        if isinstance(r_list, list):
            valid_results.extend([r for r in r_list if isinstance(r, dict)])
        # CRITICAL FIX: If a single command handler returns a dictionary, it needs to be processed.
        elif isinstance(r_list, dict): 
            valid_results.append(r_list)
        elif r_list is not None:
             # Should not happen with the fixes, but catch any non-None, non-list, non-dict
            log.error(f"try_handle_compound_command: Found unexpected type in results: {type(r_list)}")


    if not valid_results:
        # If we failed to handle any action, return None to trigger RAG
        return None
    
    return valid_results

async def generate_rag_stream(query, user, model, use_openai, format_type) -> AsyncGenerator[str, None]:
    """
    Main Pipeline Entry Point (Modified to handle structured results).
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
    
    # 2. Get Intent for Routing (Needed even if action fails)
    # We call classify here to help decide which RAG context to fetch later
    intent, score, _ = await intent_engine.classify(refined)

    # 3. Action Layer (Tools)
    t_action = time.time()
    # Now returns a list of structured results or None
    action_results = await try_handle_compound_command(refined, creds, model)
    log.debug(f"Action Layer took: {time.time() - t_action:.4f}s")
    
    action_context = ""
    run_knowledge_retrieval = True
    
    # Check if any action was performed (even if it failed)
    if action_results:
        # If any action was executed, we can skip the slower search and Nextcloud RAG
        run_knowledge_retrieval = False
        
        # Inject structured action result for LLM synthesis (Verification/Error Handoff)
        action_context = "### PREVIOUS ACTIONS (Use to inform your response. Do not hallucinate success/failure):\n"
        for res in action_results:
            # FIX: res is now guaranteed to be a dictionary or filtered out.
            status = res.get("status", "FAILURE")
            msg = res.get("message", "Unknown action.")
            
            if status == "SUCCESS":
                # Use verified state for honest confirmation
                new_state = res.get('new_state', 'N/A')
                friendly_name = res.get('friendly_name', 'N/A') # NEW
                service = res.get('service', 'N/A')             # NEW
                action_context += f"- SUCCESS: Command '{service}' sent to {friendly_name}. Verified New State: {new_state}\n"
            else:
                # Error Handoff: Force the LLM to acknowledge the failure
                entity = res.get("entity_id", "N/A")
                service = res.get("service", "N/A")
                action_context += f"- FAILURE: Command '{service}' on '{entity}' failed. Reason: {msg}\n"
        log.info(f"Action Context injected for synthesis: {action_context.strip()}")
        
    # 4. Smart Context Routing (The Fix)
    t_rag = time.time()
    ha_ctx, nc_ctx, search_ctx, cal_ctx = "", "", "", ""
    
    if run_knowledge_retrieval:
        # Defaults
        fetch_ha = True
        fetch_nc = True
        
        # --- ROUTING LOGIC ---
        # If intent suggests Documents/Knowledge, ignore Home Assistant Devices
        if intent == "content_query":
            fetch_ha = False
            log.info(f"Context Routing: Skipping HA search for content intent '{intent}'")
            
        # If intent suggests Controls/Media, ignore Nextcloud Invoices/Notes
        elif intent in ["turn_on", "turn_off", "toggle", "play_media", "stop_media", "open_app"]:
            fetch_nc = False
            log.info(f"Context Routing: Skipping Nextcloud search for control intent '{intent}'")
            
        # If intent is Calendar, we likely don't need generic RAG either
        elif intent and (intent.startswith("calendar") or intent.startswith("timer")):
            fetch_ha = False
            fetch_nc = False
            
        # Execute Fetches in Parallel based on flags
        tasks = []
        if fetch_ha: tasks.append(get_ha_context(user, refined))
        else: tasks.append(asyncio.sleep(0)) # No-op
            
        if fetch_nc: tasks.append(get_rag_context(refined))
        else: tasks.append(asyncio.sleep(0)) # No-op
            
        tasks.append(tool_web_search(refined))
        
        results = await asyncio.gather(*tasks)
        ha_ctx = results[0] if fetch_ha else ""
        nc_ctx = results[1] if fetch_nc else ""
        search_ctx = results[2]
        
        # Calendar Context Injection
        if any(x in refined.lower() for x in ["calendar", "schedule", "meeting", "today", "tomorrow"]):
            # Only read calendar if we aren't actively modifying it right now
            if not action_results:
                cal_ctx = await tool_calendar_read(creds, GlobalResources.redis_client)
            
    log.debug(f"Context Retrieval took: {time.time() - t_rag:.4f}s")

    from settings import load_system_prompt
    base_sys_prompt = load_system_prompt()
    
    now = datetime.now()
    sys_info = f"Current Date: {now.strftime('%A, %B %d, %Y')}\nCurrent Time: {now.strftime('%I:%M %p')}"
    
    # --- LOGIC BRANCH: CHOOSE TEMPLATE (NEW ADDITION) ---
    # Use simple template for successful commands to reduce verbosity
    simple_intents = ["turn_on", "turn_off", "toggle", "play_media", "stop_media", "media_next", "media_previous", "open_app", "timer_add", "timer_delete", "timer_list"]
    
    use_simple = False
    if action_results and intent in simple_intents:
        if all(r.get("status") == "SUCCESS" for r in action_results):
            use_simple = True

    if use_simple:
        template_to_use = SIMPLE_RAG_TEMPLATE
    else:
        template_to_use = RAG_TEMPLATE

    # Use Externalized Prompt Template from Settings
    prompt = template_to_use.format(
        system_prompt=base_sys_prompt,
        sys_info=sys_info,
        ha_ctx=ha_ctx,
        nc_ctx=nc_ctx,
        search_ctx=search_ctx,
        cal_ctx=cal_ctx,
        query=refined
    )
    
    # Append Action Context to the prompt (CRITICAL for hallucination mitigation)
    if action_context:
        prompt += f"\n{action_context}"
    
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
