import os
import subprocess
import time
import sys
import glob

# Configuration
TEST_DIR = "test/integration"
TEMP_DIR = "temp"
API_URL = "http://ai.local:11435"
GLOBAL_TIMEOUT = 1200 # 20 minutes max per suite run (failsafe)

def run_test_script(script_name):
    """
    Runs a test script using shell redirection to capture all output (stdout + stderr)
    directly to a file. This avoids python subprocess buffering issues.
    """
    log_file = os.path.join(TEMP_DIR, f"remote_{script_name.replace('.py', '.txt')}")
    cmd = f"python3 -u {os.path.join(TEST_DIR, script_name)} > \"{log_file}\" 2>&1"
    
    print(f"\n>>> STARTING {script_name} (Log: {log_file}) <<<", flush=True)
    
    # Start the process non-blocking
    # We use explicit shell=True for the redirection to work
    process = subprocess.Popen(cmd, shell=True, env={**os.environ, "API_URL": API_URL})
    
    return process, log_file

def tail_file(filename, n_lines=5):
    """Reads the last n_lines of a file safely."""
    if not os.path.exists(filename):
        return []
    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
            return lines[-n_lines:]
    except:
        return []

def main():
    # Ensure temp dir exists
    os.makedirs(TEMP_DIR, exist_ok=True)
    
    scripts = [
        "test_system_admin.py", # New (to be created)
        "test_device_routes.py", # New (to be created)
        "test_media_api.py",
        "test_timers.py",
        "test_notes.py",
        "test_calendar.py",
        "test_web_search.py",
        "test_music_info.py",
        "test_advanced_features.py"
    ]
    
    # Filter only existing scripts
    existing_scripts = []
    for s in scripts:
        if os.path.exists(os.path.join(TEST_DIR, s)):
            existing_scripts.append(s)
        else:
            print(f"⚠️  WARNING: Script {s} not found. Skipping.", flush=True)
            
    if not existing_scripts:
        print("❌ No test scripts found!", flush=True)
        sys.exit(1)

    print(f"🚀 Launching Test Suite ({len(existing_scripts)} scripts)...", flush=True)
    
    failed_scripts = []
    passed_count = 0
    
    for script in existing_scripts:
        start_time = time.time()
        proc, log_file = run_test_script(script)
        
        # Poll for completion
        while proc.poll() is None:
            time.sleep(2)
            # Optional: unexpected long hang check could go here
            if time.time() - start_time > 300: # 5 min individual timeout
                print(f"❌ TIMEOUT: {script} exceeded 300s. Killing...", flush=True)
                proc.kill()
                break
        
        duration = time.time() - start_time
        
        # Check result
        # Note: with shell=True, proc.returncode might be the shell's exit code. 
        # Ideally, if python fails, the shell returns non-zero.
        if proc.returncode == 0:
            print(f"✅ PASS: {script} ({duration:.1f}s)", flush=True)
            passed_count += 1
        else:
            print(f"❌ FAIL: {script} ({duration:.1f}s) - Exit Code: {proc.returncode}", flush=True)
            print("--- LOG TAIL ---", flush=True)
            print("".join(tail_file(log_file, 10)), flush=True)
            print("----------------", flush=True)
            failed_scripts.append(script)
            
    print("\n========================================")
    print(f"SUMMARY: {passed_count}/{len(existing_scripts)} Passed")
    if failed_scripts:
        print(f"FAILED: {', '.join(failed_scripts)}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
