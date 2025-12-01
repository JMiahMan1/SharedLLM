# app/logic/utils.py
import json
import time
import re
import requests
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union

from settings import (
    log, run_blocking, get_user_creds, ha_cache_get, ha_cache_set,
    OLLAMA_URL, HA_URL, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    WHOOGLE_URL, OPENAI_MODEL, DEFAULT_MODEL, OLLAMA_TIMEOUT, OLLAMA_RETRY,
    GlobalResources, openai_client, EMB_MODEL, CHAT_HISTORY_TTL,
    MAX_HISTORY_TURNS
)

# Import The New Intent Engine
from intent_engine import engine as intent_engine

try:
    from pydantic import ValidationError
except ImportError:
    ValidationError = Exception

# --- History Management ---
def _get_history_key(user: str) -> str:
    return f"rag:history:{user}"

def update_history(user: str, role: str, content: str):
    if not content: return
    msg = json.dumps({"role": role, "content": content})
    
    if GlobalResources.redis_client:
        try:
            key = _get_history_key(user)
            GlobalResources.redis_client.rpush(key, msg)
            GlobalResources.redis_client.ltrim(key, -(MAX_HISTORY_TURNS * 2), -1)
            GlobalResources.redis_client.expire(key, CHAT_HISTORY_TTL)
        except Exception as e:
            log.error(f"Redis write error: {e}")
    else:
        pass

def get_history_context(user: str) -> str:
    messages = []
    if GlobalResources.redis_client:
        try:
            key = _get_history_key(user)
            raw_msgs = GlobalResources.redis_client.lrange(key, 0, -1)
            messages = []
            for m in raw_msgs:
                if isinstance(m, bytes): m = m.decode('utf-8')
                try: messages.append(json.loads(m))
                except: pass
        except Exception as e:
            log.error(f"Redis read error: {e}")
            return ""
    
    if not messages: return ""
    
    # Format history as chat log for the LLM
    history_text = ""
    for m in messages:
        role = "USER" if m.get('role') == 'user' else "ASSISTANT"
        history_text += f"{role}: {m.get('content', '')}\n"
    return history_text

def clean_llm_output(text: str, is_voice: bool = True) -> str:
    if not text: return ""
    if not is_voice: return text 
    
    # 1. Remove Thinking Blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    # 2. Aggressive Symbol Stripping for TTS
    text = re.sub(r'[\*#_`]', '', text)
    
    # 3. Remove Prefixes
    text = re.sub(r'^(Standalone Command|Command|Output|Result|Unified Home AI):', '', text, flags=re.IGNORECASE)
    
    # 4. Remove Code Blocks remnants
    text = text.replace("json", "") 
    
    # 5. Normalize Smart Quotes and Punctuation
    replacements = {
        '\u201c': '"', '\u201d': '"', 
        '\u2018': "'", '\u2019': "'", 
        '\u2013': '-', '\u2014': '-', 
        '&': ' and ',
        '%': ' percent',
        '@': ' at '
    }
    for char, rep in replacements.items():
        text = text.replace(char, rep)

    # 6. FORCE STRIP NON-ASCII
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    
    # 7. Collapse multiple spaces
    text = re.sub(r'[ \t]+', ' ', text)
    
    return text

# --- Helper: Safe Similarity Search ---
def safe_similarity_search(collection, query: str, k: int = 4):
    """
    Safely executes similarity search on a SPECIFIC collection.
    """
    if not collection: 
        return []
    try:
        return collection.similarity_search(query, k=k)
    except (ValidationError, Exception) as e:
        log.error(f"RAG Search Error: {e}")
        return []

# --- LLM Functions ---
async def call_ollama_generate(prompt: str, model: str = DEFAULT_MODEL, stream: bool = False):
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream, "options": {"temperature": 0.0}}
    for attempt in range(max(1, OLLAMA_RETRY)):
        try:
            def _post(): return requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT, stream=stream)
            resp = await run_blocking(_post)
            resp.raise_for_status()
            if stream:
                async def async_iter():
                    for chunk in resp.iter_lines(decode_unicode=True):
                        if chunk:
                            try: yield json.loads(chunk)
                            except: pass
                        await asyncio.sleep(0)
                return {"iterable": async_iter}
            else:
                return {"text": resp.json().get("response", "")}
        except Exception as e:
            log.warning(f"Ollama attempt {attempt+1} failed: {e}")
            await asyncio.sleep(0.5)
    return {"error": "Ollama unavailable"}

async def call_openai_chat(messages, model=OPENAI_MODEL, stream=False):
    if not openai_client: 
        return {"error": "OpenAI not configured. Check OPENAI_API_KEY."}

    try:
        def _create_completion():
            return openai_client.ChatCompletion.create(
                model=model,
                messages=messages,
                stream=stream
            )

        if stream:
            response_stream = await run_blocking(_create_completion)
            async def async_iter():
                for chunk in response_stream:
                    if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                        content = getattr(chunk.choices[0].delta, 'content', '')
                        if content:
                            yield {"response": content}
                    await asyncio.sleep(0) 
            return {"iterable": async_iter}
        else:
            resp = await run_blocking(_create_completion)
            return {"text": resp.choices[0].message.content}
    except Exception as e:
        log.error(f"OpenAI Call Error: {e}")
        return {"error": str(e)}

# --- Context Retrieval Utils ---

async def get_ha_context(user, query=None):
    """
    Retrieves context ONLY from Home Assistant collection.
    Does NOT search Nextcloud.
    """
    if not GlobalResources.ha_collection or not HA_URL: return ""
    try:
        # STRICT SEPARATION: Only query the 'ha_collection'
        docs = await run_blocking(lambda: safe_similarity_search(GlobalResources.ha_collection, query, k=5))
        if not docs: return ""
        
        creds = get_user_creds(user)
        check_ids = [d.metadata.get("entity_id") for d in docs if d.metadata.get("entity_id")]
        
        headers = {"Authorization": f"Bearer {creds['ha_token']}"}
        
        # Verify current state of found entities
        def _fetch_states():
            try:
                return requests.get(f"{HA_URL.rstrip('/')}/api/states", headers=headers, timeout=3.0)
            except: return None

        r = await run_blocking(_fetch_states)
        
        if r and r.status_code == 200:
            all_states = {s["entity_id"]: s for s in r.json()}
            lines = []
            for eid in check_ids:
                if eid in all_states:
                    s = all_states[eid]
                    friendly = s.get("attributes", {}).get("friendly_name", eid)
                    state = s.get("state")
                    # Enrich with attributes if useful (e.g. brightness, volume)
                    attrs = []
                    if "brightness" in s.get("attributes", {}): 
                        attrs.append(f"brightness: {s['attributes']['brightness']}")
                    if "volume_level" in s.get("attributes", {}):
                        attrs.append(f"volume: {s['attributes']['volume_level']}")
                    
                    attr_str = f" ({', '.join(attrs)})" if attrs else ""
                    lines.append(f"- {friendly} ({eid}) is {state}{attr_str}")
            
            return "Home Assistant Devices (Verified States):\n" + "\n".join(lines)
    except Exception as e:
        log.error(f"HA Context Error: {e}")
    return ""

async def get_rag_context(query):
    """
    Retrieves context ONLY from Nextcloud/Documents collection.
    Does NOT search HA Devices.
    """
    if not GlobalResources.nextcloud_collection: return ""
    try:
        # STRICT SEPARATION: Only query 'nextcloud_collection'
        docs = await run_blocking(lambda: safe_similarity_search(GlobalResources.nextcloud_collection, query, k=3))
        
        # Add source metadata to context so LLM knows where it came from
        context_lines = []
        for d in docs:
            if d.page_content:
                source = d.metadata.get("source", "Unknown File")
                context_lines.append(f"[Source: {source}]\n...{d.page_content[:600]}...")
                
        if not context_lines: return ""
        return "Nextcloud Documents:\n" + "\n".join(context_lines)
    except: return ""

async def contextualize_query(query, user, model):
    # Optimization: If intent engine is confident, skip contextualization
    intent, score, is_high_confidence = await intent_engine.classify(query)
    
    stateless_intents = [
        "turn_on", "turn_off", "toggle", "play_media", "stop_media",
        "calendar_add", "calendar_delete", "calendar_list", "calendar_update",
        "time_query", "intent_learn"
    ]
    
    if score > 0.85 and intent in stateless_intents:
        return query

    verbs = ["turn", "play", "stop", "toggle", "schedule", "add", "delete", "remove", "cancel", "remind", "list", "learn", "teach", "map"]
    if any(query.lower().lstrip().startswith(v) for v in verbs): return query
    
    hist = get_history_context(user)
    if not hist: return query
    
    if len(query) > 150: return query

    prompt = f"Rewrite based on history:\n{hist}\nInput: {query}\nRefined (Return ONLY the query):"
    r = await call_ollama_generate(prompt, model)
    refined = clean_llm_output(r.get("text", query), is_voice=False) 
    
    if len(refined) > len(query) * 3: return query
    return refined
