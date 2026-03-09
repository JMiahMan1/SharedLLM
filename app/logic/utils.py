# app/logic/utils.py
import json
import re
import requests
import asyncio

from app.settings import (
    log, run_blocking, get_user_creds,
    OLLAMA_URL, HA_URL, OPENAI_MODEL, DEFAULT_MODEL, OLLAMA_TIMEOUT, OLLAMA_RETRY,
    GlobalResources, openai_client, CHAT_HISTORY_TTL,
    MAX_HISTORY_TURNS
)

# Import The New Intent Engine
from app.intent_engine import engine as intent_engine

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
    """
    Cleans text for Text-to-Speech (TTS) optimization if is_voice=True.
    Preserves formatting for Web UI if is_voice=False.
    """
    if not text: return ""
    if not is_voice: return text 
    
    # 1. Remove Thinking Blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    # 2. Remove Speaker Prefixes
    text = re.sub(r'^(Jarvis|Assistant|Unified Home AI|Result|Output|Command):\s*', '', text, flags=re.IGNORECASE)
    
    # 3. Fix Phone Numbers for TTS
    text = re.sub(r'\b(\d{3})-(\d{3})-(\d{4})\b', r'\1 \2 \3', text)

    # 4. Remove Markdown & Action Emotes
    text = re.sub(r'\*.*?\*', '', text)
    text = re.sub(r'[\*#_`]', '', text)

    # 5. Normalize Punctuation for Flow
    replacements = {
        '\u201c': '', '\u201d': '', # Remove smart quotes
        '"': '',                    # Remove standard quotes
        '\u2018': '', '\u2019': '', 
        '\u2013': ', ', '\u2014': ', ', # Em-dashes to pauses
        '&': ' and ',
        '%': ' percent',
        '@': ' at ',
        '+': ' plus ',
        '=': ' equals '
    }
    for char, rep in replacements.items():
        text = text.replace(char, rep)

    # 6. Fix "Sticky" Hyphens between words
    text = re.sub(r'(?<=[a-zA-Z])-(?=[a-zA-Z])', ' ', text)

    # 7. Force Strip Non-ASCII (Emoji Killer)
    text = re.sub(r'[^\x00-\x7F]+', '', text)
    
    # 8. Collapse multiple spaces BUT DO NOT STRIP
    # CRITICAL FIX: Removed .strip() to preserve token spacing in stream
    text = re.sub(r'\s+', ' ', text)
    
    return text

# --- Helper: Safe Similarity Search ---
def safe_similarity_search(collection, query: str, k: int = 4):
    """
    Safely executes similarity search, catching Pydantic ValidationErrors.
    """
    if not collection: 
        return []
    try:
        return collection.similarity_search(query, k=k)
    except (ValidationError, Exception) as e:
        log.error(f"RAG Search Error (Potentially corrupted doc in DB): {e}")
        return []

# --- LLM Functions ---
async def call_ollama_generate(prompt: str, model: str = DEFAULT_MODEL, stream: bool = False):
    url = f"{OLLAMA_URL.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": stream, "options": {"temperature": 0.0}}
    for attempt in range(max(1, OLLAMA_RETRY)):
        try:
            log.info(f"DEBUG: OLLAMA REQ URL={url} MODEL={model} PROMPT_LEN={len(prompt)}")
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
        except requests.exceptions.ConnectTimeout:
            log.error(f"Ollama Connection Timed Out (Attempt {attempt+1}/{OLLAMA_RETRY}) - Host unreachable?")
            if attempt == OLLAMA_RETRY - 1: return {"error": f"Ollama Unreachable ({OLLAMA_URL})"}
        except requests.exceptions.ReadTimeout:
            log.warning(f"Ollama Generation Timed Out (Attempt {attempt+1}) - Model too slow?")
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

async def extract_search_query(query: str, model: str = DEFAULT_MODEL) -> str:
    """Extracts the core question from a large user prompt (e.g. PDF upload) for RAG search."""
    # Ensure it's not impossibly large for Ollama context window
    truncated_query = query[:8000]
    prompt = f"Extract only the core question from this user prompt to be used for a semantic database search. Ignore any pasted text, code, or document context. Return ONLY the short search query (MAX 10 words). If there is no clear question, summarize the topic in 3-5 words.\n\nPrompt: {truncated_query}\n\nSearch Query:"
    
    try:
        r = await call_ollama_generate(prompt, model)
        result = clean_llm_output(r.get("text", ""), is_voice=False).strip()
        if result:
            return result
    except Exception as e:
        log.warning(f"Failed to extract search query: {e}")
    # Fallback: Just return a chunk of the original query
    return query[:300]


async def get_rag_context(query, model: str = DEFAULT_MODEL):
    """
    Retrieves context ONLY from Nextcloud/Documents collection.
    Does NOT search HA Devices.
    If the query is very large (e.g., pasted PDF/Code), it extracts the core question first.
    """
    if not GlobalResources.nextcloud_collection: return ""
    
    search_query = query
    if len(query) > 500:
        log.info(f"Query is unusually large ({len(query)} chars). Extracting core search question...")
        search_query = await extract_search_query(query, model)
        log.info(f"Extracted Search Query: '{search_query}'")

    try:
        # STRICT SEPARATION: Only query 'nextcloud_collection'
        docs = await run_blocking(lambda: safe_similarity_search(GlobalResources.nextcloud_collection, search_query, k=3))
        
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
    # Use is_voice=False to preserve content during rewrite
    refined = clean_llm_output(r.get("text", query), is_voice=False) 
    
    if len(refined) > len(query) * 3: return query
    return refined
