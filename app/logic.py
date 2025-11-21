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
from typing import AsyncGenerator, Optional, Dict, Any

# Import from settings
from settings import (
    log, run_blocking, get_user_creds, ha_cache_get, ha_cache_set,
    OLLAMA_URL, HA_URL, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    WHOOGLE_URL, OPENAI_MODEL, DEFAULT_MODEL, OLLAMA_TIMEOUT, OLLAMA_RETRY,
    GlobalResources, openai_client, EMB_MODEL, CHROMA_DIR,
    MAX_HISTORY_TURNS
)

try:
    import openai
    if openai_client: openai.api_key = openai_client.api_key
except: openai = None

CHAT_HISTORY = {}

# --- Helper Class for Strict Protocol Compliance ---
class StreamResponseBuilder:
    def __init__(self, model: str, format_type: str):
        self.model = model
        self.format_type = format_type
        self.req_id = f"chatcmpl-{int(time.time())}"
        self.created = int(time.time())

    def chunk(self, content=None, role=None, finish_reason=None):
        """Generates a chunk formatted for the specific protocol"""
        # 1. OpenAI SSE Format
        if self.format_type == "openai":
            delta = {}
            if role: delta["role"] = role
            if content is not None: delta["content"] = content
            
            data = {
                "id": self.req_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [{
                    "index": 0,
                    "delta": delta,
                    "finish_reason": finish_reason
                }]
            }
            return f"data: {json.dumps(data)}\n\n"

        # 2. Ollama Native Format
        else:
            data = {
                "model": self.model,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "message": {"role": role or "assistant", "content": content or ""},
                "done": False
            }
            if finish_reason == "stop":
                data["done"] = True
            return json.dumps(data) + "\n"

    def done(self):
        """Signal end of stream"""
        if self.format_type == "openai":
            return "data: [DONE]\n\n"
        return ""

# ------------------
# Basic Helpers
# ------------------
def update_history(user: str, role: str, content: str):
    if not content: return
    if user not in CHAT_HISTORY: CHAT_HISTORY[user] = []
    CHAT_HISTORY[user].append({"role": role, "content": content})
    if len(CHAT_HISTORY[user]) > MAX_HISTORY_TURNS * 2:
        CHAT_HISTORY[user] = CHAT_HISTORY[user][-(MAX_HISTORY_TURNS * 2):]

def get_history_context(user: str) -> str:
    if user not in CHAT_HISTORY: return ""
    return "\n".join([f"{'USER' if m['role']=='user' else 'ASSISTANT'}: {m['content']}" for m in CHAT_HISTORY[user]])

# ------------------
# LLM & Tool Helpers
# ------------------
async def call_ollama_generate(prompt: str, model: str = DEFAULT_MODEL, stream: bool = False):
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream}
    
    for attempt in range(max(1, OLLAMA_RETRY)):
        try:
            def _post(): return requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT, stream=stream)
            resp = await run_blocking(_post)
            resp.raise_for_status()
            
            if stream:
                # Return an async iterator factory to prevent "generator not callable" error
                async def async_iter():
                    for chunk in resp.iter_lines(decode_unicode=True):
                        if chunk:
                            try:
                                yield json.loads(chunk)
                            except: pass
                        await asyncio.sleep(0)
                return {"iterable": async_iter}
            else:
                return {"text": resp.json().get("response", "")}
        except Exception as e:
            log.warning(f"Ollama attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.2)
    return {"error": "Ollama unavailable"}

async def call_openai_chat(messages, model=OPENAI_MODEL, stream=False):
    if not openai_client: return {"error": "OpenAI not configured"}
    try:
        if stream:
            async def async_iter():
                def _create(): return openai_client.ChatCompletion.create(model=model, messages=messages, stream=True)
                response = await run_blocking(_create)
                for chunk in response: yield chunk
            return {"iterable": async_iter}
        else:
            def _create(): return openai_client.ChatCompletion.create(model=model, messages=messages)
            return {"text": (await run_blocking(_create)).choices[0].message.content}
    except Exception as e: return {"error": str(e)}

# ------------------
# Tools
# ------------------
async def tool_web_search(query: str) -> str:
    if not WHOOGLE_URL: return ""
    try:
        def do_search():
            return requests.get(f"{WHOOGLE_URL.rstrip('/')}/search", params={"q": query}, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        r = await run_blocking(do_search)
        soup = BeautifulSoup(r.text, "html.parser")
        return "\n\n".join([f"Title: {res.select_one('h3').get_text(strip=True)}\nSummary: {res.select_one('.content').get_text(strip=True)}" for res in soup.select(".result")[:3] if res.select_one("h3")])
    except: return ""

async def tool_calendar(date_range="today") -> str:
    if not NEXTCLOUD_URL: return ""
    try:
        client = caldav.DAVClient(url=f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav", username=NEXTCLOUD_USER, password=NEXTCLOUD_PASS)
        events = []
        now = datetime.now()
        for cal in client.principal().calendars():
            for ev in cal.date_search(start=now, end=now+timedelta(days=7), expand=True):
                if "SUMMARY:" in ev.data: events.append(f"- {ev.data.split('SUMMARY:')[1].splitlines()[0]} ({cal.name})")
        return "\n".join(events)
    except: return ""

# ------------------
# Home Assistant Logic (FAIL FAST OPTIMIZATION)
# ------------------
async def get_entity_state(entity_id: str, user_creds: Dict[str, str]) -> Optional[str]:
    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    for _ in range(3):
        try:
            # Reduced timeout to 2s to prevent long hangs on DNS issues
            def _get(): return requests.get(url, headers=headers, timeout=2.0)
            r = await run_blocking(_get)
            if r.status_code == 200: return r.json().get("state")
        except: await asyncio.sleep(0.5)
    return None

async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None):
    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    payload = {"entity_id": entity_id, **(service_data or {})}
    last_err = None
    for _ in range(3):
        try:
            # Reduced timeout to 3s. Local HA should be instant.
            def _post(): return requests.post(url, json=payload, headers=headers, timeout=3.0)
            r = await run_blocking(_post)
            if r.status_code < 400: return f"Successfully executed {domain}.{service} on {entity_id}."
            last_err = r.text
        except Exception as e: last_err = str(e)
        await asyncio.sleep(1)
    return f"Failed: {last_err}"

# ------------------
# Intent & Execution
# ------------------
def is_system_task(query: str) -> bool:
    q = query.strip()
    return q.startswith("### Task:") or ("Generate" in q and "tags" in q) or ("Suggest" in q and "follow-up" in q)

async def contextualize_query(query, user, model):
    if not get_history_context(user): return query
    r = await call_ollama_generate(f"Rewrite input to standalone command. History:\n{get_history_context(user)}\nInput: {query}\nStandalone:", model)
    clean = r.get("text", "").strip().strip('"')
    return clean.replace("/", "").replace("_", " ") if "/" in clean or "_" in clean else clean

async def decompose_command_query(query, model):
    r = await call_ollama_generate(f"Request: '{query}'\nReturn JSON list of actions. No Markdown.", model)
    try:
        txt = r.get("text", "").split("```json")[-1].split("```")[0].strip()
        return json.loads(txt) if isinstance(json.loads(txt), list) else [query]
    except: return [query]

async def _smart_power_delegation(entity_id, desired_state, user_creds):
    if "_chrome" in entity_id or "_cast" in entity_id:
        base = entity_id.split("_chrome")[0].split("_cast")[0]
        if await get_entity_state(base, user_creds):
            return await execute_ha_service("media_player", "turn_on" if desired_state=="on" else "turn_off", base, user_creds)
    return None

async def _handle_single_command(query, user_creds):
    q = query.lower().strip()
    service, service_data = None, None
    
    if "turn on" in q: service = "turn_on"
    elif "turn off" in q: service = "turn_off"
    elif "toggle" in q: service = "toggle"
    elif "stop" in q: service = "media_stop"
    elif re.search(r"\bplay\b", q):
        service = "play_media"
        try:
            content = q.split("play ", 1)[1].split(" on ")[0].strip().strip('"')
            if content: service_data = {"media_id": content, "enqueue": "play", "radio_mode": True}
        except: pass

    if not service or not GlobalResources.ha_collection: return None

    docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(query, k=1))
    if not docs: return None
    eid = docs[0].metadata.get("entity_id")
    
    domain = eid.split(".")[0]
    target_dom, target_svc = domain, service
    if service in ["turn_on", "turn_off", "toggle"]: target_dom = "homeassistant"
    
    if domain == "media_player":
        target_dom = "media_player"
        if service in ["turn_on", "turn_off"]:
            await _smart_power_delegation(eid, "on" if service=="turn_on" else "off", user_creds)
        
        if service == "play_media":
            if (await get_entity_state(eid, user_creds)) in ["off", "unavailable"]:
                await execute_ha_service("media_player", "turn_on", eid, user_creds)
                await asyncio.sleep(8.0)
            if service_data: target_dom, target_svc = "music_assistant", "play_media"

    return await execute_ha_service(target_dom, target_svc, eid, user_creds, service_data)

async def try_handle_compound_command(query, user_creds, model):
    cmds = await decompose_command_query(query, model)
    res = [await _handle_single_command(c, user_creds) for c in cmds if isinstance(c, str)]
    return "\n".join(filter(None, res)) if any(res) else None

async def get_ha_context(user, query=None):
    creds = get_user_creds(user)
    if not query: 
        if c := await ha_cache_get(creds["user"]): return c
    if not HA_URL: return ""
    try:
        def _get(): return requests.get(f"{HA_URL.rstrip('/')}/api/states", headers={"Authorization": f"Bearer {creds['ha_token']}"}, timeout=5)
        r = await run_blocking(_get)
        r.raise_for_status()
        return "Home Assistant Status:\n" + "\n".join([f"{s['entity_id']}: {s['state']}" for s in r.json() if s['entity_id'].split('.')[0] in ['light','switch','media_player','climate'] or 'sensor' in s['entity_id']])
    except: return ""

async def get_rag_context(query):
    if not GlobalResources.nextcloud_collection: return ""
    try:
        docs = await run_blocking(lambda: GlobalResources.nextcloud_collection.similarity_search_with_score(query, k=4))
        return "Nextcloud Docs:\n" + "\n".join([d.page_content[:500] for d in docs])
    except: return ""

# --- Main Stream Generator (UNIFIED) ---
async def generate_rag_stream(query, user, model, use_openai, format_type) -> AsyncGenerator[str, None]:
    builder = StreamResponseBuilder(model, format_type)
    
    # 1. System Task
    if is_system_task(query):
        r = await call_ollama_generate(query, model, stream=True)
        if "iterable" in r:
            yield builder.chunk(role="assistant")
            async for c in r["iterable"]():
                if isinstance(c, dict):
                    token = c.get("response", "")
                    yield builder.chunk(content=token)
            yield builder.chunk(finish_reason="stop")
            yield builder.done()
        return

    # 2. Normal Flow
    refined = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    
    # Action
    creds = get_user_creds(user)
    cmd = await try_handle_compound_command(refined, creds, model)
    if cmd:
        update_history(user, "assistant", cmd)
        yield builder.chunk(role="assistant")
        yield builder.chunk(content=cmd)
        yield builder.chunk(finish_reason="stop")
        yield builder.done()
        return

    # RAG
    context = f"{await get_ha_context(user, refined)}\n{await get_rag_context(refined)}\n{await tool_web_search(refined)}"
    prompt = f"System: Helpful assistant.\nHistory:\n{get_history_context(user)}\nContext:\n{context}\nUser: {refined}\nAnswer:"
    
    r = await call_ollama_generate(prompt, model, stream=True)
    yield builder.chunk(role="assistant")
    
    full_reply = ""
    if "iterable" in r:
        async for c in r["iterable"]():
            if isinstance(c, dict):
                token = c.get("response", "")
                full_reply += token
                yield builder.chunk(content=token)
    
    update_history(user, "assistant", full_reply)
    yield builder.chunk(finish_reason="stop")
    yield builder.done()
