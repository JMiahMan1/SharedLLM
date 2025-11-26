# app/logic.py
import json
import time
import re
import requests
import asyncio
import caldav
import traceback
import dateparser
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional, Dict, Any, List
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# Try importing Playwright
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Import from settings
from settings import (
    log, run_blocking, get_user_creds, ha_cache_get, ha_cache_set,
    OLLAMA_URL, HA_URL, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    WHOOGLE_URL, OPENAI_MODEL, DEFAULT_MODEL, OLLAMA_TIMEOUT, OLLAMA_RETRY,
    GlobalResources, openai_client, EMB_MODEL, CHAT_HISTORY_TTL,
    MAX_HISTORY_TURNS
)

# Robust User-Agent
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

# In-memory fallback if Redis is missing
_LOCAL_HISTORY = {}

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

# --- History Management (Redis / Local) ---
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
        # Fallback
        if user not in _LOCAL_HISTORY: _LOCAL_HISTORY[user] = []
        _LOCAL_HISTORY[user].append({"role": role, "content": content})
        if len(_LOCAL_HISTORY[user]) > MAX_HISTORY_TURNS * 2:
            _LOCAL_HISTORY[user] = _LOCAL_HISTORY[user][-(MAX_HISTORY_TURNS * 2):]

def get_history_context(user: str) -> str:
    messages = []
    if GlobalResources.redis_client:
        try:
            key = _get_history_key(user)
            raw_msgs = GlobalResources.redis_client.lrange(key, 0, -1)
            messages = [json.loads(m) for m in raw_msgs]
        except Exception as e:
            log.error(f"Redis read error: {e}")
            return ""
    else:
        messages = _LOCAL_HISTORY.get(user, [])
        
    return "\n".join([f"{'USER' if m['role']=='user' else 'ASSISTANT'}: {m['content']}" for m in messages])

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
                        delta = chunk.choices[0].delta
                        content = getattr(delta, 'content', '')
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

async def _scrape_with_playwright(url):
    """Tier 3 Search: Headless Browser"""
    if not PLAYWRIGHT_AVAILABLE: return []
    results = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = await browser.new_context(user_agent=SEARCH_HEADERS["User-Agent"])
            page = await context.new_page()
            try:
                await page.goto(url, timeout=15000, wait_until="domcontentloaded")
                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")
                selectors = [".result", "#main .result", ".result-content", "article", ".g"]
                for sel in selectors:
                    found = soup.select(sel)
                    if found:
                        for res in found[:4]:
                            title = res.select_one("h3, a, h2")
                            body = res.select_one(".content, .st, p, .result-body")
                            if title and body:
                                results.append(f"Title: {title.get_text(strip=True)}\nSnippet: {body.get_text(strip=True)}")
                        if len(results) >= 2: break
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
    is_ha_cmd = any(x in q_low for x in ["turn on", "turn off", "toggle", "dim", "status of", "state of", "play", "stop"])
    is_explicit = any(x in q_low for x in ["search", "find", "who is", "what is", "google", "tell me about", "linux", "price", "cost", "kernel"])
    
    if is_ha_cmd and not is_explicit: 
        return "" 

    log.info(f"Executing Web Search for: {query}")
    
    # Construct Target URL (FIX: Robust path detection)
    parsed = urlparse(WHOOGLE_URL)
    # Check if path specifically contains 'search', otherwise append it
    if "search" in parsed.path:
        # User explicitly provided a full path (e.g. /my-search-instance/search)
        search_endpoint = WHOOGLE_URL
    else:
        # Append /search to root
        search_endpoint = f"{WHOOGLE_URL.rstrip('/')}/search"

    # --- Tier 1: JSON API ---
    try:
        def do_search():
            return requests.get(search_endpoint, params={"q": query, "format": "json"}, headers=SEARCH_HEADERS, timeout=6)
        
        r = await run_blocking(do_search)
        if r.status_code == 200:
            try:
                data = r.json()
                results = data.get("results", data.get("hits", []))
                if results:
                    formatted = [f"Title: {res.get('title')}\nSnippet: {res.get('content', '')}" for res in results[:4]]
                    return "### Real-time Web Search Results (JSON):\n" + "\n\n".join(formatted)
            except: pass 
    except Exception as e:
        log.warning(f"Web Search Tier 1 (JSON) Error: {e}")

    # --- Tier 2: HTML Scraping ---
    try:
        def do_search_html():
            return requests.get(search_endpoint, params={"q": query}, headers=SEARCH_HEADERS, timeout=8)
        
        r = await run_blocking(do_search_html)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            selectors = [".result", "#main .result", ".result-content", "article", ".g", "div[class*='result']"]
            for sel in selectors:
                found = soup.select(sel)
                if found:
                    for res in found[:4]:
                        title = res.select_one("h3, a, h2")
                        body = res.select_one(".content, .st, p")
                        if title and body:
                            t_text = title.get_text(strip=True)
                            b_text = body.get_text(strip=True)
                            if t_text and b_text:
                                results.append(f"Title: {t_text}\nSnippet: {b_text}")
                    if len(results) >= 2: break
            
            if results:
                return "### Real-time Web Search Results (HTML):\n" + "\n\n".join(results)
    except Exception as e:
        log.warning(f"Web Search Tier 2 (HTML) Error: {e}")

    # --- Tier 3: Playwright ---
    if PLAYWRIGHT_AVAILABLE:
        log.info("Engaging Tier 3 (Playwright) for web search...")
        # Fallback to direct URL construction for browser
        browser_url = f"{WHOOGLE_URL.rstrip('/')}/search?q={query}"
        results = await _scrape_with_playwright(browser_url)
        if results:
             return "### Real-time Web Search Results (Playwright):\n" + "\n\n".join(results)

    return "System Notification: Web search performed but returned no results or failed."

# --- Calendar Tools ---
def _get_cal_client(creds):
    url = f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav"
    # FIX: Enforce timeout to prevent thread pool starvation on bad connections
    return caldav.DAVClient(url=url, username=creds.get('user', NEXTCLOUD_USER), password=creds.get('nc_pass', NEXTCLOUD_PASS), timeout=20)

def _get_default_cal_key(user: str) -> str:
    return f"rag:cal_default:{user}"

def _get_writable_cache_key(url: str) -> str:
    return f"rag:cal_writable:{url}"

def _is_cal_writable(cal, user: str) -> bool:
    """Checks if a calendar is writable using a test write operation. Caches result."""
    url = str(cal.url).lower()

    # 1. Redis Cache Check
    if GlobalResources.redis_client:
        cache_key = _get_writable_cache_key(url)
        cached = GlobalResources.redis_client.get(cache_key)
        if cached is not None: return cached == "1"

    # 2. Active Write Check
    try:
        test_uid = f"RAG_WRITE_{int(time.time())}"
        ev = cal.save_event(dtstart=datetime.now() - timedelta(hours=1), summary=test_uid)
        ev.delete() # Clean up immediately
        
        if GlobalResources.redis_client: 
            GlobalResources.redis_client.setex(cache_key, 3600, "1") # Cache success for 1h
        return True
    except requests.exceptions.ReadTimeout:
        log.warning(f"Calendar write check timed out for {cal.name}")
        if GlobalResources.redis_client: 
            GlobalResources.redis_client.setex(cache_key, 3600, "0") # Cache failure
        return False
    except Exception:
        if GlobalResources.redis_client: 
            GlobalResources.redis_client.setex(cache_key, 3600, "0")
        return False

def _set_user_default_cal(user: str, cal_name: str):
    if GlobalResources.redis_client:
        GlobalResources.redis_client.set(_get_default_cal_key(user), cal_name)

def _get_user_default_cal(user: str) -> Optional[str]:
    if GlobalResources.redis_client:
        return GlobalResources.redis_client.get(_get_default_cal_key(user))
    return None

async def tool_calendar_list(user_creds: Dict[str, str]) -> str:
    if not NEXTCLOUD_URL: return ""
    try:
        def _fetch():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            # Filter output to avoid showing system calendars by default (reduces confusion)
            valid = [f"- {c.name}" for c in calendars if "birthday" not in (c.name or "").lower() and "contact" not in (c.name or "").lower()]
            return "Available Calendars:\n" + "\n".join(valid) if valid else "No writable calendars."
        return await run_blocking(_fetch)
    except Exception as e: return f"Error listing calendars: {e}"

async def tool_calendar_read(user_creds: Dict[str, str]) -> str:
    if not NEXTCLOUD_URL: return ""
    try:
        def _fetch():
            client = _get_cal_client(user_creds)
            found_events = []
            calendars = client.principal().calendars()
            now = datetime.now()
            end = now + timedelta(days=7)
            for cal in calendars:
                # Optimization: Check writability cache if available to skip known read-only
                if GlobalResources.redis_client:
                    ck = _get_writable_cache_key(str(cal.url).lower())
                    if GlobalResources.redis_client.get(ck) == "0": continue

                try:
                    events = cal.search(start=now, end=end, event=True, expand=True)
                    for ev in events:
                        if hasattr(ev.vobject_instance, 'vevent'):
                            ve = ev.vobject_instance.vevent
                            t = ve.dtstart.value.strftime("%Y-%m-%d %H:%M") if isinstance(ve.dtstart.value, datetime) else str(ve.dtstart.value)
                            found_events.append(f"- [{t}] {ve.summary.value} ({cal.name})")
                except: pass
            return found_events
        
        results = await run_blocking(_fetch)
        return "Upcoming Events:\n" + "\n".join(results) if results else "No events found."
    except: return ""

async def extract_event_data(query: str, model: str) -> Dict[str, str]:
    prompt = (
        f"Extract details from: \"{query}\".\n"
        "Return JSON with keys: 'summary' (string), 'start_time' (natural language or 'today'), 'calendar_target' (string or null), 'intent' ('add', 'delete', 'update').\n"
        "IMPORTANT: 'summary' MUST be the event title. If input is 'RAG_Test_123', summary is 'RAG_Test_123'.\n"
        "JSON:"
    )
    r = await call_ollama_generate(prompt, model=model)
    text = clean_llm_output(r.get("text", "{}"))
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else json.loads(text)
    except: return {}

async def tool_calendar_add(query: str, user_creds: Dict[str, str], model: str) -> str:
    if not NEXTCLOUD_URL: return "Error: Nextcloud not configured."
    data = await extract_event_data(query, model)
    summary = data.get("summary")
    start = data.get("start_time")
    target = data.get("calendar_target")
    
    if not summary or not start: return "Missing event details."
    if "missing" in summary.lower() and "detail" in summary.lower(): return "Error: Could not extract valid event summary."

    dt = dateparser.parse(start, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now()})
    if not dt: return f"Invalid date: {start}"

    try:
        def _add():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            if not calendars: raise Exception("No calendars found.")
            
            # Sort candidates loosely to try 'best' names first
            # Personal/Private -> Username -> Rest
            calendars.sort(key=lambda c: 0 if "personal" in (c.name or "").lower() else (1 if user_creds['user'].lower() in (c.name or "").lower() else 10))

            selected = None
            
            # 1. Explicit Target (Check name)
            if target:
                for c in calendars:
                    if target.lower() in (c.name or "").lower():
                        if _is_cal_writable(c, user_creds['user']):
                            selected = c
                            break
            
            # 2. Stored Default
            if not selected:
                def_name = _get_user_default_cal(user_creds['user'])
                if def_name:
                    for c in calendars:
                        if c.name == def_name: 
                            if _is_cal_writable(c, user_creds['user']):
                                selected = c
                                break
            
            # 3. Lazy Search (Stop at first writable)
            if not selected:
                for c in calendars:
                    if _is_cal_writable(c, user_creds['user']):
                        selected = c
                        break

            # 4. Fallback (Use first writable found, even if not ideal)
            if not selected:
                 for c in calendars:
                     if _is_cal_writable(c, user_creds['user']):
                         selected = c
                         break

            if not selected: raise Exception("No suitable writable calendar found.")

            end = dt + timedelta(hours=1)
            selected.save_event(dtstart=dt, dtend=end, summary=summary)
            _set_user_default_cal(user_creds['user'], selected.name)
            return selected.name

        cal_name = await run_blocking(_add)
        return f"Scheduled '{summary}' for {dt.strftime('%Y-%m-%d %H:%M')} on '{cal_name}'."
    except Exception as e:
        log.error(f"Calendar Add Error: {e}")
        return f"Failed to add event: {str(e)}"

async def tool_calendar_delete(query: str, user_creds: Dict[str, str], model: str) -> str:
    if not NEXTCLOUD_URL: return "Error: Nextcloud not configured."
    data = await extract_event_data(query, model)
    keyword = data.get("summary")
    target = data.get("calendar_target")
    if not keyword: return "Missing event name."

    try:
        def _delete():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            
            candidates = []
            for c in calendars:
                if target and target.lower() not in (c.name or "").lower(): continue
                if _is_cal_writable(c, user_creds['user']):
                    candidates.append(c)

            count = 0
            start = datetime.now() - timedelta(days=1)
            end = start + timedelta(days=30)
            
            for c in candidates:
                try:
                    events = c.search(start=start, end=end, event=True, expand=True)
                    for ev in events:
                        if keyword.lower() in ev.vobject_instance.vevent.summary.value.lower():
                            ev.delete()
                            count += 1
                            break
                    if count > 0: break
                except: pass
            return count

        cnt = await run_blocking(_delete)
        return f"Deleted event matching '{keyword}'." if cnt else "No matching event found."
    except Exception as e: return f"Delete error: {e}"

async def tool_calendar_update(query: str, user_creds: Dict[str, str], model: str) -> str:
    if not NEXTCLOUD_URL: return "Error: Nextcloud not configured."
    data = await extract_event_data(query, model)
    keyword = data.get("summary")
    new_start = data.get("start_time")
    target = data.get("calendar_target")
    
    if not keyword or not new_start: return "Missing details."
    dt = dateparser.parse(new_start, languages=['en'], settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now()})
    if not dt: return f"Invalid date: {new_start}"

    try:
        def _update():
            client = _get_cal_client(user_creds)
            calendars = client.principal().calendars()
            
            candidates = []
            for c in calendars:
                if target and target.lower() not in (c.name or "").lower(): continue
                if _is_cal_writable(c, user_creds['user']):
                    candidates.append(c)
            
            count = 0
            start = datetime.now() - timedelta(days=1)
            end = start + timedelta(days=30)
            
            for c in candidates:
                try:
                    events = c.search(start=start, end=end, event=True, expand=True)
                    for ev in events:
                        ve = ev.vobject_instance.vevent
                        if keyword.lower() in ve.summary.value.lower():
                            duration = timedelta(hours=1)
                            try:
                                if hasattr(ve, 'dtend'): duration = ve.dtend.value - ve.dtstart.value
                            except: pass
                            ve.dtstart.value = dt
                            ve.dtend.value = dt + duration
                            ev.save()
                            count += 1
                            break
                    if count > 0: break
                except: pass
            return count

        cnt = await run_blocking(_update)
        return f"Rescheduled event to {dt.strftime('%Y-%m-%d %H:%M')}." if cnt else "Event not found."
    except Exception as e: return f"Update error: {e}"

# --- HA Logic ---
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
    
    log.info(f"EXEC HA: {domain}.{service} on {entity_id} | Data: {service_data}")
    
    last_err = None
    for i in range(3):
        try:
            def _post(): return requests.post(url, json=payload, headers=headers, timeout=5.0)
            r = await run_blocking(_post)
            if r.status_code < 400: 
                return f"Successfully executed {domain}.{service} on {entity_id}."
            last_err = f"HTTP {r.status_code}: {r.text}"
        except Exception as e: last_err = str(e)
        await asyncio.sleep(1)
    
    log.error(f"Failed to execute HA command: {last_err}")
    return f"Failed: {last_err}"

async def _handle_single_command(query, user_creds, model=None):
    q_low = query.lower().strip()
    
    # Calendar
    if any(x in q_low for x in ["schedule", "remind", "calendar", "event", "appointment", "meeting"]):
        if any(x in q_low for x in ["list", "what calendars"]): return await tool_calendar_list(user_creds)
        if any(x in q_low for x in ["cancel", "delete", "remove"]): return await tool_calendar_delete(query, user_creds, model or DEFAULT_MODEL)
        if any(x in q_low for x in ["move", "change", "reschedule", "update"]): return await tool_calendar_update(query, user_creds, model or DEFAULT_MODEL)
        
        # Strict Add Trigger: Must have an action verb to avoid accidental 'add' on read queries
        if any(x in q_low for x in ["add", "new", "create", "schedule", "remind", "put"]):
             return await tool_calendar_add(query, user_creds, model or DEFAULT_MODEL)

    # HA
    service = None
    if "turn on" in q_low: service = "turn_on"
    elif "turn off" in q_low: service = "turn_off"
    elif "toggle" in q_low: service = "toggle"
    elif "stop" in q_low: service = "media_stop"
    elif re.search(r"\bplay\b", q_low): service = "play_media"
    
    if not service or not GlobalResources.ha_collection: return None

    clean_q = q_low
    for phrase in ["turn on", "turn off", "toggle", "play", "stop", "the", "please", " on "]:
        clean_q = clean_q.replace(phrase, " ")
    clean_q = clean_q.strip()
    
    try:
        docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(clean_q, k=1))
        if not docs: return None
        
        eid = docs[0].metadata.get("entity_id")
        domain = eid.split(".")[0]
        
        service_data = None
        if service == "play_media" and domain == "media_player":
            parts = q_low.split("play ", 1)
            if len(parts) > 1:
                content = parts[1].split(" on ")[0].strip()
                service_data = {"media_content_id": content, "media_content_type": "music", "enqueue": "play"}
            if await get_entity_state(eid, user_creds) in ["off", "unavailable"]:
                await execute_ha_service("media_player", "turn_on", eid, user_creds)
                await asyncio.sleep(3.0)

        target_dom = "homeassistant" if service in ["turn_on", "turn_off", "toggle"] else domain
        return await execute_ha_service(target_dom, service, eid, user_creds, service_data)
    except Exception as e:
        log.error(f"Error in command execution: {e}")
        return None

async def decompose_command_query(query: str, model: str) -> List[str]:
    # Intelligent splitting for "Turn off X and Y" to ensure both turn off
    if " and " in query.lower():
        if "turn " in query.lower() or "play " in query.lower():
            prompt = (
                f"Split this compound command into standalone commands. Distribute verbs to objects.\n"
                f"Input: '{query}'\nOutput JSON List of strings:"
            )
            try:
                r = await call_ollama_generate(prompt, model=model)
                lst = json.loads(clean_llm_output(r.get("text", "[]")))
                if isinstance(lst, list) and lst: return lst
            except: pass
            return query.split(" and ")
    return [query]

async def try_handle_compound_command(query, user_creds, model):
    cmds = await decompose_command_query(query, model)
    tasks = []
    for c in cmds:
        if isinstance(c, str) and len(c.strip()) > 3:
            tasks.append(_handle_single_command(c, user_creds, model))
    if not tasks: return None
    results = await asyncio.gather(*tasks)
    valid = [r for r in results if r]
    return "\n".join(valid) if valid else None

async def contextualize_query(query, user, model):
    hist = get_history_context(user)
    if not hist: return query
    if len(query.split()) > 4 and any(x in query.lower() for x in ["search", "turn", "play"]): return query
    prompt = f"Rewrite based on history:\n{hist}\nInput: {query}\nRefined:"
    r = await call_ollama_generate(prompt, model)
    return clean_llm_output(r.get("text", query))

async def get_ha_context(user, query=None):
    if not GlobalResources.ha_collection or not HA_URL: return ""
    try:
        docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(query, k=5))
        if not docs: return ""
        
        creds = get_user_creds(user)
        check_ids = [d.metadata.get("entity_id") for d in docs if d.metadata.get("entity_id")]
        
        headers = {"Authorization": f"Bearer {creds['ha_token']}"}
        r = await run_blocking(lambda: requests.get(f"{HA_URL.rstrip('/')}/api/states", headers=headers, timeout=3.0))
        
        if r.status_code == 200:
            all_states = {s["entity_id"]: s for s in r.json()}
            lines = []
            for eid in check_ids:
                if eid in all_states:
                    s = all_states[eid]
                    friendly = s.get("attributes", {}).get("friendly_name", eid)
                    state = s.get("state")
                    lines.append(f"- {friendly} ({eid}) is {state}")
            return "Home Assistant Devices:\n" + "\n".join(lines)
    except Exception as e:
        log.error(f"HA Context Error: {e}")
    return ""

async def get_rag_context(query):
    if not GlobalResources.nextcloud_collection: return ""
    try:
        docs = await run_blocking(lambda: GlobalResources.nextcloud_collection.similarity_search(query, k=3))
        return "Nextcloud Documents:\n" + "\n".join([f"...{d.page_content[:400]}..." for d in docs])
    except: return ""

async def generate_rag_stream(query, user, model, use_openai, format_type) -> AsyncGenerator[str, None]:
    builder = StreamResponseBuilder(model, format_type)
    
    refined = await contextualize_query(query, user, model)
    update_history(user, "user", query)
    
    creds = get_user_creds(user)
    # This now handles Calendar Adds/Delete/List/Update
    action_result = await try_handle_compound_command(refined, creds, model)
    
    if action_result:
        final_msg = f"{action_result}"
        update_history(user, "assistant", final_msg)
        yield builder.chunk(role="assistant")
        yield builder.chunk(content=final_msg)
        yield builder.chunk(finish_reason="stop")
        yield builder.done()
        return

    ha_future = get_ha_context(user, refined)
    nc_future = get_rag_context(refined)
    search_future = tool_web_search(refined)
    
    ha_ctx, nc_ctx, search_ctx = await asyncio.gather(ha_future, nc_future, search_future)

    cal_ctx = ""
    if any(x in refined.lower() for x in ["calendar", "schedule", "meeting", "today", "tomorrow"]):
        if not any(x in refined.lower() for x in ["schedule a", "add", "remind", "cancel", "delete", "move", "reschedule"]):
            cal_ctx = await tool_calendar_read(creds)

    context_block = f"{ha_ctx}\n{nc_ctx}\n{search_ctx}\n{cal_ctx}"
    today = datetime.now().strftime('%Y-%m-%d')
    
    prompt = f"""### INSTRUCTIONS
You are the Unified Home AI.
Current Date: {today}

### CONTEXT DATA
{context_block}

### USER QUERY
{refined}

### RESPONSE GUIDELINES
1. Use CONTEXT DATA if available.
2. If CONTEXT DATA is empty/irrelevant, answer using your general knowledge.
3. Keep it concise.
"""

    yield builder.chunk(role="assistant")
    
    r = None
    if use_openai and openai_client:
        messages = [
            {"role": "system", "content": "You are a helpful home assistant. Use the context provided."},
            {"role": "user", "content": prompt}
        ]
        r = await call_openai_chat(messages, model, stream=True)
    else:
        r = await call_ollama_generate(prompt, model, stream=True)

    full_text = ""
    if "iterable" in r:
        async for c in r["iterable"]():
            if isinstance(c, dict):
                token = c.get("response", "") 
                full_text += token
                yield builder.chunk(content=token)
    elif "text" in r:
        full_text = r["text"]
        yield builder.chunk(content=full_text)
    
    update_history(user, "assistant", full_text)
    yield builder.chunk(finish_reason="stop")
    yield builder.done()
