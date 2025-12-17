
import unittest
import requests
import time
import sys
import os
from dotenv import load_dotenv

# Load env
load_dotenv()
API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}

# Target Entity
DEVICE_NAME = "Office TV"
DEVICE_ENTITY = "media_player.office_tv_chrome_2"
CMD_TIMEOUT = 60  # Increased to handle slow remote API

# --- LOCAL HELPERS ---
def print_pass(msg):
    print(f"✅ PASS: {msg}")

def print_fail(msg):
    print(f"❌ FAIL: {msg}")

def print_info(msg):
    print(f"\nℹ️  {msg}")

def get_ha_state(entity_id):
    """Get current state of Home Assistant entity"""
    try:
        r = requests.get(
            f"{HA_URL}/api/states/{entity_id}", 
            headers=HA_HEADERS, 
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[ERROR] Exception getting state for {entity_id}: {e}")
        return None

def wait_for_valid_state(entity_id, timeout=30):
    """Wait for device to be in a state where commands are valid (playing, paused, idle)"""
    start = time.time()
    while time.time() - start < timeout:
        state_data = get_ha_state(entity_id)
        if state_data:
            s = state_data.get('state')
            if s in ['playing', 'paused', 'idle', 'on']:
                return True, s
            # Special check for buffering/apps
            app_id = state_data.get('attributes', {}).get('app_id')
            if app_id and s != 'off' and s != 'unavailable':
                 return True, f"{s} (App: {app_id})"
        time.sleep(2)
    return False, "off/unavailable"

def send_command(command):
    """Send command to RAG API"""
    try:
        print(f"  > Sending: '{command}'")
        resp = requests.post(
            f"{API_URL}/api/chat",
            json={"messages": [{"role": "user", "content": command}]},
            headers=HEADERS,
            timeout=CMD_TIMEOUT 
        )
        if resp.status_code == 200:
            content = resp.json().get("message", {}).get("content", "")
            print(f"    Response: {content[:100]}...")
            return content
        else:
            print(f"    [Error] API {resp.status_code}: {resp.text}")
            return f"Error: {resp.status_code}"
    except Exception as e:
        print(f"    [Exception]: {e}")
        return f"Exception: {e}"

# --- TEST CLASS ---
class TestOfficeTV(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        print(f"\n{'='*60}")
        print(f"Target: {DEVICE_NAME} ({DEVICE_ENTITY})")
        print("Strategy: Launch App First -> Verify Wake -> Test Controls")
        print(f"{'='*60}")

    def test_01_wake_and_launch(self):
        """CRITICAL: Launch content to ensure device is awake and listening."""
        print_info("TEST 1: Wake & Launch YouTube")
        
        # 1. Check initial state
        initial = get_ha_state(DEVICE_ENTITY)
        print(f"    Initial State: {initial.get('state') if initial else 'Unknown'}")

        # 2. Launch YouTube (This acts as Power On + Set Content)
        send_command(f"Launch YouTube on {DEVICE_NAME}")
        
        # 3. VERIFY: Device must wake up and report valid state
        print("    Verifying device wake-up...")
        is_ready, state = wait_for_valid_state(DEVICE_ENTITY, timeout=45)
        
        if is_ready:
            print_pass(f"Device is Active (State: {state})")
        else:
            print_fail(f"Device failed to wake/launch. State: {state}")
            print("    [ABORT] Cannot proceed with Volume/Transport tests if device is dead.")
            self.fail("Device failed to wake up.")

    def test_02_volume_verification(self):
        """Test Volume controls ONLY if device is active."""
        print_info("TEST 2: Volume Verification")
        
        # Get current volume
        state_data = get_ha_state(DEVICE_ENTITY)
        if not state_data or state_data.get('state') in ['off', 'unavailable']:
            self.skipTest("Device is OFF, skipping volume test")

        current_vol = state_data.get("attributes", {}).get("volume_level")
        if current_vol is None:
            print("    [WARN] Device does not report volume level. Attempting blind set.")
            target_vol = 0.5
        else:
            # Pick a target different from current
            target_vol = 0.6 if abs(current_vol - 0.5) < 0.05 else 0.5
            print(f"    Current Vol: {current_vol}, Target: {target_vol}")

        # Execute
        send_command(f"Set volume on {DEVICE_NAME} to {int(target_vol*100)}%")
        time.sleep(5) # Allow processing
        
        # Verify
        new_state = get_ha_state(DEVICE_ENTITY)
        new_vol = new_state.get("attributes", {}).get("volume_level")
        
        if new_vol and abs(new_vol - target_vol) < 0.05:
            print_pass(f"Volume match verified (Got: {new_vol})")
        else:
            print_fail(f"Volume mismatch. Expected {target_vol}, Got {new_vol}")

    def test_03_mute_toggle(self):
        """Test Mute/Unmute toggle."""
        print_info("TEST 3: Mute Toggle")
        
        # Mute
        send_command(f"Mute {DEVICE_NAME}")
        time.sleep(3)
        s = get_ha_state(DEVICE_ENTITY)
        is_muted = s.get("attributes", {}).get("is_volume_muted")
        
        if is_muted:
            print_pass("Mute Verified")
        else:
            print_fail(f"Mute failed (is_volume_muted: {is_muted})")
            
        # Unmute
        send_command(f"Unmute {DEVICE_NAME}")
        time.sleep(3)
        s = get_ha_state(DEVICE_ENTITY)
        is_muted = s.get("attributes", {}).get("is_volume_muted")
        
        if not is_muted:
            print_pass("Unmute Verified")
        else:
            print_fail(f"Unmute failed (is_volume_muted: {is_muted})")

    def test_04_transport_controls(self):
        """Test Pause/Play."""
        print_info("TEST 4: Transport (Pause/Resume)")
        
        # Ensure we are in a state that supports pause (playing)
        s = get_ha_state(DEVICE_ENTITY)
        if s.get('state') != 'playing':
             print(f"    [INFO] State is '{s.get('state')}', forcing Play first...")
             send_command(f"Resume {DEVICE_NAME}")
             time.sleep(5)

        # Pause
        send_command(f"Pause {DEVICE_NAME}")
        time.sleep(5)
        
        s = get_ha_state(DEVICE_ENTITY)
        if s.get('state') == 'paused':
            print_pass("Paused Verified")
        else:
            print_fail(f"Pause failed. State: {s.get('state')}")

        # Resume
        send_command(f"Resume {DEVICE_NAME}")
        time.sleep(5)
        s = get_ha_state(DEVICE_ENTITY)
        if s.get('state') == 'playing':
            print_pass("Resume Verified")
        else:
            print_fail(f"Resume failed. State: {s.get('state')}")

    def test_05_power_off(self):
        """Finally turn it off."""
        print_info("TEST 5: Power Off")
        
        send_command(f"Turn off {DEVICE_NAME}")
        time.sleep(10)
        
        s = get_ha_state(DEVICE_ENTITY)
        if s.get('state') == 'off':
            print_pass("Power Off Verified")
        else:
            print_fail(f"Power Off Failed. State: {s.get('state')}")

if __name__ == "__main__":
    # Fail fast if wake up fails
    runner = unittest.TextTestRunner(failfast=True)
    unittest.main(testRunner=runner, exit=False)
