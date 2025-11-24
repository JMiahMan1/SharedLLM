# app/logic.py
import json
import time
import re
import requests
import asyncio
import caldav
import traceback
from dateparser.search import search_dates
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
    text = re.sub(r'^(Standalone Command|Command|Output|Refined|Result):', '', text.strip(), flags=re.IGNORECASE)
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
    is_ha_cmd = any(x in q_low for x in ["turn on", "turn off", "toggle", "dim", "status of", "state of", "play", "stop", "schedule", "remind"])
    is_explicit = any(x in q_low for x in ["search", "find", "who is", "what is", "google", "tell me about", "linux", "price", "cost", "kernel"])
    
    if is_ha_cmd and not is_explicit: 
        return "" 

    log.info(f"Executing Web Search for: {query}")
    
    parsed = urlparse(WHOOGLE_URL)
    if "search" in parsed.path:
        search_endpoint = WHOOGLE_URL
    else:
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
        browser_url = f"{WHOOGLE_URL.rstrip('/')}/search?q={query}"
        results = await _scrape_with_playwright(browser_url)
        if results:
             return "### Real-time Web Search Results (Playwright):\n" + "\n\n".join(results)

    return "System Notification: Web search performed but returned no results or failed."

# --- Enhanced Tool: Calendar (Read/Write) ---
async def tool_calendar(query: str = "") -> str:
    if not NEXTCLOUD_URL: return ""
    try:
        client = caldav.DAVClient(url=f"{NEXTCLOUD_URL.rstrip('/')}/remote.php/dav", username=NEXTCLOUD_USER, password=NEXTCLOUD_PASS)
        now = datetime.now()
        
        # --- WRITE Intent ---
        if any(x in query.lower() for x in ["add", "create", "schedule", "new event", "remind me to"]):
            log.info(f"Detected Calendar Write Intent: {query}")
            
            # 1. Explicit Calendar Routing logic
            target_cal_name = None
            cal_match = re.search(r"(?:on|to)\s+calendar\s+([\w\s]+)", query, re.IGNORECASE)
            if cal_match:
                target_cal_name = cal_match.group(1).strip()
                # Remove the routing phrase from the query so it doesn't end up in the title
                query_for_parsing = re.sub(r"(?:on|to)\s+calendar\s+[\w\s]+", "", query, flags=re.IGNORECASE)
                log.info(f"Explicit Calendar Requested: '{target_cal_name}'")
            else:
                query_for_parsing = query

            try:
                # Force relative base to now to avoid caching issues
                time_matches = search_dates(
                    query_for_parsing, 
                    languages=['en'], 
                    settings={'PREFER_DATES_FROM': 'future', 'RELATIVE_BASE': datetime.now()}
                )
            except Exception as e:
                log.error(f"Dateparser Error: {e}")
                return "System Error: Failed to parse date."
            
            if not time_matches:
                # Fallback check for "tomorrow"
                if "tomorrow" in query_for_parsing.lower():
                     start_dt = datetime.now() + timedelta(days=1)
                     start_dt = start_dt.replace(hour=12, minute=0, second=0, microsecond=0)
                     matched_text = "tomorrow"
                else:
                    return "Calendar: I understood you want to schedule an event, but I couldn't understand the date or time. Please specify when."
            else:
                matched_text, start_dt = time_matches[-1]
            
            end_dt = start_dt + timedelta(hours=1)
            
            # Cleanup title
            title = query_for_parsing
            for phrase in [matched_text, "add", "create", "schedule", "event", "calendar", "remind me to", " on ", " at "]:
                title = re.sub(phrase, " ", title, flags=re.IGNORECASE)
            title = title.strip() or "New Event"
            title = title[0].upper() + title[1:]
            
            def _create_event():
                try:
                    calendars = client.principal().calendars()
                    if not calendars: return "No calendar found."
                    
                    final_cal = None
                    
                    # A. Explicit Routing
                    if target_cal_name:
                        for cal in calendars:
                            if target_cal_name.lower() in (cal.name or "").lower():
                                final_cal = cal
                                break
                    
                    # B. Smart Defaults (if explicit failed or wasn't asked)
                    if not final_cal:
                        # Priority 1: "Personal"
                        for cal in calendars:
                            if "personal" in (cal.name or "").lower():
                                final_cal = cal
                                break
                        
                        # Priority 2: Family/User Name (excluding birthdays)
                        if not final_cal:
                            for cal in calendars:
                                name = (cal.name or "").lower()
                                url_str = str(cal.url).lower()
                                if "contact_birthdays" in url_str or "birthdays" in name: continue
                                if "family" in name or NEXTCLOUD_USER.lower() in name:
                                    final_cal = cal
                                    break
                        
                        # Priority 3: First available writable
                        if not final_cal: 
                            final_cal = calendars[0]

                    final_cal.save_event(dtstart=start_dt, dtend=end_dt, summary=title)
                    return f"Successfully scheduled '{title}' for {start_dt.strftime('%Y-%m-%d %H:%M')} on calendar '{final_cal.name}'."
                except Exception as e:
                    return f"Error creating event: {str(e)}"

            return await run_blocking(_create_event)

        # --- READ Intent ---
        start_of_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_of_day + timedelta(days=7)
        
        def _fetch():
            found_events = []
            try:
                calendars = client.principal().calendars()
                if not calendars: return []
                for cal in calendars:
                    try:
                        events = cal.search(start=start_of_day, end=end_date, event=True, expand=True)
                        for ev in events:
                            if hasattr(ev.vobject_instance, 'vevent'):
                                vevent = ev.vobject_instance.vevent
                                start_dt = vevent.dtstart.value
                                summary = vevent.summary.value
                                time_str = start_dt.strftime("%Y-%m-%d %H:%M") if isinstance(start_dt, datetime) else f"{start_dt} (All Day)"
                                found_events.append(f"- [{time_str}] {summary}")
                    except: pass 
            except Exception as e:
                log.error(f"Calendar fetch error: {e}")
            found_events.sort()
            return found_events
        
        results = await run_blocking(_fetch)
        return "Calendar Events (Next 7 Days):\n" + "\n".join(results) if results else "Calendar: No upcoming events found."
    except Exception as e: 
        log.error(f"Calendar Error: {e}")
        return ""

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
            
            if r.status_code == 500:
                log.error(f"HA Server Error (500) for {domain}.{service}. Device might be flaky.")
                return "Command sent, but device returned an internal error."
                
            if r.status_code < 400: 
                return f"Successfully executed {domain}.{service} on {entity_id}."
            last_err = f"HTTP {r.status_code}: {r.text}"
        except Exception as e: last_err = str(e)
        await asyncio.sleep(1)
    
    log.error(f"Failed to execute HA command: {last_err}")
    return f"Failed: {last_err}"

async def _handle_timer_command(query, user_creds):
    q_low = query.lower()
    if "stop" in q_low or "cancel" in q_low:
        return await execute_ha_service("timer", "cancel", "timer.default", user_creds)
    
    duration = "00:10:00" 
    mins = re.search(r'(\d+)\s*min', q_low)
    secs = re.search(r'(\d+)\s*sec', q_low)
    hours = re.search(r'(\d+)\s*hour', q_low)
    
    total_seconds = 0
    if mins: total_seconds += int(mins.group(1)) * 60
    if secs: total_seconds += int(secs.group(1))
    if hours: total_seconds += int(hours.group(1)) * 3600
    
    if total_seconds > 0:
        duration = str(timedelta(seconds=total_seconds))
        
    return await execute_ha_service("timer", "start", "timer.default", user_creds, {"duration": duration})

async def _handle_single_command(query, user_creds):
    q_low = query.lower().strip()
    
    # 1. Check for specific tools (Timers, Calendar)
    if "timer" in q_low:
        return await _handle_timer_command(query, user_creds)

    if any(x in q_low for x in ["schedule", "remind me to", "add to calendar", "create event"]):
        return await tool_calendar(query)

    # 2. Parse Action
    service, service_data = None, None
    if "turn on" in q_low: service = "turn_on"
    elif "turn off" in q_low: service = "turn_off"
    elif "toggle" in q_low: service = "toggle"
    elif "stop" in q_low: service = "media_stop"
    elif re.search(r"\bplay\b", q_low):
        service = "play_media"
        parts = q_low.split("play ", 1)
        if len(parts) > 1:
            content_part = parts[1].split(" on ")[0].strip().strip('"').strip("'")
            if content_part:
                service_data = {"media_content_id": content_part, "media_content_type": "music", "enqueue": "play"}
    
    if not service: return None

    clean_q = q_low
    for phrase in ["turn on", "turn off", "toggle", "play", "stop", "the", "please", " on "]:
        clean_q = clean_q.replace(phrase, " ")
    clean_q = clean_q.strip()
    
    if not clean_q or not GlobalResources.ha_collection: return None

    try:
        # 3. Find Candidate Entities
        docs = await run_blocking(lambda: GlobalResources.ha_collection.similarity_search(clean_q, k=3))
        if not docs: return None
        
        candidates = []
        for d in docs:
            eid = d.metadata.get("entity_id")
            if not eid: continue
            score = 0
            
            # Context-Aware Scoring
            if service == "play_media":
                if "_chrome" in eid or "_cast" in eid or "google" in eid: score += 5
            else:
                if "_chrome" in eid or "_cast" in eid: score -= 3
                if "remote" in eid and "remote" not in clean_q: score -= 2
            
            if clean_q in d.page_content.lower(): score += 2
            
            candidates.append((score, eid, d))
            
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_eid = candidates[0][1]
        
        log.info(f"Selected entity {best_eid} for '{clean_q}' (Service: {service})")
        
        domain = best_eid.split(".")[0]
        target_dom, target_svc = domain, service
        
        if service in ["turn_on", "turn_off", "toggle"]: 
            target_dom = "homeassistant"
        
        if domain == "media_player":
            target_dom = "media_player"
            if service == "play_media":
                state = await get_entity_state(best_eid, user_creds)
                if state in ["off", "unavailable"]:
                    await execute_ha_service("media_player", "turn_on", best_eid, user_creds)
                    await asyncio.sleep(3.0)

        return await execute_ha_service(target_dom, target_svc, best_eid, user_creds, service_data)
        
    except Exception as e:
        log.error(f"Error in command execution: {e}")
        return None

async def decompose_command_query(query: str, model: str) -> List[str]:
    # Verb Distribution for "Turn off X and Y"
    if " and " in query.lower():
        parts = query.split(" and ")
        if len(parts) == 2:
            p1 = parts[0].strip()
            p2 = parts[1].strip()
            verbs = ["turn on", "turn off", "toggle", "play", "stop", "dim"]
            p2_has_verb = any(v in p2.lower() for v in verbs)
            if not p2_has_verb:
                for v in verbs:
                    if v in p1.lower():
                        new_p2 = f"{v} {p2}"
                        log.info(f"Verb Distribution: '{p2}' -> '{new_p2}' (inherited from '{p1}')")
                        parts[1] = new_p2
                        break
        return parts
        
    return [query]

async def try_handle_compound_command(query, user_creds, model):
    cmds = await decompose_command_query(query, model)
    tasks = []
    
    for c in cmds:
        if isinstance(c, str) and len(c.strip()) > 3:
            tasks.append(_handle_single_command(c, user_creds))
            
    if not tasks: return None
    
    results = await asyncio.gather(*tasks)
    valid_results = [r for r in results if r is not None]
    
    if valid_results:
        return "\n".join(valid_results)
    
    return None

async def contextualize_query(query, user, model):
    hist = get_history_context(user)
    if not hist: return query
    
    if len(query.split()) > 4 and any(x in query.lower() for x in ["search", "turn", "play", "timer", "schedule"]):
        return query

    prompt = (
        f"History:\n{hist}\nInput: {query}\n"
        "Task: Rephrase 'Input' to be a standalone question using context from 'History'. "
        "If 'Input' is already clear, output it unchanged. "
        "Do NOT answer the question. Output ONLY the rephrased text."
    )
    
    log.info(f"Contextualizing: '{query}' with History length {len(hist)}")
    r = await call_ollama_generate(prompt, model)
    refined = clean_llm_output(r.get("text", query))
    log.info(f"Refined Query: '{refined}'")
    
    return refined

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
    if any(x in refined.lower() for x in ["calendar", "schedule", "meeting", "today", "tomorrow", "plan"]):
        cal_ctx = await tool_calendar(refined)

    context_block = f"{ha_ctx}\n{nc_ctx}\n{search_ctx}\n{cal_ctx}"
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    prompt = f"""### INSTRUCTIONS
You are the Unified Home AI.
Current Date/Time: {today}

### CONTEXT DATA
{context_block}

### USER QUERY
{refined}

### RESPONSE GUIDELINES
1. Use CONTEXT DATA if available.
2. If the user asked to create an event or timer and you see "Successfully scheduled/executed" in the context or previous turn, confirm it.
3. If CONTEXT DATA is empty/irrelevant, answer using your general knowledge.
4. Keep it concise.
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
