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

Devices:
- Office TV (media_player.office_tv_chrome_2)
- Master Bedroom TV (media_player.master_bedroom_tv_2)
- Gracie's TV (media_player.gracies_tv - TODO: verify entity_id)

Test Coverage:
- Music playback via Music Assistant (play, pause, resume, skip, stop)
- Video playback via YouTube (play, pause, resume, stop)
- Auto-power-on functionality
- State consistency verification
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
API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
REMOTE_HOST = "jeremiah@192.168.2.211"
if not HA_URL:
    HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
if not HA_TOKEN:
    HA_TOKEN = os.getenv("HA_TOKEN", "")

API_HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
HA_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

# Devices to test
DEVICES = {
    "Office TV": "media_player.office_tv_chrome_2",
    "Master Bedroom TV": "media_player.master_bedroom_tv_2",
    # "Gracie's TV": "media_player.gracies_tv",  # TODO: Verify entity ID
}

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
        # Write errors
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
        
        # Write detailed logs
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
        print(f"Total Tests: {self.tests_run}")
        print(f"Passed: \033[92m{self.tests_passed}\033[0m")
        print(f"Failed: \033[91m{self.tests_failed}\033[0m")
        if self.tests_run > 0:
            success_rate = self.tests_passed / self.tests_run * 100
            color = "\033[92m" if success_rate >= 80 else "\033[93m" if success_rate >= 50 else "\033[91m"
            print(f"Success Rate: {color}{success_rate:.1f}%\033[0m")
        
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
        "media_position": state_data.get("attributes", {}).get("media_position")
    }

def send_command(query: str, max_retries: int = 1) -> Tuple[bool, int, str]:
    """Send command to API with retry logic
    
    Returns: (success, status_code, response_text)
    """
    log(f"Command: '{query}'")
    
    for attempt in range(max_retries + 1):
        try:
            start_time = time.time()
            resp = requests.post(
                f"{API_URL}/api/chat",
                json={"messages": [{"role": "user", "content": query}]},
                headers=API_HEADERS,
                timeout=60
            )
            duration = time.time() - start_time
            
            success = resp.status_code == 200
            log(f"Response: {resp.status_code} ({duration:.1f}s)", "SUCCESS" if success else "WARNING")
            
            return (success, resp.status_code, resp.text[:200])
            
        except requests.exceptions.Timeout:
            if attempt < max_retries:
                log(f"Timeout, retrying ({attempt + 1}/{max_retries})...", "WARNING")
                time.sleep(2)
            else:
                log("Command timed out", "ERROR")
                return (False, 408, "Timeout")
        except Exception as e:
            log(f"Command failed: {e}", "ERROR")
            return (False, 500, str(e))
    
    return (False, 500, "Max retries exceeded")

def force_device_off(entity_id: str) -> bool:
    """Force device to OFF state via HA API"""
    log(f"Forcing {entity_id} OFF")
    try:
        requests.post(
            f"{HA_URL}/api/services/media_player/turn_off",
            headers=HA_HEADERS,
            json={"entity_id": entity_id},
            timeout=10
        )
        
        # Wait and verify
        for _ in range(15):
            state_data = get_ha_state(entity_id)
            if state_data and state_data.get('state') in ['off', 'standby', 'unavailable']:
                log(f"✅ {entity_id} is OFF", "SUCCESS")
                return True
            time.sleep(1)
            
        log(f"Device did not turn off completely", "WARNING")
        return False
    except Exception as e:
        log(f"Force OFF failed: {e}", "ERROR")
        return False

def wait_for_state(entity_id: str, expected_states: List[str], timeout: int = 20) -> Tuple[str, dict]:
    """Wait for device to reach expected state
    
    Returns: (final_state, state_info)
    """
    if isinstance(expected_states, str):
        expected_states = [expected_states]
    
    log(f"Waiting for state in {expected_states} (max {timeout}s)")
    
    start_time = time.time()
    last_state = None
    
    for _ in range(timeout):
        state_info = get_detailed_state(entity_id)
        if state_info:
            last_state = state_info['state']
            if last_state in expected_states:
                elapsed = time.time() - start_time
                log(f"✅ State reached: {last_state} ({elapsed:.1f}s)", "SUCCESS")
                return (last_state, state_info)
        time.sleep(1)
    
    elapsed = time.time() - start_time
    state_info = get_detailed_state(entity_id) or {}
    current_state = state_info.get('state', 'unknown')
    log(f"❌ Timeout after {elapsed:.1f}s. State: {current_state}", "WARNING")
    return (current_state, state_info)

def run_test(test_name: str, device_name: str, entity_id: str, 
             command: str, expected_states: List[str], timeout: int = 15) -> bool:
    """Run a single test with full verification"""
    
    log(f"\n--- {test_name} ---")
    start_time = time.time()
    
    # Get initial state
    initial_state = get_detailed_state(entity_id)
    results.log_detail("TEST_START", test_name, {"initial_state": initial_state})
    
    # Send command
    success, status_code, response = send_command(command)
    
    if not success:
        duration = time.time() - start_time
        results.log_fail(
            test_name,
            f"API error: HTTP {status_code}",
            expected="200",
            actual=str(status_code),
            state_info=initial_state
        )
        return False
    
    # Verify state change
    final_state, state_info = wait_for_state(entity_id, expected_states, timeout)
    duration = time.time() - start_time
    
    if final_state in expected_states:
        results.log_pass(test_name, duration, state_info)
        return True
    else:
        results.log_fail(
            test_name,
            "State mismatch",
            expected=str(expected_states),
            actual=final_state,
            state_info=state_info
        )
        return False

def test_music_playback(device_name: str, entity_id: str):
    """Test music playback via Music Assistant"""
    log(f"\n{'='*80}")
    log(f"MUSIC ASSISTANT TESTS: {device_name}")
    log(f"{'='*80}")
    
    # Ensure clean start
    force_device_off(entity_id)
    time.sleep(2)
    
    # Test 1: Play Music (tests auto-power-on)
    run_test(
        f"{device_name} - Play Music (Auto-Power-On)",
        device_name, entity_id,
        f"Play Brandon Lake on {device_name}",
        ['playing', 'buffering'],
        timeout=30
    )
    time.sleep(3)
    
    # Test 2: Pause
    run_test(
        f"{device_name} - Pause Music",
        device_name, entity_id,
        f"Pause music on {device_name}",
        ['paused'],
        timeout=15
    )
    time.sleep(2)
    
    # Test 3: Resume
    run_test(
        f"{device_name} - Resume Music",
        device_name, entity_id,
        f"Resume music on {device_name}",
        ['playing'],
        timeout=15
    )
    time.sleep(2)
    
    # Test 4: Skip Track
    run_test(
        f"{device_name} - Skip Track",
        device_name, entity_id,
        f"Skip to next song on {device_name}",
        ['playing'],
        timeout=15
    )
    time.sleep(2)
    
    # Test 5: Stop
    run_test(
        f"{device_name} - Stop Music",
        device_name, entity_id,
        f"Stop music on {device_name}",
        ['idle', 'off', 'paused'],
        timeout=15
    )
    
    # Cleanup
    force_device_off(entity_id)
    time.sleep(2)
    
    # Capture logs after music tests
    capture_docker_logs()

def test_video_playback(device_name: str, entity_id: str):
    """Test video playback via YouTube"""
    log(f"\n{'='*80}")
    log(f"YOUTUBE VIDEO TESTS: {device_name}")
    log(f"{'='*80}")
    
    # Ensure clean start
    force_device_off(entity_id)
    time.sleep(2)
    
    # Test 1: Play YouTube Video (tests auto-power-on)
    run_test(
        f"{device_name} - Play YouTube Video (Auto-Power-On)",
        device_name, entity_id,
        f"Watch Big Buck Bunny on YouTube with {device_name}",
        ['playing', 'buffering'],
        timeout=35
    )
    time.sleep(3)
    
    # Test 2: Pause Video
    run_test(
        f"{device_name} - Pause Video",
        device_name, entity_id,
        f"Pause video on {device_name}",
        ['paused'],
        timeout=15
    )
    time.sleep(2)
    
    # Test 3: Resume Video
    run_test(
        f"{device_name} - Resume Video",
        device_name, entity_id,
        f"Resume video on {device_name}",
        ['playing'],
        timeout=15
    )
    time.sleep(2)
    
    # Test 4: Stop Video
    run_test(
        f"{device_name} - Stop Video",
        device_name, entity_id,
        f"Stop video on {device_name}",
        ['idle', 'off', 'paused'],
        timeout=15
    )
    
    # Cleanup
    force_device_off(entity_id)
    time.sleep(2)
    
    # Capture logs after video tests
    capture_docker_logs()

def main():
    """Run comprehensive test suite"""
    print("\n" + "="*80)
    print("COMPREHENSIVE MEDIA PLAYBACK TEST SUITE - ENHANCED")
    print("="*80)
    print(f"Start Time: {results.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"API: {API_URL}")
    print(f"HA: {HA_URL}")
    print(f"Devices: {', '.join(DEVICES.keys())}")
    print("="*80)
    
    # Pre-flight checks
    log("\nPre-flight checks...", "INFO")
    if not check_api_health():
        log("WARNING: API health check failed - proceeding anyway", "WARNING")
    
    # Capture initial logs
    capture_docker_logs()
    
    # Test each device
    for device_name, entity_id in DEVICES.items():
        log(f"\n\n{'#'*80}")
        log(f"DEVICE: {device_name} ({entity_id})")
        log(f"{'#'*80}")
        
        # Run test suites
        test_music_playback(device_name, entity_id)
        test_video_playback(device_name, entity_id)
    
    # Final log capture
    log("\nCapturing final logs...", "INFO")
    capture_docker_logs()
    
    # Write all logs and print summary
    results.write_logs()
    results.print_summary()
    
    # Exit with appropriate code
    sys.exit(0 if results.tests_failed == 0 else 1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\n\nTest interrupted by user", "WARNING")
        results.write_logs()
        results.print_summary()
        sys.exit(130)
    except Exception as e:
        log(f"\n\nFATAL ERROR: {e}", "ERROR")
        results.log_backend_error("FATAL", str(e))
        results.write_logs()
        sys.exit(1)
