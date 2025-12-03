import subprocess
import sys
import os

def run_script(script_name):
    print(f"\n{'='*40}")
    print(f"RUNNING: {script_name}")
    print(f"{'='*40}\n")
    # CRITICAL: Ensure we run the script from the root directory if necessary or adjust paths
    result = subprocess.run([sys.executable, script_name])
    if result.returncode != 0:
        print(f"\n[!] {script_name} FAILED!")
        sys.exit(result.returncode)

if __name__ == "__main__":
    # 1. Run Advanced Media Routing Tests (Apps, Navigation)
    run_script(os.path.join("test", "test_media_api.py"))

    # 2. Run Main Integration Tests (Context, Calendar, Basic HA)
    run_script(os.path.join("test", "live_test.py"))
    
    # 3. Run Timer/Alarm Tests (NEW)
    run_script(os.path.join("test", "test_timers.py"))
    
    print("\n\n[+] ALL TESTS PASSED SUCCESSFULLY!")
