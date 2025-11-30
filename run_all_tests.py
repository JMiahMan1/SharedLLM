import subprocess
import sys

def run_script(script_name):
    print(f"\n{'='*40}")
    print(f"RUNNING: {script_name}")
    print(f"{'='*40}\n")
    result = subprocess.run([sys.executable, script_name])
    if result.returncode != 0:
        print(f"\n[!] {script_name} FAILED!")
        sys.exit(result.returncode)

if __name__ == "__main__":
    # 1. Run Advanced Media Routing Tests (Apps, Navigation)
    run_script("test/test_media_api.py")

    # 2. Run Main Integration Tests (Context, Calendar, Basic HA)
    run_script("test/live_test.py")
    
    print("\n\n[+] ALL TESTS PASSED SUCCESSFULLY!")
