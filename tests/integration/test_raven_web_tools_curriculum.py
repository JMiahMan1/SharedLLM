"""Teaching curriculum: Raven's web toolset (WebSearch / WebRead / WebScraper).

Companion to ``test_raven_python_basics.py``: that file teaches Raven the
coding tools; this one teaches the *web* tools and, crucially, the NEW
``WebScraperRequest`` tool (commit 3d5e605d).

The ladder (each mission is its own test so a failure stops the run):

  1. ``web-search-basics``    — use WebSearchRequest, save titles+URLs.
  2. ``web-read-basics``      — use WebReadRequest, extract title+paragraphs.
  3. ``web-scraper-basics``   — use WebScraperRequest (NEW tool), save prices.
  4. ``web-tool-selection``   — pick the CHEAPEST sufficient tool; justify.
  5. ``web-chaining-memory``  — chain search→read AND apply past lessons.

Each mission:
  * creates a dedicated workspace (exact id given in the prompt),
  * writes a concrete artifact file the test independently verifies,
  * APPENDS a dated lesson to ``raven_memory.md`` (Raven's journal; the
    gateway's post-mission reflection also appends its own entry),
  * is dispatched through the normal mission pipeline and polled to a
    terminal state. The test never executes the mission itself.

Requires LIVE_E2E=1 and a reachable gateway (GATEWAY_URL); no GitHub
token needed (these are research missions, not code missions).
"""
import os
import time

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "RAVEN_SECURE_2026")
RAVEN_API_KEY = os.getenv("RAVEN_API_KEY", "")

CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "1800"))
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "20"))


def _live_enabled() -> bool:
    return bool(os.getenv("LIVE_E2E"))


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven web-tools teaching e2e requires LIVE_E2E=1",
    ),
]


# ---------------------------------------------------------------------------
# Live mission dispatch + polling (mirrors test_raven_python_basics.py).
# ---------------------------------------------------------------------------
def _chat_auth_headers() -> dict:
    if RAVEN_API_KEY:
        return {"Authorization": f"Bearer {RAVEN_API_KEY}"}
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _ws_headers() -> dict:
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _list_missions() -> list[dict]:
    with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
        resp = c.get(f"{GATEWAY_URL}/api/raven/missions")
        if resp.status_code != 200:
            return []
        data = resp.json()
    if isinstance(data, list):
        return data
    for key in ("missions", "items", "results"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _recover_mission_id(query: str) -> int | None:
    marker = query.strip()[:160]
    best: int | None = None
    for m in _list_missions():
        proposed = (m.get("proposed_mission") or "").strip()
        if proposed[:160] == marker:
            mid = m.get("id")
            if isinstance(mid, int) and (best is None or mid > best):
                best = mid
    return best


def _prune_stale_missions(query: str) -> None:
    """Delete queued missions with the same prompt from previous runs/retries
    so they don't pile up in the singleton worker queue ahead of the one we
    are about to dispatch (each mission takes ~20-30 min to execute)."""
    marker = query.strip()[:160]
    for m in _list_missions():
        proposed = (m.get("proposed_mission") or "").strip()
        if proposed[:160] == marker and m.get("status") in ("queued", "pending"):
            mid = m.get("id")
            if isinstance(mid, int):
                try:
                    with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
                        c.delete(f"{GATEWAY_URL}/api/raven/missions/{mid}")
                except Exception:
                    pass


def _chat_submit(query: str) -> int:
    body = {"query": query, "coding_model": _live_coding_model()}
    _prune_stale_missions(query)
    last_err: str | None = None
    for _ in range(3):
        try:
            with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
                resp = c.post(f"{GATEWAY_URL}/api/raven/missions", json=body)
                if resp.status_code in (200, 201, 202):
                    data = resp.json()
                    mission = data.get("mission") or {}
                    mid = mission.get("id") or data.get("mission_id")
                    if mid is not None:
                        return int(mid)
            last_err = f"status {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(3)
    recovered = _recover_mission_id(query)
    assert recovered is not None, f"mission submit failed ({last_err}) and none in queue"
    return int(recovered)


def _chat_wait(mission_id: int) -> dict:
    """Wait for a mission, tolerating queue delay: the overall deadline counts
    from dispatch, but once the mission starts EXECUTING it gets a fresh
    CHAT_TIMEOUT (the worker is a singleton, so earlier missions can consume
    the whole initial window before ours even starts)."""
    with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
        deadline = time.time() + CHAT_TIMEOUT
        exec_deadline: float | None = None
        while True:
            now = time.time()
            if exec_deadline is not None and now > exec_deadline:
                return {"status": "TIMEOUT"}
            if exec_deadline is None and now > deadline:
                return {"status": "TIMEOUT"}
            try:
                resp = c.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") in ("completed", "failed", "dismissed"):
                        return data
                    if data.get("status") == "executing" and exec_deadline is None:
                        exec_deadline = now + CHAT_TIMEOUT
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)


def _live_coding_model() -> str:
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
            r = c.get(f"{GATEWAY_URL}/api/config")
            if r.status_code == 200:
                m = (r.json() or {}).get("config", {}).get("coding_model")
                if m:
                    return str(m)
    except Exception:
        pass
    return ""


def _delete_workspace_if_exists(ws_id: str) -> None:
    try:
        with httpx.Client(headers=_ws_headers(), timeout=30.0) as c:
            c.delete(f"{GATEWAY_URL}/api/workspaces/{ws_id}")
    except Exception:
        pass


def _read_workspace_file(ws_id: str, path: str) -> str | None:
    """Read a file from the mission workspace via the gateway proxy (admin)."""
    try:
        with httpx.Client(headers=_ws_headers(), timeout=30.0) as c:
            resp = c.post(
                f"{GATEWAY_URL}/api/workspaces/files/read",
                json={"workspace_id": ws_id, "relative_path": path},
            )
            if resp.status_code == 200:
                return (resp.json() or {}).get("content")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Curriculum ladder. Each test = one mission; run in file order.
# ---------------------------------------------------------------------------
def _run_mission(
    mission_id: str,
    ws_id: str,
    prompt: str,
    artifact: str,
    artifact_check,
    memory_keywords: tuple[str, ...],
    expect_desc: str,
) -> None:
    _delete_workspace_if_exists(ws_id)

    mid = _chat_submit(prompt)
    result = _chat_wait(mid)
    assert result.get("status") == "completed", (
        f"mission {mission_id} did not complete: {result.get('status')}\n"
        f"result: {(result.get('result') or '')[:500]}"
    )

    # Prefer the workspace id the mission actually used.
    used_ws = (result.get("workspace_id") or ws_id)

    # Double-check 1: the required artifact exists and satisfies its contract.
    content = _read_workspace_file(used_ws, artifact)
    assert content, f"[{mission_id}] {artifact} missing from workspace {used_ws}"
    assert artifact_check(content), (
        f"[{mission_id}] {artifact} did not satisfy: {expect_desc}\n"
        f"content:\n{content[-800:]}"
    )

    # Double-check 2: the lesson was logged to Raven's per-task memory.
    memory = _read_workspace_file(used_ws, "raven_memory.md")
    assert memory, f"[{mission_id}] raven_memory.md missing from workspace {used_ws}"
    assert any(kw in memory.lower() for kw in memory_keywords), (
        f"[{mission_id}] raven_memory.md does not record the web-tool lesson "
        f"(expected any of {memory_keywords}):\n{memory[:800]}"
    )


def test_raven_web_search_basics():
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-web-search'. "
        "Use your WebSearchRequest tool (NOT WebScraperRequest — search is the "
        "right tool here) to search for 'Raspberry Pi 5 current price'. "
        "From the results, save a markdown file `search_results.md` in the "
        "workspace listing the top 5 results: one line per result with the "
        "title, a short snippet, and the URL. Then APPEND a dated lesson to "
        "`raven_memory.md` describing what WebSearchRequest returns and how "
        "you saved the results. You do not need a GitHub repo for this task."
    )
    _run_mission(
        "web-search-basics",
        "raven-web-search",
        prompt,
        "search_results.md",
        lambda c: c.lower().count("http") >= 3,
        ("websearch", "search"),
        "at least 3 URLs listed in search_results.md",
    )


def test_raven_web_read_basics():
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-web-read'. "
        "Use your WebReadRequest tool to read https://en.wikipedia.org/wiki/Raspberry_Pi. "
        "Note that WebReadRequest truncates long pages around 15000 characters, "
        "so capture the title (first heading) and the opening paragraphs early. "
        "Save a markdown file `read_summary.md` in the workspace with the page "
        "title and the first paragraph verbatim (as close as possible). "
        "Then APPEND a dated lesson to `raven_memory.md` describing what "
        "WebReadRequest returns and the truncation behavior. No GitHub repo needed."
    )
    _run_mission(
        "web-read-basics",
        "raven-web-read",
        prompt,
        "read_summary.md",
        lambda c: "Raspberry Pi" in c and len(c) > 40,
        ("webread", "read request"),
        "read_summary.md names the page and contains the opening text",
    )


def test_raven_web_scraper_basics():
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-web-scraper'. "
        "Use your WebScraperRequest tool (this is the NEW specialized tool for "
        "extracting structured listing data) with query 'logitech mx master 3s' "
        "and the named source 'ebay'. The tool returns structured items with "
        "prices and specs. Save a markdown file `prices.md` in the workspace "
        "listing the top 3 items with their prices (one per line, e.g. "
        "'Item: <name> — $<price>'). If the scraper errors, retry once with a "
        "different named source such as 'google_shopping' before giving up. "
        "Then APPEND a dated lesson to `raven_memory.md` describing what "
        "WebScraperRequest returns (structured prices/specs) and any "
        "gotchas you hit. No GitHub repo needed."
    )
    _run_mission(
        "web-scraper-basics",
        "raven-web-scraper",
        prompt,
        "prices.md",
        lambda c: "$" in c and len(c.splitlines()) >= 3,
        ("scraper", "scrape", "price"),
        "prices.md lists at least 3 item/price lines with dollar amounts",
    )


def test_raven_web_tool_selection():
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-web-tool-choice'. "
        "Task: find who is the current (2026) US President and his vice president. "
        "You have three web tools with very different costs: WebSearchRequest "
        "(cheap, fast — returns titles/URLs/snippets), WebReadRequest (medium — "
        "loads a full page), and WebScraperRequest (expensive — headless browser "
        "scraping, for structured prices). CHOOSE THE CHEAPEST tool that can "
        "complete this task, and do not use any heavier tool. Save a markdown "
        "file `tool_choice.md` in the workspace that states exactly which tool "
        "you used, the answer you found, and one sentence of reasoning for why "
        "that tool was sufficient. Then APPEND a dated lesson to "
        "`raven_memory.md` about choosing the cheapest sufficient web tool. "
        "No GitHub repo needed."
    )
    _run_mission(
        "web-tool-selection",
        "raven-web-tool-choice",
        prompt,
        "tool_choice.md",
        lambda c: "websearch" in c.lower() and "president" in c.lower(),
        ("cheap", "sufficient", "selection", "websearch"),
        "tool_choice.md names WebSearchRequest (or a justified lighter tool) and the answer",
    )


def test_raven_web_chaining_memory():
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-web-chain'. "
        "BEFORE starting, recall your past lessons: your system prompt is "
        "augmented with [SYSTEM_LEARNINGS — PAST LESSONS] from your previous "
        "missions — READ those and APPLY them here. "
        "Task: research the Raspberry Pi 5: use WebSearchRequest to find 2-3 "
        "reputable articles about its release and pricing, then use "
        "WebReadRequest to read the most promising one and extract concrete "
        "facts (release date, price). Chain the tools: search first, then read. "
        "Save `report.md` in the workspace with: the sources you used (URLs), "
        "3 concrete facts, and a short 'Lessons applied' section naming which "
        "past lessons (from your web missions) you applied in this task. "
        "Then APPEND a dated lesson to `raven_memory.md` about chaining "
        "websearch -> webread and applying past lessons. No GitHub repo needed."
    )
    _run_mission(
        "web-chaining-memory",
        "raven-web-chain",
        prompt,
        "report.md",
        lambda c: "http" in c.lower() and "lessons applied" in c.lower(),
        ("lesson", "chain", "applied"),
        "report.md has URLs and a 'Lessons applied' section naming past lessons",
    )


def test_raven_web_challenge_fallback():
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-challenge'. "
        "Lesson to learn: some sites (e.g. raspberrypi.com) are behind a "
        "Cloudflare challenge that WebReadRequest may not bypass. "
        "Task: use WebReadRequest to read "
        "https://www.raspberrypi.com/products/raspberry-pi-5/. "
        "If the result reports a Cloudflare challenge (or 'Just a moment' / "
        "empty anti-bot page), do NOT give up — switch to your "
        "WebScraperRequest tool (it uses the camoufox anti-bot browser) with "
        "that product URL and extract the product name, a headline/description, "
        "and the price if visible. Save `challenge_fallback.md` in the "
        "workspace containing: which tool succeeded, the product name, and "
        "whatever product info you could extract. Then APPEND a dated lesson to "
        "`raven_memory.md` about falling back when a site is behind a "
        "Cloudflare challenge. No GitHub repo needed."
    )
    # WebReadRequest can now auto-bypass Cloudflare via its internal camoufox
    # retry (challenge wait-solve), so the report may legitimately document a
    # successful WebReadRequest with a camoufox bypass instead of the scraper.
    def _challenge_resolved(content: str) -> bool:
        lower = content.lower()
        return (
            "raspberry" in lower
            and ("webscraper" in lower or "camoufox" in lower or "bypass" in lower)
            and len(content) > 80
        )
    _run_mission(
        "web-challenge-fallback",
        "raven-challenge",
        prompt,
        "challenge_fallback.md",
        _challenge_resolved,
        ("scraper", "challenge", "cloudflare"),
        "challenge_fallback.md names a working tool (WebScraperRequest or camoufox bypass) and extracted Raspberry Pi product info",
    )


def test_raven_web_program_mission():
    """End-to-end coding from web research: search example code, build a real
    (web) program, test it with tests that build on one another, and prove it
    actually runs."""
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-web-program'. "
        "Task: build a real, runnable web program from example code you find "
        "on the web. Steps: "
        "(1) Use WebSearchRequest (2-3 queries) to find example code for a "
        "simple in-memory REST API using ONLY the Python standard library "
        "(http.server / BaseHTTPRequestHandler — no external packages). "
        "(2) Create the workspace and write `app.py` implementing: "
        "POST /items (JSON body {name}, returns {id, name}), GET /items "
        "(list all), GET /items/<id> (one item or 404). Use an in-memory "
        "store with a counter for ids. "
        "(3) Write tests that BUILD ON ONE ANOTHER using unittest "
        "(stdlib): a setUpClass/class attribute starts the server once; "
        "test_01 checks the server starts and /items returns an empty list; "
        "test_02 POSTs an item and asserts the response; test_03 GETs /items "
        "and asserts the item created in test_02 is present; test_04 GETs "
        "/items/<id> from test_02 and asserts it. Run them with "
        "`python -m unittest discover -s tests -v` and fix until all pass. "
        "(4) PROVE it runs: start the server in the background in the "
        "workspace shell, curl POST /items then GET /items, capture the real "
        "responses, then stop the server. "
        "(5) Write PROGRAM_REPORT.md containing: the exact URLs where you "
        "found the example code, what you adapted, the test output showing "
        "all tests passing, and the curl proof output. "
        "(6) APPEND a dated lesson to raven_memory.md about building from "
        "web-researched example code (format: RULE / ROOT CAUSE / OUTCOME / "
        "CONFIDENCE). No GitHub repo needed."
    )

    def _program_report_ok(content: str) -> bool:
        lower = content.lower()
        return (
            "http" in lower
            and any(kw in lower for kw in ("pass", "ok", "ran", "test"))
            and len(content) > 300
        )

    _run_mission(
        "web-program-mission",
        "raven-web-program",
        prompt,
        "PROGRAM_REPORT.md",
        _program_report_ok,
        ("program", "web", "test"),
        "PROGRAM_REPORT.md documents web-researched example code, passing tests, and a live run",
    )


def test_raven_memory_usage():
    """Prove Raven actually APPLIES a stored lesson and cites it as
    `Apply: [id]` in its plan — the measurable memory-usage signal — and
    that lesson documents stay compact (JSON) so they do not waste the
    context budget."""
    prompt = (
        "Raven, create a dedicated workspace with id 'raven-memory-check'. "
        "This mission trains MEMORY USAGE. Your prompt includes a "
        "[SYSTEM_LEARNINGS — PAST LESSONS] block: each line is "
        "`- [id][outcome] (conf) rule`. Pick ONE lesson relevant to this "
        "task (e.g. the one about WebSearchRequest being the cheapest "
        "sufficient tool for straightforward facts). When you apply it in "
        "your plan, cite it EXACTLY as `Apply: [id]` (copy the id verbatim "
        "from the brackets). Task: use WebSearchRequest to answer: 'In what "
        "year was the Raspberry Pi 5 released?' Save `answer.md` containing "
        "the answer, the exact `Apply: [id]` citation line, and which lesson "
        "you applied. No GitHub repo needed."
    )
    ws_id = "raven-memory-check"
    _delete_workspace_if_exists(ws_id)

    mid = _chat_submit(prompt)
    result = _chat_wait(mid)
    assert result.get("status") == "completed", (
        f"mission memory-usage did not complete: {result.get('status')}\n"
        f"result: {(result.get('result') or '')[:500]}"
    )

    # Proof 1: the mission's own answer documents an applied lesson citation.
    used_ws = (result.get("workspace_id") or ws_id)
    content = _read_workspace_file(used_ws, "answer.md")
    assert content, f"[memory-usage] answer.md missing from workspace {used_ws}"
    lower = content.lower()
    assert "apply:" in lower and "lesson-" in lower, (
        f"[memory-usage] answer.md does not cite an applied lesson:\n{content[-800:]}"
    )

    # Proof 2: the mission output_log contains the `Apply: [id]` citation in
    # the plan (the plan is where the protocol requires the citation).
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
            resp = c.get(f"{GATEWAY_URL}/api/raven/missions/{mid}")
            detail = resp.json() if resp.status_code == 200 else {}
        output_log = detail.get("output_log") or ""
        assert "Apply:" in output_log, (
            f"[memory-usage] output_log has no Apply: citation (plan not recorded):\n{output_log[-600:]}"
        )
    except Exception as e:
        raise AssertionError(f"[memory-usage] could not read mission output_log: {e}")

    # Proof 3 (context budget): lesson documents in system_learnings are
    # compact JSON (id + rule), not multi-KB prose — the renderer injects one
    # short line per lesson and respects the 15k-char mission budget.
    rag_url = GATEWAY_URL.replace(":8080", ":8004")
    try:
        with httpx.Client(headers=_ws_headers(), timeout=30.0) as c:
            resp = c.post(
                f"{rag_url}/rag/search",
                json={
                    "collection_name": "system_learnings",
                    "query": "web search lesson",
                    "user_id": "default",
                    "k": 10,
                },
            )
            hits = (resp.json() or {}).get("results", []) if resp.status_code == 200 else []
        assert hits, "[memory-usage] no system_learnings hits found for budget check"
        for h in hits:
            content_hit = h.get("content") or ""
            assert content_hit.strip().startswith("{"), (
                "[memory-usage] lesson not stored as compact JSON (context bloat):\n"
                f"{content_hit[:200]}"
            )
            assert len(content_hit) <= 1500, (
                f"[memory-usage] lesson document too large ({len(content_hit)} chars):\n"
                f"{content_hit[:200]}"
            )
    except AssertionError:
        raise
    except Exception as e:
        raise AssertionError(f"[memory-usage] RAG budget check failed: {e}")
