
import asyncio
import logging
import sys
import os
import re

# Adjust path to find modules if run from root
sys.path.append(sys.path[0] + "/app")

# Mocking settings/log if running outside full app context
from settings import log, GlobalResources, get_user_creds

# Import Tools
from logic.web_search import tool_web_search
from logic.note_ops import tool_note_read, tool_note_add, tool_note_delete
from logic.calendar_ops import tool_calendar_list
from logic.timer_ops import tool_timer_list, tool_timer_add, tool_timer_delete

# Attempt to import pipeline for RAG simulation
try:
    from logic.pipeline import generate_rag_stream
    RAG_AVAILABLE = True
except ImportError:
    RAG_AVAILABLE = False
    print("Warning: RAG Pipeline could not be imported. RAG tests will be skipped.")

# Scenario Definitions
SCENARIOS = [
    # --- WEB SEARCH ---
    {
        "name": "General Knowledge (Simple)",
        "query": "Who is the Prime Minister of Canada?",
        "expected_keywords": ["Trudeau", "Justin"],
        "type": "web",
        "tool": tool_web_search
    },
    {
        "name": "General Knowledge (Historical)",
        "query": "When was the Eiffel Tower constructed?",
        "expected_keywords": ["1887", "1889", "Gustave"],
        "type": "web",
        "tool": tool_web_search
    },
    {
        "name": "Current Event / Tech (Freshness)",
        "query": "Current stable version of Python",
        "expected_keywords": ["3.13", "3.12", "3.11", "Python"],
        "type": "web",
        "tool": tool_web_search
    },
    {
        "name": "Video Search (YouTube)",
        "query": "site:youtube.com cute funny cats",
        "expected_keywords": ["youtube.com", "watch"],
        "expected_url_pattern": r"https?://(www\.)?youtube\.com/watch\?v=",
        "type": "web",
        "tool": tool_web_search
    },
    {
        "name": "Video Search (Rumble)",
        "query": "site:rumble.com trending news",
        "expected_keywords": ["rumble.com"],
        "type": "web",
        "tool": tool_web_search
    },
    {
        "name": "Niche/Obscure Query",
        "query": "What is the airspeed velocity of an unladen swallow python code",
        "expected_keywords": ["Monty", "Python", "European", "African"],
        "type": "web",
        "tool": tool_web_search
    },
    {
        "name": "Edge Case (Gibberish)",
        "query": "asdfjkl;1234@#$%",
        "expected_keywords": [],
        "type": "web_edge",
        "tool": tool_web_search
    },
    
    # --- INTERNAL DATA (Notes) ---
    {
        "name": "Setup: Create Test Note",
        "query": "Create note TestNote123 Content: Verification Data",
        "type": "setup_note",
        "tool": tool_note_add,
        "args": ["TestNote123", "Verification Data", "Test"]
    },
    {
        "name": "Search: Read Note",
        "query": "Read note TestNote123",
        "expected_keywords": ["Verification Data"],
        "type": "internal_note",
        "tool": tool_note_read,
        "args": ["TestNote123"]
    },
    {
        "name": "Cleanup: Delete Test Note",
        "query": "Delete note TestNote123",
        "type": "cleanup_note",
        "tool": tool_note_delete,
        "args": ["TestNote123"]
    },

    # --- INTERNAL DATA (Calendar) ---
    {
        "name": "Search: List Calendars",
        "query": "List calendars",
        "expected_keywords": ["Calendar"], # Assuming at least one calendar exists
        "type": "internal_cal",
        "tool": tool_calendar_list,
        "requires_creds": True
    },

    # --- INTERNAL DATA (Timers) ---
    {
        "name": "Setup: Create Timer",
        "query": "Set timer for 5 minutes",
        "type": "setup_timer",
        "tool": tool_timer_add,
        "args": ["5 minutes"],
        "requires_creds": True
    },
    {
        "name": "Search: List Timers",
        "query": "Show timers",
        "expected_keywords": ["5 minutes", "remaining"],
        "type": "internal_timer",
        "tool": tool_timer_list,
        "requires_creds": True
    },
    {
        "name": "Cleanup: Delete Timer",
        "query": "Delete timer", # This might require logic to find ID? Simulating exact match?
        # Actually tool_timer_delete needs a query or ID.
        # Timer ops tool_timer_delete takes query.
        "type": "cleanup_timer",
        "tool": tool_timer_delete,
        "args": ["all"], # or '5 minutes'
        "requires_creds": True
    }
]

async def run_tests():
    print(f"Starting Comprehensive Search Verification Suite ({len(SCENARIOS)} scenarios)...\n")
    results = []
    
    # Initialize basic creds if needed
    user = "jeremiah" # Default user
    creds = {"nc_user": os.getenv("NEXTCLOUD_USER"), "nc_pass": os.getenv("NEXTCLOUD_PASS"), 
             "ha_url": os.getenv("HA_URL"), "ha_token": os.getenv("HA_TOKEN")}
    
    redis_client = GlobalResources.redis_client
    
    for i, scenario in enumerate(SCENARIOS):
        print(f"--- Scenario {i+1}: {scenario['name']} ---")
        
        try:
            start_ts = asyncio.get_event_loop().time()
            output = ""
            
            tool = scenario.get("tool")
            args = scenario.get("args", [])
            
            # Execute Tool based on signature requirements
            if scenario.get("type") == "web" or scenario.get("type") == "web_edge":
                 output = await tool(scenario['query'])
            
            elif scenario.get("requires_creds"):
                 # Tools requiring (creds, redis) or similar
                 if tool == tool_calendar_list:
                     output = await tool(creds, redis_client)
                 elif tool == tool_timer_list:
                     output = await tool(creds, redis_client)
                 elif tool == tool_timer_add:
                     # tool_timer_add(query, creds, model, redis...)
                     output = str(await tool(args[0], creds, "test-model", redis_client))
                 elif tool == tool_timer_delete:
                     output = str(await tool(args[0], creds, redis_client))
            
            elif scenario.get("type") in ["setup_note", "internal_note", "cleanup_note"]:
                 output = str(await tool(*args))
            
            duration = asyncio.get_event_loop().time() - start_ts
            
            # Analyze Result
            passed = True
            failure_reason = ""
            
            # 1. Check for empty/failure response (Soft check for setup/cleanup)
            if not output and "setup" not in scenario["type"] and "cleanup" not in scenario["type"]:
                 if scenario["type"] == "web":
                     passed = False
                     failure_reason = "No results returned"
            
            # 2. Check keywords
            missing = []
            if passed and scenario.get("expected_keywords"):
                if isinstance(output, str):
                    lower_out = output.lower()
                    for kw in scenario["expected_keywords"]:
                        if kw.lower() not in lower_out:
                            missing.append(kw)
                else:
                    # Output might be dict or list
                    lower_out = str(output).lower()
                    for kw in scenario["expected_keywords"]:
                        if kw.lower() not in lower_out:
                            missing.append(kw)
                            
                if missing:
                    if scenario["type"] != "web_edge":
                        passed = False
                        failure_reason = f"Missing keywords: {missing}"

            # 3. Check Web Patterns
            if passed and scenario.get("expected_url_pattern") and isinstance(output, str):
                if not re.search(scenario["expected_url_pattern"], output):
                     passed = False
                     failure_reason = "Expected URL pattern not found"

            status = "PASS" if passed else "FAIL"
            print(f"Status: {status} ({duration:.2f}s)")
            if not passed:
                print(f"Reason: {failure_reason}")
                print(f"Output Snippet: {str(output)[:200]}...")
            
            # Clean up setup/cleanup outputs
            if "setup" in scenario["type"] or "cleanup" in scenario["type"]:
                 status = "DONE" # Just mark as executed
            
            results.append({"name": scenario["name"], "status": status, "note": failure_reason})
            
        except Exception as e:
            print(f"Status: ERROR")
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            results.append({"name": scenario["name"], "status": "ERROR", "note": str(e)})
        
        print("\n")
        # Increase delay for Web scenarios to avoid "Instance has been ratelimited" 503 errors
        delay = 10 if scenario.get("type", "").startswith("web") else 2
        print(f"Sleeping {delay}s to respect rate limits...")
        await asyncio.sleep(delay)

    # RAG Simulation (Git/Contacts)
    # Since we don't have explicit tools for Contacts/Git, we test if RAG stream handles them
    # by simulating a query.
    if RAG_AVAILABLE:
        print("--- Scenario: RAG Retrieval (Git/Contacts - Simulation) ---")
        rag_queries = ["Find contact Jeremiah", "Search git repositories for 'SharedLLM'"]
        for q in rag_queries:
             print(f"Query: {q}")
             # We can't easily capture stream output here without complex mocking
             # But we can assume if pipeline doesn't crash, it works?
             # For now, we skip deep RAG integration testing in this script as it depends on Vector Store state.
             print("Status: SKIPPED (Requires Vector Store State verification)\n")


    # Summary
    print("=== TEST SUMMARY ===")
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    print(f"Total: {len(results)} | Passed: {pass_count} | Failed: {sum(1 for r in results if r['status'] == 'FAIL')}")
    for r in results:
        note = f"({r['note']})" if r['note'] else ""
        print(f"- {r['name']}: {r['status']} {note}")

if __name__ == "__main__":
    asyncio.run(run_tests())
