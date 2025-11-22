# app/logic.py
import json
import time
import re
import requests
import asyncio
import caldav
import traceback
import sys
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional, Dict, Any, List
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs 

# Try importing Playwright
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    log.warning("Playwright not found. Javascript-heavy sites will not be indexable.")

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

# Robust User-Agent
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

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

# --- Helpers ---
def update_history(user: str, role: str, content: str):
    if not content: return
    if user not in CHAT_HISTORY: CHAT_HISTORY[user] = []
    CHAT_HISTORY[user].append({"role": role, "content": content})
    if len(CHAT_HISTORY[user]) > MAX_HISTORY_TURNS * 2:
        CHAT_HISTORY[user] = CHAT_HISTORY[user][-(MAX_HISTORY_TURNS * 2):]

def get_history_context(user: str) -> str:
    if user not in CHAT_HISTORY: return ""
    return "\n".join([f"{'USER' if m['role']=='user' else 'ASSISTANT'}: {m['content']}" for m in CHAT_HISTORY[user]])

def clean_llm_output(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'\*\*.*?\*\*:', '', text)
    text = re.sub(r'^(Standalone Command|Command|Output|Result):', '', text.strip(), flags=re.IGNORECASE)
    text = text.replace("```json", "").replace("```", "")
    return text.strip().strip('"').strip("'")

# --- LLM & Tools ---
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

async def _scrape_with_playwright(url):
    """Tier 4 Search: Headless Browser with Nuclear Fallback"""
    if not PLAYWRIGHT_AVAILABLE: return []
    results = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await browser.new_context(user_agent=SEARCH_HEADERS["User-Agent"])
            page = await context.new_page()
            
            try:
                await page.goto(url, timeout=30000, wait_until="networkidle")
                await page.wait_for_timeout(4000) 
                
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")
                
                selectors = [".result", "#main .result", ".result-content", "article", "div[class*='result']", ".g"]
                for sel in selectors:
                    found = soup.select(sel)
                    if found:
                        for res in found[:5]:
                            title = res.select_one("h3, a, h2")
                            body = res.select_one(".content, .st, p, .result-body")
                            if title and body:
                                t_text = title.get_text(strip=True)
                                b_text = body.get_text(strip=True)
                                if len(t_text) > 5 and len(b_text) > 10:
                                    results.append(f"Title: {t_text}\nSnippet: {b_text}")
                        if len(results) >= 2: break
                
                if len(results) < 2:
                    log.info("Playwright selectors failed. Dumping raw page text.")
                    raw_text = await page.evaluate("document.body.innerText")
                    clean_text = re.sub(r'\n\s*\n', '\n', raw_text)
                    truncated = clean_text[:2500]
                    results = [f"RAW PAGE CONTENT (Parse this for answers):\n{truncated}"]

            except Exception as e:
                log.warning(f"Playwright Page Error: {e}")
            finally:
                await browser.close()
    except Exception as e:
        log.error(f"Playwright Engine Error: {e}")
    return results

async def tool_web_search(query: str) -> str:
    if not WHOOGLE_URL: return ""
    q_low = query.lower()
    is_ha_cmd = any(x in q_low for x in ["turn on", "turn off", "toggle", "dim", "status of", "state of"])
    is_explicit = any(x in q_low for x in ["search", "find", "who is", "what is", "google", "tell me about", "linux", "price", "cost"])
    if is_ha_cmd and not is_explicit: return "" 

    base_url_r = WHOOGLE_URL.rstrip('/')
    
    # Check if WHOOGLE_URL is already an API path
    is_custom_api_url = any(path_part in WHOOGLE_URL for path_part in ["/api/web/getQuery", "/api/web/search"])

    # --- Tier 1: Custom API (Fixing the path duplication bug) ---
    if "api/web" in WHOOGLE_URL in WHOOGLE_URL:
        try:
            # FIX: If WHOOGLE_URL contains the path, use it as the base URL for parameters.
            target_url = WHOOGLE_URL # Use the WHOOGLE_URL exactly as provided
            
            params = {"q": query, "page": 1, "cachettl": 0} # Use correct query structure

            def do_search_custom_api():
                return requests.get(target_url, params=params, headers=SEARCH_HEADERS, timeout=6)
            
            r = await run_blocking(do_search_custom_api)
            if r.status_code == 200:
                try:
                    data = r.json()
                    results = data.get("results", data.get("hits", []))
                    
                    if isinstance(results, list) and len(results) > 0:
                        formatted = [f"Title: {res.get('title', 'N/A')}\nSnippet: {res.get('snippet', res.get('content', ''))}" for res in results[:4]]
                        log.info(f"Tier 1 (Custom API) found {len(formatted)} results.")
                        return "### Real-time Web Search Results:\n" + "\n\n".join(formatted)
                except:
                    log.warning("Custom API returned HTML or non-standard JSON. Falling through.")
        except Exception: pass
        
    # --- Tier 2: Legacy JSON (Whoogle/SearXNG standard) ---
    try:
        def do_search_json():
            return requests.get(f"{base_url_r}/search", params={"q": query, "format": "json"}, headers=SEARCH_HEADERS, timeout=6)
        r = await run_blocking(do_search_json)
        if r.status_code == 200:
            try:
                data = r.json()
                results = data.get("results", [])
                if results:
                    formatted = [f"Title: {res.get('title')}\nSnippet: {res.get('content', '')}" for res in results[:4]]
                    log.info(f"Tier 2 (JSON) found {len(formatted)} results.")
                    return "### Real-time Web Search Results:\n" + "\n\n".join(formatted)
            except: pass
    except: pass

    # --- Tier 3: HTML Scraping ---
    try:
        target_url = f"{base_url_r}/search?q={query}"
        def do_search_html():
            return requests.get(target_url, headers=SEARCH_HEADERS, timeout=6)
        r = await run_blocking(do_search_html)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        
        selectors = [".result", "#main .result", ".result-content", ".g", "div[class*='result']", "article"]
        for sel in selectors:
            found = soup.select(sel)
            if found:
                for res in found[:4]:
                    title = res.select_one("h3, a, h2")
                    body = res.select_one(".content, .st, p")
                    if title and body:
                        results.append(f"Title: {title.get_text(strip=True)}\nSnippet: {body.get_text(strip=True)}")
                if len(results) >= 2: break
        
        if results:
            log.info(f"Tier 3 (HTML) found {len(results)} results.")
            return "### Real-time Web Search Results:\n" + "\n\n".join(results)
    except Exception: pass

    # --- Tier 4: Playwright (Headless Browser) ---
    if PLAYWRIGHT_AVAILABLE:
        log.info("Lower tiers failed. Engaging Tier 4 (Playwright) for SPA rendering...")
        results = await _scrape_with_playwright(f"{base_url_r}/search?q={query}")
        if results:
            log.info(f"Tier 4 (Playwright) found {len(results)} results.")
            return "### Real-time Web Search Results:\n" + "\n\n".join(results)

    log.warning(f"All search tiers returned 0 results for '{query}'")
    return "System Notification: Web search was performed but returned no results."

async def tool_calendar(date_range="today") -> str:
    if not NEXTCLOUD_URL: return ""
    try:
        client = caldav.DAVClient(url=f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav", username=NEXTCLOUD_USER, password=NEXTCLOUD_PASS)
        now = datetime.now()
        end_date = now + timedelta(days=14)
        def _fetch():
            found_events = []
            try:
                for cal in client.principal().calendars():
                    for ev in cal.date_search(start=now, end=end_date, expand=True):
                        try:
                            vevent = ev.vobject_instance.vevent
                            start_dt = vevent.dtstart.value
                            time_str = start_dt.strftime("%Y-%m-%d %H:%M") if isinstance(start_dt, datetime) else f"{start_dt} (All Day)"
                            found_events.append(f"- [{time_str}] {vevent.summary.value}")
                        except:
                             if "SUMMARY:" in ev.data: found_events.append(f"- [Unknown Time] {ev.data.split('SUMMARY:')[1].splitlines()[0]}")
            except: pass
            found_events.sort()
            return found_events
        results = await run_blocking(_fetch)
        return "Calendar Events:\n" + "\n".join(results) if results else "Calendar: No upcoming events."
    except: return ""

# --- Logic ---
async def get_entity_state(entity_id: str, user_creds: Dict[str, str]) -> Optional[str]:
    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    for _ in range(3):
        try:
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
            def _post(): return requests.post(url, json=payload, headers=headers, timeout=3.0)
            r = await run_blocking(_post)
            if r.status_code < 400: return f"Successfully executed {domain}.{service} on {entity_id}."
            last_err = r.text
        except Exception as e: last_err = str(e)
        await asyncio.sleep(1)
    return f"Failed: {last_err}"

def is_system_task(query: str) -> bool:
    q = query.strip()
    return q.startswith("### Task:") or ("Generate" in q and "tags" in q)

async def contextualize_query(query, user, model):
    if not get_history_context(user): return query
    prompt = (
        "You are a command rewriting engine. Clarify the User Input based on History.\n"
        "RULES:\n"
        "1. Output ONLY the refined natural language query.\n"
        "2. DO NOT generate entity IDs (e.g., NO 'light.office_tv').\n"
        "3. Keep it simple English.\n\n"
        f"History:\n{get_history_context(user)}\nInput: {query}\nRefined Query:"
    )
    r = await call_ollama_generate(prompt, model)
    clean = clean_llm_output(r.get("text", ""))
    return clean if clean else query

async def decompose_command_query(query: str, model: str) -> List[str]:
    cleaned_query = clean_llm_output(query)
    prompt = f"Break this request into a JSON list of atomic commands. Output JSON ONLY.\nUser: '{cleaned_query}'\nOutput:"
    r = await call_ollama_generate(prompt, model)
    match = re.search(r"\[.*\]", r.get("text", ""), re.DOTALL)
    if match:
        try: return json.loads(match.group(0))
        except: pass
    return [cleaned_query]

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
    tasks = [_handle_single_command(c, user_creds) for c in cmds if isinstance(c, str) and len(c) > 3]
    if not tasks: return None
    results = await asyncio.gather(*tasks)
    return "\n".join(filter(None, results)) if any(results) else None

async def get_ha_context(user, query=None):
    if not GlobalResources.ha_collection or not HA_URL: return ""
    docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(query, k=5))
    if not docs: return ""
    
    creds = get_user_creds(user)
    check_ids = {d.metadata.get("entity_id"): d.page_content for d in docs if d.metadata.get("entity_id")}
    live_info = []
    try:
        def _get_all(): return requests.get(f"{HA_URL.rstrip('/')}/api/states", headers={"Authorization": f"Bearer {creds['ha_token']}"}, timeout=3.0)
        r = await run_blocking(_get_all)
        if r.status_code == 200:
            states = {s["entity_id"]: s["state"] for s in r.json()}
            for eid, desc in check_ids.items():
                live_info.append(f"- {desc} [Status: {states.get(eid, 'unknown')}]")
    except: pass
    return "Home Assistant Devices:\n" + "\n".join(live_info)

async def get_rag_context(query):
    if not GlobalResources.nextcloud_collection: return ""
    try:
        docs = await run_blocking(lambda: GlobalResources.nextcloud_collection.similarity_search_with_score(query, k=4))
        return "Nextcloud Docs:\n" + "\n".join([d.page_content[:500] for d in docs])
    except: return ""

async def generate_rag_stream(query, user, model, use_openai, format_type) -> AsyncGenerator[str, None]:
    builder = StreamResponseBuilder(model, format_type)
    if is_system_task(query):
        r = await call_ollama_generate(query, model, stream=True)
        if "iterable" in r:
            yield builder.chunk(role="assistant")
            async for c in r["iterable"]():
                if isinstance(c, dict): yield builder.chunk(content=c.get("response", ""))
            yield builder.chunk(finish_reason="stop")
            yield builder.done()
        return

    refined = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    
    creds = get_user_creds(user)
    cmd = await try_handle_compound_command(refined, creds, model)
    if cmd:
        update_history(user, "assistant", cmd)
        yield builder.chunk(role="assistant")
        yield builder.chunk(content=cmd)
        yield builder.chunk(finish_reason="stop")
        yield builder.done()
        return

    ha_ctx = await get_ha_context(user, refined)
    nc_ctx = await get_rag_context(refined)
    search_ctx = await tool_web_search(refined) 

    cal_ctx = ""
    if any(x in refined.lower() for x in ["calendar", "schedule", "meeting", "today", "tomorrow"]):
        cal_ctx = f"Calendar:\n{await tool_calendar()}"

    context = f"{ha_ctx}\n{nc_ctx}\n{search_ctx}\n{cal_ctx}"
    today = datetime.now().strftime('%Y-%m-%d')
    prompt = f"""### INSTRUCTIONS
You are an intelligent assistant with real-time access.
TODAY: {today}

### CONTEXT
{context}

### RULES
1. Use CONTEXT for answers. It contains real-time data.
2. If CONTEXT says "Linux Kernel 6.x", say that. Do not use old training data.
3. If Web Search returned no results, admit it.

### QUERY
{refined}

### ANSWER
"""
    r = await call_ollama_generate(prompt, model, stream=True)
    yield builder.chunk(role="assistant")
    full = ""
    if "iterable" in r:
        async for c in r["iterable"]():
            if isinstance(c, dict):
                token = c.get("response", "")
                full += token
                yield builder.chunk(content=token)
    update_history(user, "assistant", full)
    yield builder.chunk(finish_reason="stop")
    yield builder.done()
