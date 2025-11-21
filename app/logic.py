# app/logic.py
import json
import time
import re
import requests
import caldav
import traceback
import asyncio
from datetime import datetime, timedelta
from functools import partial
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

# Import from settings
from settings import (
    log, run_blocking, get_user_creds, ha_cache_get, ha_cache_set,
    OLLAMA_URL, HA_URL, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    WHOOGLE_URL, OPENAI_MODEL, DEFAULT_MODEL, OLLAMA_TIMEOUT, OLLAMA_RETRY,
    GlobalResources, openai_client, EMB_MODEL, CHROMA_DIR,
    MAX_HISTORY_TURNS
)

# Optional OpenAI
try:
    import openai
    if openai_client: openai.api_key = openai_client.api_key
except: openai = None

# ------------------
# In-Memory History
# ------------------
CHAT_HISTORY = {}

def update_history(user: str, role: str, content: str):
    if not content: return
    if user not in CHAT_HISTORY:
        CHAT_HISTORY[user] = []
    CHAT_HISTORY[user].append({"role": role, "content": content})
    if len(CHAT_HISTORY[user]) > MAX_HISTORY_TURNS * 2:
        CHAT_HISTORY[user] = CHAT_HISTORY[user][-(MAX_HISTORY_TURNS * 2):]

def get_history_context(user: str) -> str:
    if user not in CHAT_HISTORY: return ""
    formatted = []
    for msg in CHAT_HISTORY[user]:
        role = "USER" if msg["role"] == "user" else "ASSISTANT"
        formatted.append(f"{role}: {msg['content']}")
    return "\n".join(formatted)

# ------------------
# LLM Helpers
# ------------------
async def call_ollama_generate(prompt: str, model: str = DEFAULT_MODEL, stream: bool = False):
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream}
    
    for attempt in range(max(1, OLLAMA_RETRY)):
        try:
            def _post(): return requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT, stream=stream)
            resp = await run_blocking(_post)
            resp.raise_for_status()
            r = resp
            break
        except Exception as e:
            log.warning(f"Ollama attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.2)
    else:
        raise HTTPException(status_code=502, detail="Ollama unavailable")

    if stream:
        async def async_iter():
            for chunk in r.iter_lines(decode_unicode=True):
                if chunk:
                    try:
                        obj = json.loads(chunk)
                        yield obj
                        if obj.get("done"): break
                    except: yield chunk
                    await asyncio.sleep(0) 
        return {"iterable": async_iter}

    try:
        data = r.json()
        return {"text": data.get("text") or data.get("response") or ""}
    except:
        return {"text": r.text}

async def call_openai_chat(messages, model=OPENAI_MODEL, stream=False):
    if not openai_client: raise HTTPException(501, detail="OpenAI not configured")
    try:
        if stream:
            async def async_iter():
                # OpenAI synchronous client wrapper
                def _create(): return openai_client.ChatCompletion.create(model=model, messages=messages, stream=True)
                response = await run_blocking(_create)
                for chunk in response:
                    yield chunk
                    await asyncio.sleep(0)
            return {"iterable": async_iter}
        else:
            # Using run_blocking but keeping verbose exception handling
            def _create(): return openai_client.ChatCompletion.create(model=model, messages=messages)
            resp = await run_blocking(_create)
            return {"text": resp.choices[0].message.content}
    except Exception as e:
        log.exception("OpenAI Error")
        raise HTTPException(status_code=502, detail=str(e))

# ------------------
# Tools
# ------------------
async def tool_web_search(query: str) -> str:
    if not WHOOGLE_URL: return "[System: Web Search Unavailable]"
    try:
        def do_search():
            return requests.get(f"{WHOOGLE_URL.rstrip('/')}/search", params={"q": query}, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        r = await run_blocking(do_search)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for res in soup.select(".result")[:3]:
            title = res.select_one("h3")
            body = res.select_one(".content") or res.select_one(".snippet")
            if title:
                results.append(f"Title: {title.get_text(strip=True)}\nSummary: {body.get_text(strip=True) if body else ''}")
        return "\n\n".join(results) if results else "No web results."
    except Exception as e: return f"Search Error: {e}"

async def tool_calendar(date_range="today") -> str:
    if not NEXTCLOUD_URL: return "[Calendar Unavailable]"
    def _fetch():
        try:
            client = caldav.DAVClient(url=f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav", username=NEXTCLOUD_USER, password=NEXTCLOUD_PASS)
            principal = client.principal()
            calendars = principal.calendars()
            events_out = []
            now = datetime.now()
            start = now
            end = now + timedelta(days=1 if "today" in date_range else 7)
            
            for cal in calendars:
                for ev in cal.date_search(start=start, end=end, expand=True):
                    data = ev.data
                    if "SUMMARY:" in data:
                        summary = data.split("SUMMARY:")[1].split("\n")[0].strip()
                        events_out.append(f"- {summary} ({cal.name})")
            return "\n".join(events_out)
        except Exception as e: return f"Cal Error: {e}"
    res = await run_blocking(_fetch)
    return f"Calendar:\n{res}" if res else "No events."

async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None):
    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    payload = {"entity_id": entity_id}
    if service_data: payload.update(service_data)
    
    try:
        def _post():
            return requests.post(url, json=payload, headers=headers, timeout=5.0)
        resp = await run_blocking(_post)
        
        if resp.status_code >= 400:
            log.error(f"HA Service Error ({resp.status_code}): {resp.text}")
            return f"Home Assistant failed: {resp.text}"
        
        resp.raise_for_status()
        return f"Successfully executed {domain}.{service} on {entity_id}."
    except Exception as e:
        log.error(f"Action failed: {e}")
        return f"Failed to execute command: {e}"

# ------------------
# Intent Logic
# ------------------
def is_system_task(query: str) -> bool:
    q = query.strip()
    return q.startswith("### Task:") or ("Generate" in q and "tags" in q) or ("Suggest" in q and "follow-up" in q)

async def contextualize_query(query, user, model):
    """
    Rewrites the query based on history.
    Strictly enforces natural language to prevent /slash_command hallucinations.
    """
    hist = get_history_context(user)
    if not hist: return query
    
    prompt = f"""Rewrite the User Input to be a standalone natural language command based on Chat History.
Do not output code, JSON, or slash commands. Just plain English.
History:
{hist}
User Input: {query}
Standalone Input:"""

    try:
        r = await call_ollama_generate(prompt, model)
        clean = r["text"].strip().strip('"')
        
        # FIX: Aggressively strip slash command artifacts and underscores
        if "/" in clean or "_" in clean:
            clean = clean.replace("/", "").replace("_", " ").replace("-", " ")
            
        log.debug(f"Contextualized: '{query}' -> '{clean}'")
        return clean
    except: return query

async def decompose_command_query(query, model):
    prompt = f"Split this request into a JSON list of single commands. Input: '{query}' Output (JSON):"
    try:
        r = await call_ollama_generate(prompt, model)
        txt = r["text"].strip()
        if "```" in txt: txt = txt.split("```json")[1].split("```")[0] if "json" in txt else txt.split("```")[1]
        return json.loads(txt)
    except: return [query]

async def classify_informational_intent(query):
    q = query.lower()
    if "schedule" in q or "calendar" in q: return "calendar"
    if "search" in q or "news" in q or "who is" in q: 
        if "turn" not in q and "play" not in q: return "web"
    return "general"

async def _handle_single_command(query, user_creds):
    q = query.lower().strip()
    service, service_data = None, None
    
    # Map verbs
    if "turn on" in q or "switch on" in q or "dim" in q: service = "turn_on"
    elif "turn off" in q or "switch off" in q: service = "turn_off"
    elif "toggle" in q: service = "toggle"
    elif "lock" in q: service = "lock"
    elif "unlock" in q: service = "unlock"
    elif "open" in q: service = "open_cover"
    elif "close" in q: service = "close_cover"
    
    # Music (MA Logic)
    elif "play" in q:
        service = "play_media"
        try:
            clean = q.replace("please","").replace("music","").replace("song","")
            # Improved parsing: "play X on Y"
            if " on " in clean: 
                parts = clean.split("play ", 1)[1].split(" on ")
                content = parts[0].strip()
                # We don't extract the device name here, we let vector search find it from the full query
            else: 
                content = clean.split("play ", 1)[1].strip()
            
            content = content.strip('"')
            if content: service_data = {"media_id": content, "enqueue": "play", "radio_mode": True}
        except: pass
        
    elif "pause" in q: service = "media_pause"
    elif "resume" in q: service = "media_play"
    elif "stop" in q: service = "media_stop"
    elif "next" in q: service = "media_next_track"

    if not service or not GlobalResources.ha_collection: return None

    # Vector Search
    try:
        # Search for the device name in the query
        docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(query, k=1))
        if not docs: return None
        eid = docs[0].metadata.get("entity_id")
        if not eid: return None
        
        domain = eid.split(".")[0]
        target_dom, target_svc = domain, service
        
        # Domain Fixes
        if service in ["turn_on", "turn_off", "toggle"]: target_dom = "homeassistant"
        
        # Lock Fix
        if domain == "lock" and "turn" in service: target_svc = "lock" if "on" in service else "unlock"; target_dom = "lock"
        
        # Cover Fix
        if "cover" in service: target_dom = "cover" if domain == "cover" else "homeassistant"; target_svc = "turn_on" if "open" in service else "turn_off"
        
        # Media Fixes
        if domain == "media_player":
            target_dom = "media_player"
            if service == "turn_on": target_svc = "turn_on" # Actually Power On
            if service == "turn_off": target_svc = "turn_off" # Actually Power Off
            
            # Music Assistant Routing
            if service == "play_media" and service_data:
                target_dom = "music_assistant"
                target_svc = "play_media"

        return await execute_ha_service(target_dom, target_svc, eid, user_creds, service_data)
    except Exception as e:
        log.warning(f"Cmd error: {e}")
        return None

async def try_handle_compound_command(query, user_creds, model):
    cmds = await decompose_command_query(query, model)
    res = []
    for c in cmds:
        if isinstance(c, str) and c.strip():
            r = await _handle_single_command(c, user_creds)
            if r: res.append(r)
    return "\n".join(res) if res else None

# --- Context Fetchers ---
async def get_ha_context(user, query=None):
    creds = get_user_creds(user)
    if not query:
        c = await ha_cache_get(creds["user"])
        if c: return c
    if not HA_URL: return ""
    
    t_ids = {"sensor.time_date", "sun.sun"}
    if GlobalResources.ha_collection and query:
        try:
            docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(query, k=10))
            for d in docs: t_ids.add(d.metadata.get("entity_id"))
        except: pass

    try:
        def _get(): return requests.get(f"{HA_URL.rstrip('/')}/api/states", headers={"Authorization": f"Bearer {creds['ha_token']}"}, timeout=4)
        r = await run_blocking(_get)
        r.raise_for_status()
        lines = []
        for s in r.json():
            if s["state"] in ["unavailable", "unknown"]: continue
            eid = s["entity_id"]
            if eid in t_ids or eid.split(".")[0] in ["person", "weather", "calendar"] or (not query and len(lines) < 1000):
                fname = s.get("attributes", {}).get("friendly_name", "")
                lines.append(f"{eid}: {s['state']} ({fname})")
        ctx = "Home Assistant Status:\n" + "\n".join(lines)
        if not query: await ha_cache_set(creds["user"], ctx)
        return ctx
    except Exception as e: return f"HA Error: {e}"

async def get_rag_context(query):
    if not GlobalResources.nextcloud_collection: return ""
    try:
        docs = await run_blocking(lambda: GlobalResources.nextcloud_collection.similarity_search_with_score(query, k=4))
        return "Nextcloud Docs:\n" + "\n\n".join([f"[Source: {d.metadata.get('path')}]\n{d.page_content}" for d,s in docs])
    except: return ""

# --- Main Stream Logic ---
async def stream_rag_result(query, user, model, use_openai, format_type):
    # 1. System Task Bypass
    if is_system_task(query):
        if use_openai and openai_client:
            resp = await call_openai_chat([{"role":"user", "content": query}], model=OPENAI_MODEL, stream=True)
            async def oa_task():
                yield f"data: {json.dumps({'id': f'chat-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]})}\n\n"
                async for c in resp["iterable"]():
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': c.choices[0].delta.get('content','')}}]})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(oa_task(), media_type="text/event-stream")
        else:
            r = await call_ollama_generate(query, model, stream=True)
            if "iterable" in r:
                async def ol_task():
                    if format_type == "openai": yield f"data: {json.dumps({'id': f'chat-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]})}\n\n"
                    async for c in r["iterable"]():
                        txt = c.get("response","") if isinstance(c,dict) else str(c)
                        if format_type == "openai":
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': txt}}]})}\n\n"
                            if isinstance(c, dict) and c.get("done"): yield "data: [DONE]\n\n"
                        else: yield json.dumps(c) + "\n" if format_type == "chat" else json.dumps({"response":txt})+"\n"
                return StreamingResponse(ol_task(), media_type="application/x-ndjson")

    refined = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    
    # 2. Intents
    creds = get_user_creds(user)
    cmd_res = await try_handle_compound_command(refined, creds, model)
    if cmd_res:
        update_history(user, "assistant", cmd_res)
        async def act_fmt():
            if format_type == "openai":
                yield f"data: {json.dumps({'id': f'chat-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]})}\n\n"
                yield f"data: {json.dumps({'choices': [{'delta': {'content': cmd_res}}]})}\n\n"
                yield f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"
            elif format_type == "chat":
                yield json.dumps({"model": model, "message": {"role": "assistant", "content": cmd_res}, "done": True}) + "\n"
            else:
                yield json.dumps({"response": cmd_res, "done": True}) + "\n"
        return StreamingResponse(act_fmt(), media_type="text/event-stream" if format_type == "openai" else "application/x-ndjson")

    # 3. Gather Context
    ha_ctx = await get_ha_context(user, query=refined)
    nc_ctx = await get_rag_context(refined)
    intent = await classify_informational_intent(refined)
    ext = ""
    if intent == "web": ext = await tool_web_search(refined)
    elif intent == "calendar": ext = await tool_calendar()

    combined = "\n\n".join([c for c in (ha_ctx, nc_ctx, ext) if c])
    hist = get_history_context(user)
    prompt = f"System: You are a helpful assistant.\nHistory:\n{hist}\nContext:\n{combined}\n\nUser: {refined}\nAnswer:"
    
    # 4. Stream Reply
    full_reply = ""
    if use_openai and openai_client:
        resp = await call_openai_chat([{"role":"user", "content":prompt}], model=OPENAI_MODEL, stream=True)
        async def oa_gen():
            nonlocal full_reply
            yield f"data: {json.dumps({'id': f'chat-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]})}\n\n"
            async for c in resp["iterable"]():
                txt = c.choices[0].delta.get("content", "") if hasattr(c,"choices") else ""
                full_reply += txt
                yield f"data: {json.dumps({'choices': [{'delta': {'content': txt}}]})}\n\n"
            update_history(user, "assistant", full_reply)
            yield "data: [DONE]\n\n"
        return StreamingResponse(oa_gen(), media_type="text/event-stream")

    r = await call_ollama_generate(prompt, model, stream=True)
    if "iterable" in r:
        async def ol_gen():
            nonlocal full_reply
            if format_type == "openai": yield f"data: {json.dumps({'id': f'chat-{int(time.time())}', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]})}\n\n"
            async for c in r["iterable"]():
                if isinstance(c, dict):
                    token = c.get("response", "")
                    full_reply += token
                    if format_type == "openai":
                        yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': token}}]})}\n\n"
                        if c.get("done"): yield "data: [DONE]\n\n"
                    elif format_type == "chat":
                         if "message" not in c: yield json.dumps({"model": model, "message": {"role": "assistant", "content": token}, "done": c.get("done", False)}) + "\n"
                         else: yield json.dumps(c) + "\n"
                    else: yield json.dumps(c) + "\n"
                else: yield str(c) + "\n"
            update_history(user, "assistant", full_reply)
        return StreamingResponse(ol_gen(), media_type="text/event-stream" if format_type == "openai" else "application/x-ndjson")
    return JSONResponse({"error": "Stream failed"})
