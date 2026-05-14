#!/usr/bin/env python3
"""
Comprehensive Media Playback Test Suite - Enhanced Version

Features:
- Natural language command testing (mimics real user interaction)
- Comprehensive state verification via HA API
- Docker log monitoring for backend errors
- Detailed error tracking with context
- Auto-power-on verification for each command
- Retry logic for transient failures
- Progress tracking and time measurements
- [NEW] Dynamic Device Discovery (No Hardcoding)

Target Names:
- "Office TV"
- "Master Bedroom TV"
- "Gracie's TV"

Test Coverage:
- Music playback via Music Assistant (play, pause, resume, skip, stop)
- Video playback via YouTube (play, pause, resume, stop)
- Auto-power-on functionality
- State consistency verification
- Music Volume Confirmation
"""

import sys
import os
import time
import requests
import json
from datetime import datetime
from typing import Optional, List, Tuple

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from app.settings import HA_URL, HA_ENV_TOKEN as HA_TOKEN
from test_helpers import get_ha_state, verify_device_state

# Configuration
API_URL = os.getenv("API_URL", "http://ai.local:11435")
REMOTE_HOST = "jeremiah@ai.local"
if not HA_URL:
    HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
if not HA_TOKEN:
    HA_TOKEN = os.getenv("HA_TOKEN", "")

API_HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
HA_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

# Target Friendly Names to Test matching brands:
# Chrome/Android: Office TV, Master Bedroom TV
# Roku: Gracie's TV / 28 TCL Roku TV
# LG WebOS: Living Room TV (assumed)
TARGET_NAMES = ["Office TV", "Master Bedroom TV", "Gracie", "Roku", "Living Room TV", "LG"]

# Output files
ERROR_LOG = "temp/comprehensive_test_errors.txt"
DETAILED_LOG = "temp/comprehensive_test_detailed.txt"
DOCKER_LOG_SNAPSHOT = "temp/docker_logs_snapshot.txt"

class EnhancedTestResults:
    """Enhanced test results tracking with detailed context"""
    
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.tests_skipped = 0
        self.errors = []
        self.detailed_logs = []
        self.start_time = datetime.now()
        self.backend_errors = []
        
    def log_detail(self, category: str, message: str, data: dict = None):
        """Log detailed information for debugging"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        entry = {
            "timestamp": timestamp,
            "category": category,
            "message": message,
            "data": data or {}
        }
        self.detailed_logs.append(entry)
        
    def log_pass(self, test_name: str, duration: float = 0, state_info: dict = None):
        """Log successful test with timing"""
        self.tests_passed += 1
        self.tests_run += 1
        duration_str = f" ({duration:.1f}s)" if duration > 0 else ""
        print(f"\033[92m✅ PASS\033[0m {test_name}{duration_str}")
        self.log_detail("PASS", test_name, {"duration": duration, "state": state_info})
    
    def log_fail(self, test_name: str, reason: str, expected: str = None, actual: str = None, state_info: dict = None):
        """Log failed test with full context"""
        self.tests_failed += 1
        self.tests_run += 1
        
        error_context = {
            "test": test_name,
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        
        if expected:
            error_context["expected"] = expected
        if actual:
            error_context["actual"] = actual
        if state_info:
            error_context["state_info"] = state_info
            
        self.errors.append(error_context)
        
        print(f"\033[91m❌ FAIL\033[0m {test_name}")
        print(f"       \033[91m{reason}\033[0m")
        if expected and actual:
            print(f"       Expected: {expected}, Got: {actual}")
            
        self.log_detail("FAIL", test_name, error_context)

    def log_skip(self, test_name: str, reason: str):
        """Log skipped test"""
        self.tests_skipped += 1
        print(f"\033[93m⚠️  SKIP\033[0m {test_name} ({reason})")
        self.log_detail("SKIP", test_name, {"reason": reason})
    
    def log_backend_error(self, error_type: str, message: str):
        """Log errors detected in backend logs"""
        self.backend_errors.append({
            "type": error_type,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })
        print(f"\033[93m⚠️  BACKEND ERROR\033[0m {error_type}: {message}")
    
    def write_logs(self):
        """Write all logs to files"""
        if self.errors or self.backend_errors:
            with open(ERROR_LOG, 'w') as f:
                f.write(f"TEST ERROR LOG - {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*80 + "\n\n")
                
                if self.errors:
                    f.write("TEST FAILURES:\n")
                    f.write("-"*80 + "\n")
                    for i, error in enumerate(self.errors, 1):
                        f.write(f"\n{i}. {error['test']}\n")
                        f.write(f"   Time: {error['timestamp']}\n")
                        f.write(f"   Reason: {error['reason']}\n")
                        if 'expected' in error:
                            f.write(f"   Expected: {error['expected']}\n")
                        if 'actual' in error:
                            f.write(f"   Actual: {error['actual']}\n")
                        if 'state_info' in error:
                            f.write(f"   State Info: {json.dumps(error['state_info'], indent=2)}\n")
                
                if self.backend_errors:
                    f.write(f"\n\nBACKEND ERRORS:\n")
                    f.write("-"*80 + "\n")
                    for i, error in enumerate(self.backend_errors, 1):
                        f.write(f"\n{i}. [{error['type']}] {error['message']}\n")
                        f.write(f"   Time: {error['timestamp']}\n")
                        
            print(f"\n\033[93m[INFO]\033[0m Errors written to {ERROR_LOG}")
        
        with open(DETAILED_LOG, 'w') as f:
            f.write(f"DETAILED TEST LOG - {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("="*80 + "\n\n")
            for entry in self.detailed_logs:
                f.write(f"[{entry['timestamp']}] {entry['category']}: {entry['message']}\n")
                if entry['data']:
                    f.write(f"  Data: {json.dumps(entry['data'], indent=2)}\n")
            print(f"\033[93m[INFO]\033[0m Detailed log written to {DETAILED_LOG}")
    
    def print_summary(self):
        """Print comprehensive test summary"""
        duration = (datetime.now() - self.start_time).total_seconds()
        
        print("\n" + "="*80)
        print("COMPREHENSIVE TEST SUMMARY")
        print("="*80)
        print(f"Duration: {duration:.1f}s")
        print(f"Total Tests: {self.tests_run + self.tests_skipped}")
        print(f"Passed: \033[92m{self.tests_passed}\033[0m")
        print(f"Failed: \033[91m{self.tests_failed}\033[0m")
        print(f"Skipped: \033[93m{self.tests_skipped}\033[0m")
        
        total_valid = self.tests_run
        if total_valid > 0:
            success_rate = self.tests_passed / total_valid * 100
            color = "\033[92m" if success_rate >= 80 else "\033[93m" if success_rate >= 50 else "\033[91m"
            print(f"Success Rate (of attempted): {color}{success_rate:.1f}%\033[0m")
        
        if self.backend_errors:
            print(f"\nBackend Errors Detected: \033[93m{len(self.backend_errors)}\033[0m")
        
        print("="*80)

results = EnhancedTestResults()

def log(msg: str, category: str = "INFO"):
    """Enhanced logging with categories"""
    colors = {
        "INFO": "\033[94m",
        "SUCCESS": "\033[92m",
        "WARNING": "\033[93m",
        "ERROR": "\033[91m",
        "RESET": "\033[0m"
    }
    color = colors.get(category, colors["INFO"])
    print(f"{color}[{category}]{colors['RESET']} {msg}", flush=True)
    results.log_detail(category, msg)

def check_api_health() -> bool:
    """Verify API is responding"""
    try:
        resp = requests.get(f"{API_URL}/health", timeout=5)
        return resp.status_code == 200
    except:
        return False

def capture_docker_logs():
    """Capture recent docker logs for error analysis"""
    try:
        import subprocess
        result = subprocess.run(
            ["ssh", REMOTE_HOST, "docker logs unified_rag_api --tail 50"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            with open(DOCKER_LOG_SNAPSHOT, 'w') as f:
                f.write(f"Docker Logs Snapshot - {datetime.now().isoformat()}\n")
                f.write("="*80 + "\n")
                f.write(result.stdout)
            
            # Check for errors in logs
            error_keywords = ["ERROR", "FAIL", "Traceback", "Exception"]
            for line in result.stdout.split('\n'):
                if any(keyword in line for keyword in error_keywords):
                    results.log_backend_error("LogError", line.strip())
                    
    except Exception as e:
        log(f"Failed to capture docker logs: {e}", "WARNING")

def get_detailed_state(entity_id: str) -> Optional[dict]:
    """Get detailed device state with all attributes"""
    state_data = get_ha_state(entity_id)
    if not state_data:
        return None
    
    return {
        "state": state_data.get("state", "unknown"),
        "media_title": state_data.get("attributes", {}).get("media_title"),
        "app_id": state_data.get("attributes", {}).get("app_id"),
        "volume": state_data.get("attributes", {}).get("volume_level"),
        "device_class": state_data.get("attributes", {}).get("device_class"),
        "friendly_name": state_data.get("attributes", {}).get("friendly_name")
    }

def discover_devices() -> dict:
    """
    Dynamically discover devices from HA matching TARGET_NAMES.
    Returns: { "Friendly Name": { "music_entity": ..., "video_entity": ..., "turn_off_entity": ...} }
    """
    log("Discovering devices from Home Assistant...", "INFO")
    try:
        resp = requests.get(f"{HA_URL}/api/states", headers=HA_HEADERS, timeout=10)
        items = resp.json()
    except Exception as e:
        log(f"Discovery Failed: {e}", "ERROR")
        return {}

    device_map = {}
    
    for target in TARGET_NAMES:
        candidates = []
        target_lower = target.lower()
        
        # Find all media players matching name
        for item in items:
            eid = item["entity_id"]
            if not eid.startswith("media_player."): continue
            
            attrs = item.get("attributes", {})
            fname = attrs.get("friendly_name", "").lower()
            
            # Strict-ish matching
            # Fuzzy match: if target is in friendly name OR entity_id
            if target_lower in fname or target_lower.replace(" ", "_") in eid:
                 # Special handling for pairs (e.g. Chrome vs Android)
                 # If we have a 'cast' or 'chrome' entity, it's likely a cast target
                 # If we have a 'tv' or 'android' entity, it's a video target
                 candidates.append(item)
                 
        if not candidates:
            # Special fallback for Gracie's TV which might be "28_tcl_roku_tv"
            if "gracie" in target_lower:
                 for item in items:
                      if "roku" in item["entity_id"]:
                           candidates.append(item)
            
        if not candidates:
            log(f"Could not find any entities for '{target}'", "WARNING")
            continue

        video_entity = None
        music_entity = None
        
        # Logic to separate TV from Speaker/Cast
        for c in candidates:
            eid = c["entity_id"]
            dclass = c.get("attributes", {}).get("device_class")
            
            if dclass == "tv":
                video_entity = eid
            elif dclass == "speaker":
                music_entity = eid
            elif "roku" in eid: # Roku is usually both
                video_entity = eid
                music_entity = eid
            
            # If nothing specific, just pick one as default
            if not video_entity: video_entity = eid
            if not music_entity: music_entity = eid
            
        # Ensure we have both set, defaulting to each other if missing
        if not video_entity and music_entity: video_entity = music_entity
        if not music_entity and video_entity: music_entity = video_entity
        
        device_map[target] = {
            "name": target,
            "music_entity": music_entity,
            "video_entity": video_entity,
            "turn_off_entity": video_entity # Always turn off TV as priority
        }
        
        log(f"Discovered '{target}': Music={music_entity}, Video={video_entity}", "SUCCESS")
        
    return device_map

def send_command(query: str, max_retries: int = 1) -> Tuple[bool, int, str]:
    """Send command to API with retry logic"""
    log(f"Command: '{query}'")
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                f"{API_URL}/api/chat",
                json={"messages": [{"role": "user", "content": query}]},
                headers=API_HEADERS,
                timeout=60
            )
            success = resp.status_code == 200
            log(f"Response: {resp.status_code}", "SUCCESS" if success else "WARNING")
            return (success, resp.status_code, resp.text[:200])
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                log(f"Timeout... retrying", "WARNING")
            else:
                return (False, 408, "Timeout")
        except Exception as e:
            return (False, 500, str(e))
    return (False, 500, "Max retries exceeded")

def force_device_off(entity_id: str) -> bool:
    """Force device to OFF state via HA API"""
    log(f"Forcing {entity_id} OFF")
    try:
        requests.post(f"{HA_URL}/api/services/media_player/turn_off", headers=HA_HEADERS, json={"entity_id": entity_id}, timeout=10)
        time.sleep(2)
        state_data = get_ha_state(entity_id)
        if state_data and state_data.get('state') in ['off', 'standby', 'unavailable']:
            log(f"✅ {entity_id} is OFF", "SUCCESS")
            return True
        return False
    except Exception:
        return False

def wait_for_state(entity_id: str, expected_states: List[str], timeout: int = 20) -> Tuple[str, dict]:
    """Wait for device to reach expected state"""
    if isinstance(expected_states, str): expected_states = [expected_states]
    log(f"Waiting for state in {expected_states} (max {timeout}s)")
    start_time = time.time()
    for _ in range(timeout):
        state_info = get_detailed_state(entity_id)
        if state_info and state_info['state'] in expected_states:
             return (state_info['state'], state_info)
        time.sleep(1)
    
    current = get_detailed_state(entity_id) or {}
    return (current.get('state', 'unknown'), current)

def run_test(test_name: str, device_name: str, entity_id: str, 
             command: str, expected_states: List[str], timeout: int = 15, 
             precondition_met: bool = True) -> bool:
    """Run a single test with full verification"""
    
    if not precondition_met:
        results.log_skip(test_name, "Dependency failed")
        return False

    log(f"\n--- {test_name} ---")
    start_time = time.time()
    
    success, status_code, _ = send_command(command)
    
    if not success:
        results.log_fail(test_name, f"API error: HTTP {status_code}", "200", str(status_code))
        return False
    
    final_state, state_info = wait_for_state(entity_id, expected_states, timeout)
    duration = time.time() - start_time
    
    if final_state in expected_states:
        results.log_pass(test_name, duration, state_info)
        return True
    else:
        results.log_fail(test_name, "State mismatch", str(expected_states), final_state, state_info)
        return False

def test_music_playback(device_name: str, config: dict):
    """Test music playback via Music Assistant"""
    entity_id = config["music_entity"]
    power_id = config["turn_off_entity"]

    log(f"\n{'='*80}")
    log(f"MUSIC ASSISTANT TESTS: {device_name}")
    log(f"{'='*80}")
    
    force_device_off(power_id)
    time.sleep(2)
    
    play_success = run_test(f"{device_name} - Play Music", device_name, entity_id, f"Play Brandon Lake on {device_name}", ['playing', 'buffering'], timeout=30)
    time.sleep(3)
    
    # Volume Test (Music) - If playing, verify volume change
    if play_success:
        test_volume_control(device_name, entity_id)

    run_test(f"{device_name} - Pause Music", device_name, entity_id, f"Pause music on {device_name}", ['paused'], timeout=15, precondition_met=play_success)
    time.sleep(2)
    
    run_test(f"{device_name} - Resume Music", device_name, entity_id, f"Resume music on {device_name}", ['playing'], timeout=15, precondition_met=play_success)
    time.sleep(2)
    
    run_test(f"{device_name} - Skip Track", device_name, entity_id, f"Skip to next song on {device_name}", ['playing'], timeout=15, precondition_met=play_success)
    time.sleep(2)

    run_test(f"{device_name} - Stop Music", device_name, entity_id, f"Stop music on {device_name}", ['idle', 'off', 'paused'], timeout=15, precondition_met=play_success)
    
    force_device_off(power_id)
    time.sleep(2)
    capture_docker_logs()

def test_volume_control(device_name: str, entity_id: str):
    log(f"\n--- {device_name} - Volume Tests ---")
    send_command(f"Set volume to 25% on {device_name}")
    time.sleep(5) # Wait for propagation
    state = get_detailed_state(entity_id)
    vol = state.get("volume")
    
    if vol is not None and 0.23 <= vol <= 0.27:
         results.log_pass(f"{device_name} - Set Volume 25%", 0, state)
    else:
         results.log_fail(f"{device_name} - Set Volume 25%", f"Volume: {vol}", "0.25", str(vol), state)

def test_video_playback(device_name: str, config: dict):
    """Test video playback via YouTube"""
    entity_id = config["video_entity"] # Use VIDEO entity (TV)
    power_id = config["turn_off_entity"]

    log(f"\n{'='*80}")
    log(f"YOUTUBE VIDEO TESTS: {device_name}")
    log(f"{'='*80}")
    
    force_device_off(power_id)
    time.sleep(2)
    
    play_success = run_test(f"{device_name} - Play Video", device_name, entity_id, f"Watch Big Buck Bunny on YouTube with {device_name}", ['playing', 'buffering', 'on'], timeout=40)
    time.sleep(3)
    
    run_test(f"{device_name} - Pause Video", device_name, entity_id, f"Pause video on {device_name}", ['paused'], timeout=15, precondition_met=play_success)
    time.sleep(2)
    
    run_test(f"{device_name} - Resume Video", device_name, entity_id, f"Resume video on {device_name}", ['playing'], timeout=15, precondition_met=play_success)
    time.sleep(2)
    
    run_test(f"{device_name} - Stop Video", device_name, entity_id, f"Stop video on {device_name}", ['idle', 'off', 'paused'], timeout=15, precondition_met=play_success)
    
    force_device_off(power_id)
    time.sleep(2)
    capture_docker_logs()

def main():
    print("\n" + "="*80)
    print("COMPREHENSIVE MEDIA PLAYBACK TEST SUITE - DYNAMIC DISCOVERY")
    print("="*80)
    
    # Dynamic Discovery
    devices_map = discover_devices()
    if not devices_map:
        log("No devices discovered or API failed. Exiting.", "ERROR")
        sys.exit(1)
      # Pre-flight checks
    log("\nPre-flight checks...", "INFO")
    
    # BLOCK until API is healthy
    api_up = False
    for i in range(30): # 60 seconds
         if check_api_health():
              api_up = True
              log("API is Healthy.", "SUCCESS")
              break
         log(f"Waiting for API to come up... ({i+1}/30)", "WARNING")
         time.sleep(2)
         
    if not api_up:
        log("FATAL: API did not come up after 60 seconds. Aborting tests.", "ERROR")
        sys.exit(1)
    capture_docker_logs()
    
    for device_name, config in devices_map.items():
        log(f"\n\n{'#'*80}")
        log(f"Testing Device: {device_name}")
        log(f"{'#'*80}")
        
        test_music_playback(device_name, config)
        test_video_playback(device_name, config)
    
    log("\nCapturing final logs...", "INFO")
    capture_docker_logs()
    results.write_logs()
    results.print_summary()
    sys.exit(0 if results.tests_failed == 0 else 1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        results.write_logs()
        sys.exit(130)
    except Exception as e:
        log(f"FATAL: {e}", "ERROR")
        sys.exit(1)
