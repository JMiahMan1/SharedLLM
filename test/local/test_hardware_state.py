# test/local/test_hardware_state.py
import os
import time

import httpx
import pytest
from dotenv import load_dotenv

# Load local .env
load_dotenv()

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
LIVE_TEST_URL = os.getenv("LIVE_TEST_URL")
GATEWAY_URL = LIVE_TEST_URL if LIVE_TEST_URL else "http://localhost:11435" # Gateway port in SOA

# Headers for direct HA verification
HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

@pytest.fixture
def client():
    return httpx.Client(timeout=180.0)

def get_ha_state(entity_id: str):
    """Directly query Home Assistant for the real state of an entity."""
    url = f"{HA_URL}/api/states/{entity_id}"
    with httpx.Client(verify=False) as client:
        resp = client.get(url, headers=HA_HEADERS)
        if resp.status_code == 200:
            return resp.json()
        return None

@pytest.mark.local_only
def test_light_toggle_verification(client):
    """
    Test: Send a toggle command to the Gateway and verify HA state actually flips.
    """
    entity_id = "light.piano_lamp"

    # 1. Get initial state
    initial = get_ha_state(entity_id)
    assert initial is not None, f"Entity {entity_id} not found in HA"
    initial_state = initial.get("state")
    print(f"\n[Test] Initial state of {entity_id}: {initial_state}")

    # 2. Send toggle via Gateway
    payload = {
        "query": f"toggle the {entity_id.split('.')[1].replace('_', ' ')}",
        "voice_id": "default"
    }
    print("[Test] Sending toggle request to Gateway...")
    resp = client.post(f"{GATEWAY_URL}/api/chat", json=payload)
    assert resp.status_code == 200
    print(f"[Test] Gateway Response: {resp.json().get('message')}")

    # 3. Wait for state propagation
    time.sleep(2)

    # 4. Verify state change
    final = get_ha_state(entity_id)
    assert final is not None, f"Entity {entity_id} not found after toggle"
    final_state = final.get("state")
    print(f"[Test] Final state of {entity_id}: {final_state}")

    assert final_state != initial_state, f"State did not change! Expected != {initial_state}"

@pytest.mark.local_only
def test_brightness_control_verification(client):
    """
    Test: Set brightness and verify HA attributes match.
    """
    entity_id = "light.piano_lamp"
    target_pct = 75
    target_val = int(target_pct * 2.55) # HA uses 0-255 internally

    # Send command
    payload = {
        "query": f"set the piano lamp to {target_pct}% brightness",
        "voice_id": "default"
    }
    print(f"\n[Test] Setting brightness to {target_pct}%...")
    resp = client.post(f"{GATEWAY_URL}/api/chat", json=payload)
    assert resp.status_code == 200

    # Wait
    time.sleep(2)

    # Verify attributes
    state = get_ha_state(entity_id)
    assert state is not None, f"Entity {entity_id} not found for brightness check"
    brightness = state.get("attributes", {}).get("brightness")
    print(f"[Test] HA reported brightness: {brightness} (Target range: {target_val-5} to {target_val+5})")

    # Allow small tolerance
    assert abs(brightness - target_val) < 10

@pytest.mark.local_only
def test_nextcloud_note_creation(client):
    """
    Test: Create a note and verify it actually exists on Nextcloud.
    """
    note_title = f"LocalTest_{int(time.time())}"
    note_content = "This is a deep functionality test."

    payload = {
        "query": f"create a note called {note_title} with content {note_content}",
        "voice_id": "default"
    }
    print(f"\n[Test] Creating note '{note_title}' via Gateway...")
    resp = client.post(f"{GATEWAY_URL}/api/chat", json=payload)
    assert resp.status_code == 200

    # Verification: Try to read the note back
    # We can use the gateway's own read endpoint or check WebDAV directly.
    # Checking via Gateway ensures the whole SOA chain works.
    time.sleep(1)
    read_payload = {"query": f"read my note called {note_title}", "voice_id": "default"}
    read_resp = client.post(f"{GATEWAY_URL}/api/chat", json=read_payload)

    assert read_resp.status_code == 200
    assert note_content in read_resp.json().get("message", "")
    print("[Test] Note verification successful.")

if __name__ == "__main__":
    # Allow running directly for convenience
    pytest.main([__file__, "-s"])
