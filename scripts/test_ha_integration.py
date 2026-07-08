#!/usr/bin/env python3
"""
Home Assistant Integration Test Suite
Tests execution service HA endpoints AND full chat interface against real HA instance.
Verifies actual state changes and HA logbook entries (no false positives).

Required environment variables:
    PROD_HOST        - Production server IP (default: 192.168.2.205)
    INTERNAL_SECRET  - SharedLLM internal secret
    HA_URL           - Home Assistant URL
    HA_TOKEN         - Home Assistant long-lived access token
"""
import json
import os
import sys
import time

import httpx

PROD_HOST = os.getenv("PROD_HOST", "192.168.2.205")
SECRET = os.environ["INTERNAL_SECRET"]
HA_URL = os.environ["HA_URL"]
HA_TOKEN = os.environ["HA_TOKEN"]
EXEC_URL = f"http://{PROD_HOST}:8003"
GATEWAY_URL = f"http://{PROD_HOST}:8080"

USER_CTX = {
    "user": "default",
    "is_admin": True,
    "ha_url": HA_URL,
    "ha_token": HA_TOKEN,
}

HEADERS = {
    "Content-Type": "application/json",
    "X-Internal-Secret": SECRET,
}

def _safe_resp_content(resp):
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict):
            return msg.get("content", "")
    return ""

NON_ADMIN_CTX = {
    "user": "testuser",
    "is_admin": False,
    "ha_url": HA_URL,
    "ha_token": HA_TOKEN,
}

results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

def log(msg, level="INFO"):
    print(f"[{level}] {msg}")

def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    if passed:
        results["passed"] = results.get("passed", 0) + 1
    else:
        results["failed"] = results.get("failed", 0) + 1
    details_list = results.get("details", [])
    if not isinstance(details_list, list):
        details_list = []
        results["details"] = details_list
    details_list.append(f"  [{status}] {name}: {detail}")
    print(f"  [{status}] {name}: {detail}")

def ha_get_state(entity_id):
    """Get state directly from HA to verify changes."""
    try:
        resp = httpx.get(
            f"{HA_URL}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
        return None
    except Exception as e:
        return {"error": str(e)}

def ha_get_logbook(entity_id, minutes=5):
    """Get logbook entries from HA directly."""
    import datetime
    start = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes)).isoformat()
    try:
        resp = httpx.get(
            f"{HA_URL}/api/logbook/{start}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            params={"entity": entity_id},
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        return []

def exec_post(endpoint, payload):
    """POST to execution service."""
    try:
        resp = httpx.post(
            f"{EXEC_URL}{endpoint}",
            headers=HEADERS,
            json=payload,
            timeout=30.0,
        )
        return resp.status_code, resp.json()
    except Exception as e:
        return 0, {"error": str(e)}

def exec_get(endpoint, params=None):
    """GET from execution service."""
    try:
        resp = httpx.get(
            f"{EXEC_URL}{endpoint}",
            headers=HEADERS,
            params=params,
            timeout=30.0,
        )
        return resp.status_code, resp.json()
    except Exception as e:
        return 0, {"error": str(e)}

def gateway_chat(query, stream=False):
    """Send a chat message to the gateway (Ollama-compatible API)."""
    try:
        resp = httpx.post(
            f"{GATEWAY_URL}/api/chat",
            headers={"Content-Type": "application/json"},
            json={
                "model": "assistant",
                "query": query,
                "stream": stream,
            },
            timeout=120.0,
        )
        return resp.status_code, resp.json()
    except Exception as e:
        return 0, {"error": str(e)}

def wait_for_state_change(entity_id, expected_state, timeout=15):
    """Poll HA until state changes or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        state = ha_get_state(entity_id)
        if state and state.get("state") == expected_state:
            return True, state
        time.sleep(1)
    final = ha_get_state(entity_id)
    return False, final

def wait_for_logbook_entry(entity_id, expected_action, timeout=15):
    """Poll HA logbook until expected entry appears."""
    start = time.time()
    while time.time() - start < timeout:
        entries = ha_get_logbook(entity_id, minutes=5)
        if isinstance(entries, list):
            for entry in entries:
                if expected_action.lower() in str(entry.get("action_type", "")).lower() or \
                   expected_action.lower() in str(entry.get("message", "")).lower() or \
                   expected_action.lower() in str(entry.get("state", "")).lower():
                    return True, entries
        time.sleep(2)
    entries = ha_get_logbook(entity_id, minutes=5)
    return False, entries


# ─── Test 1: Light Control - Turn ON (Direct API) ─────────────────────────────
def test_light_turn_on():
    log("=== Test 1: Light Control - Turn ON (Direct API) ===")
    entity = "light.piano_lamp"

    initial = ha_get_state(entity)
    initial_state = initial.get("state") if initial else "unknown"
    log(f"  Initial state of {entity}: {initial_state}")

    # Ensure it starts off
    if initial_state == "on":
        exec_post("/execute/light", {"user_context": USER_CTX, "entity_id": entity, "action": "turn_off"})
        time.sleep(2)

    # Turn on via execution service (mimics gateway fast path)
    payload = {
        "user_context": USER_CTX,
        "entity_id": entity,
        "action": "turn_on",
    }
    status, resp = exec_post("/execute/light", payload)

    if status != 200:
        record("Light ON - HTTP response", False, f"Status={status}, resp={resp}")
        return

    record("Light ON - HTTP response", resp.get("status") == "SUCCESS", f"resp={resp.get('status')}")

    # Verify state changed in HA
    changed, final = wait_for_state_change(entity, "on")
    record("Light ON - HA state verified", changed, f"Expected=on, Actual={final.get('state') if isinstance(final, dict) else final}")

    # Verify logbook entry
    has_log, entries = wait_for_logbook_entry(entity, "on")
    record("Light ON - HA logbook entry", has_log, f"Found {len(entries)} entries")


# ─── Test 2: Light Control - Turn OFF (Direct API) ────────────────────────────
def test_light_turn_off():
    log("=== Test 2: Light Control - Turn OFF (Direct API) ===")
    entity = "light.piano_lamp"

    payload = {
        "user_context": USER_CTX,
        "entity_id": entity,
        "action": "turn_off",
    }
    status, resp = exec_post("/execute/light", payload)

    if status != 200:
        record("Light OFF - HTTP response", False, f"Status={status}, resp={resp}")
        return

    record("Light OFF - HTTP response", resp.get("status") == "SUCCESS", f"resp={resp.get('status')}")

    # Verify state changed in HA
    changed, final = wait_for_state_change(entity, "off")
    record("Light OFF - HA state verified", changed, f"Expected=off, Actual={final.get('state') if isinstance(final, dict) else final}")

    # Verify logbook entry
    has_log, entries = wait_for_logbook_entry(entity, "off")
    record("Light OFF - HA logbook entry", has_log, f"Found {len(entries)} entries")


# ─── Test 3: Light Control - Brightness (Direct API) ──────────────────────────
def test_light_brightness():
    log("=== Test 3: Light Control - Brightness (Direct API) ===")
    entity = "light.piano_lamp"

    # First ensure it's on
    exec_post("/execute/light", {"user_context": USER_CTX, "entity_id": entity, "action": "turn_on"})
    time.sleep(2)

    # Set brightness
    payload = {
        "user_context": USER_CTX,
        "entity_id": entity,
        "action": "turn_on",
        "brightness_pct": 50,
    }
    status, resp = exec_post("/execute/light", payload)

    if status != 200:
        record("Light Brightness - HTTP response", False, f"Status={status}, resp={resp}")
        return

    record("Light Brightness - HTTP response", resp.get("status") == "SUCCESS", f"resp={resp.get('status')}")

    # Verify brightness attribute changed
    time.sleep(3)
    state = ha_get_state(entity)
    if state and "attributes" in state:
        attrs = state.get("attributes")
        if attrs and isinstance(attrs, dict):
            brightness = attrs.get("brightness")
            # HA brightness is 0-255, 50% = ~128
            expected_approx = 128
            close = brightness is not None and abs(brightness - expected_approx) < 30
            record("Light Brightness - HA attribute verified", close, f"brightness={brightness} (expected ~{expected_approx})")
        else:
            record("Light Brightness - HA attribute verified", False, "attributes not a dict")
    else:
        record("Light Brightness - HA attribute verified", False, "state is None or missing attributes")

    # Turn off after test
    exec_post("/execute/light", {"user_context": USER_CTX, "entity_id": entity, "action": "turn_off"})


# ─── Test 4: Generic HA Service Call (Direct API) ─────────────────────────────
def test_ha_service_call():
    log("=== Test 4: Generic HA Service Call (Direct API) ===")
    entity = "light.hall_lamp"

    initial = ha_get_state(entity)
    initial_state = initial.get("state") if initial else "unknown"
    log(f"  Initial state of {entity}: {initial_state}")

    target_state = "off" if initial_state == "on" else "on"

    payload = {
        "user_context": USER_CTX,
        "domain": "light",
        "service": f"turn_{target_state}",
        "entity_id": entity,
        "service_data": {},
    }
    status, resp = exec_post("/execute/ha_service", payload)

    if status != 200:
        record("HA Service - HTTP response", False, f"Status={status}, resp={resp}")
        return

    record("HA Service - HTTP response", resp.get("status") == "SUCCESS", f"resp={resp.get('status')}")

    # Verify state changed in HA
    changed, final = wait_for_state_change(entity, target_state)
    record("HA Service - HA state verified", changed, f"Expected={target_state}, Actual={final.get('state') if isinstance(final, dict) else final}")

    # Verify logbook
    has_log, entries = wait_for_logbook_entry(entity, target_state)
    record("HA Service - HA logbook entry", has_log, f"Found {len(entries)} entries")


# ─── Test 5: HA Logbook Endpoint ──────────────────────────────────────────────
def test_ha_logbook():
    log("=== Test 5: HA Logbook Endpoint ===")
    entity = "light.piano_lamp"

    payload = {
        "user_context": USER_CTX,
        "entity_id": entity,
        "days": 1,
    }
    status, resp = exec_post("/execute/ha_logbook", payload)

    if status != 200:
        record("HA Logbook - HTTP response", False, f"Status={status}, resp={resp}")
        return

    detail = resp.get("detail", {}) if isinstance(resp, dict) else {}
    entries = detail.get("entries", []) if isinstance(detail, dict) else []
    has_entries = len(entries) > 0
    record("HA Logbook - HTTP response", resp.get("status") == "SUCCESS", f"status={resp.get('status')}, entries={len(entries)}")
    record("HA Logbook - Has entries", has_entries, f"Found {len(entries)} entries for {entity}")

    if has_entries:
        sample = entries[0] if entries else {}
        log(f"  Sample entry: {json.dumps(sample, indent=2)[:200]}")


# ─── Test 6: Discovery Entities ───────────────────────────────────────────────
def test_discovery_entities():
    log("=== Test 6: Discovery Entities ===")
    status, resp = exec_get("/discovery/entities", {"ha_url": HA_URL, "ha_token": HA_TOKEN})

    if status != 200:
        record("Discovery Entities - HTTP response", False, f"Status={status}, resp={resp}")
        return

    entities = resp.get("entities", [])
    has_entities = len(entities) > 0
    record("Discovery Entities - HTTP response", has_entities, f"Found {len(entities)} entities")

    # Check area mapping
    with_areas = [e for e in entities if isinstance(e, dict) and e.get("attributes", {}).get("area_id")]
    record("Discovery Entities - Area mapping present", len(with_areas) > 0, f"{len(with_areas)} entities have area_id")


# ─── Test 7: Discovery History ────────────────────────────────────────────────
def test_discovery_history():
    log("=== Test 7: Discovery History ===")
    entity = "light.piano_lamp"
    status, resp = exec_get("/discovery/history", {
        "ha_url": HA_URL,
        "ha_token": HA_TOKEN,
        "entity_id": entity,
        "days": 1,
    })

    if status != 200:
        record("Discovery History - HTTP response", False, f"Status={status}")
        return

    is_list = isinstance(resp, list)
    record("Discovery History - Returns list", is_list, f"Got {len(resp) if is_list else 'non-list'} entries")


# ─── Test 8: Authorization - Non-admin blocked from unlock ────────────────────
def test_auth_non_admin_blocked():
    log("=== Test 8: Authorization - Non-admin blocked ===")
    # Try to unlock a lock (admin-only action)
    payload = {
        "user_context": NON_ADMIN_CTX,
        "entity_id": "lock.front_door",
        "action": "unlock",
    }
    status, resp = exec_post("/execute/security", payload)

    # Should be blocked by handler-level authorization
    if status == 403:
        record("Security - Non-admin blocked (HTTP 403)", True, "Correctly rejected")
    elif resp.get("status") == "FAILURE":
        record("Security - Non-admin blocked (FAILURE response)", True, f"resp={resp.get('message', '')}")
    else:
        record("Security - Non-admin blocked", False, f"status={status}, resp={resp}")


# ─── Test 9: Climate Control ──────────────────────────────────────────────────
def test_climate_control():
    log("=== Test 9: Climate Control ===")
    entity = "climate.downstairs"

    initial = ha_get_state(entity)
    if not initial:
        record("Climate - Entity exists", False, "Entity not found")
        return
    initial_attrs = initial.get("attributes") if initial else {}
    record("Climate - Entity exists", True, f"state={initial.get('state') if initial else 'unknown'}")

    initial_temp = initial_attrs.get("temperature") if initial_attrs and isinstance(initial_attrs, dict) else None
    log(f"  Initial temperature: {initial_temp}")

    target_temp = 75.0

    payload = {
        "user_context": USER_CTX,
        "entity_id": entity,
        "temperature": target_temp,
    }
    status, resp = exec_post("/execute/climate", payload)

    if status != 200:
        record("Climate - HTTP response", False, f"Status={status}, resp={resp}")
        return

    record("Climate - HTTP response", resp.get("status") == "SUCCESS", f"resp={resp.get('status')}")

    # Verify temperature changed
    time.sleep(5)
    state = ha_get_state(entity)
    if state:
        state_attrs = state.get("attributes")
        new_temp = state_attrs.get("temperature") if state_attrs and isinstance(state_attrs, dict) else None
        temp_changed = new_temp == target_temp
        record("Climate - Temperature verified", temp_changed, f"Expected={target_temp}, Actual={new_temp}")
    else:
        record("Climate - Temperature verified", False, "Could not read state")


# ─── Test 10: Health endpoint ─────────────────────────────────────────────────
def test_health():
    log("=== Test 10: Health Endpoint ===")
    status, resp = exec_get("/health")
    record("Health - Responds OK", status == 200 and resp.get("status") == "ok", f"status={status}, resp={resp}")


# ─── Test 11: Full Chat Interface - "Turn on the piano lamp" ──────────────────
def test_chat_turn_on_light():
    log("=== Test 11: Full Chat Interface - 'Turn on the piano lamp' ===")
    entity = "light.piano_lamp"

    # Ensure it starts off
    initial = ha_get_state(entity)
    if initial and initial.get("state") == "on":
        exec_post("/execute/light", {"user_context": USER_CTX, "entity_id": entity, "action": "turn_off"})
        time.sleep(2)

    # Send chat message
    log("  Sending: 'Jarvis, turn on the piano lamp'")
    status, resp = gateway_chat("Jarvis, turn on the piano lamp")

    if status != 200:
        record("Chat - HTTP response", False, f"Status={status}, resp={resp}")
        return

    # Check response indicates success
    content = _safe_resp_content(resp)
    log(f"  Gateway response: {content[:200]}")

    success_indicators = ["success", "completed", "executed", "turned on", "piano lamp"]
    has_success = any(indicator in content.lower() for indicator in success_indicators)
    record("Chat - Response indicates success", has_success, f"content={content[:150]}")

    # Verify state changed in HA
    changed, final = wait_for_state_change(entity, "on")
    record("Chat - HA state verified (light.piano_lamp=on)", changed, f"Expected=on, Actual={final.get('state') if isinstance(final, dict) else final}")

    # Verify logbook entry
    has_log, entries = wait_for_logbook_entry(entity, "on")
    record("Chat - HA logbook entry", has_log, f"Found {len(entries)} entries")


# ─── Test 12: Full Chat Interface - "Turn off the piano lamp" ─────────────────
def test_chat_turn_off_light():
    log("=== Test 12: Full Chat Interface - 'Turn off the piano lamp' ===")
    entity = "light.piano_lamp"

    # Ensure it starts on
    initial = ha_get_state(entity)
    if initial and initial.get("state") == "off":
        exec_post("/execute/light", {"user_context": USER_CTX, "entity_id": entity, "action": "turn_on"})
        time.sleep(2)

    # Send chat message
    log("  Sending: 'Jarvis, turn off the piano lamp'")
    status, resp = gateway_chat("Jarvis, turn off the piano lamp")

    if status != 200:
        record("Chat OFF - HTTP response", False, f"Status={status}, resp={resp}")
        return

    content = _safe_resp_content(resp)
    log(f"  Gateway response: {content[:200]}")

    success_indicators = ["success", "completed", "executed", "turned off", "off"]
    has_success = any(indicator in content.lower() for indicator in success_indicators)
    record("Chat OFF - Response indicates success", has_success, f"content={content[:150]}")

    # Verify state changed in HA
    changed, final = wait_for_state_change(entity, "off")
    record("Chat OFF - HA state verified (light.piano_lamp=off)", changed, f"Expected=off, Actual={final.get('state') if isinstance(final, dict) else final}")

    # Verify logbook entry
    has_log, entries = wait_for_logbook_entry(entity, "off")
    record("Chat OFF - HA logbook entry", has_log, f"Found {len(entries)} entries")


# ─── Test 13: Full Chat Interface - HA Status Query ───────────────────────────
def test_chat_status_query():
    log("=== Test 13: Full Chat Interface - Status Query ===")
    entity = "light.hall_lamp"

    # Get actual state from HA first
    actual = ha_get_state(entity)
    actual_state = actual.get("state") if isinstance(actual, dict) else "unknown"
    log(f"  Actual HA state of {entity}: {actual_state}")

    # Ask via chat
    log("  Sending: 'What is the status of the hall lamp?'")
    status, resp = gateway_chat("What is the status of the hall lamp?")

    if status != 200:
        record("Chat Status - HTTP response", False, f"Status={status}")
        return

    content = _safe_resp_content(resp)
    log(f"  Gateway response: {content[:200]}")

    # Verify response mentions the actual state
    actual_state_lower = actual_state.lower() if isinstance(actual_state, str) else ""
    has_state = actual_state_lower in content.lower()
    record("Chat Status - Response matches HA state", has_state, f"HA state={actual_state}, response mentions it={has_state}")


# ─── Run all tests ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log("Starting HA Integration Test Suite")
    log(f"Execution service: {EXEC_URL}")
    log(f"Gateway: {GATEWAY_URL}")
    log(f"HA URL: {HA_URL}")
    log("")

    test_health()
    log("")
    test_light_turn_on()
    log("")
    test_light_turn_off()
    log("")
    test_light_brightness()
    log("")
    test_ha_service_call()
    log("")
    test_ha_logbook()
    log("")
    test_discovery_entities()
    log("")
    test_discovery_history()
    log("")
    test_auth_non_admin_blocked()
    log("")
    test_climate_control()
    log("")
    test_chat_turn_on_light()
    log("")
    test_chat_turn_off_light()
    log("")
    test_chat_status_query()
    log("")

    # Summary
    log("=" * 60)
    log(f"RESULTS: {results['passed']} passed, {results['failed']} failed, {results['skipped']} skipped")
    log("")
    for d in (results.get("details") or []):
        print(d)

    sys.exit(0 if results["failed"] == 0 else 1)
