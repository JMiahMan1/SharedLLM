
import requests
import sys
import subprocess
import os

LOG_FILE = "diagnose.txt"

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")
    print(msg)

def main():
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    
    url = "http://192.168.2.205:11435/health"
    log(f"Checking {url}...")
    try:
        r = requests.get(url, timeout=5)
        log(f"Status: {r.status_code}")
        log(f"Body: {r.text}")
    except Exception as e:
        log(f"Connection Failed: {e}")
        return

    log("-" * 20)
    log("Running live_test.py...")
    
    env = os.environ.copy()
    env["API_URL"] = "http://192.168.2.205:11435"
    
    try:
        # Run live_test.py and capture output
        proc = subprocess.run(
            [sys.executable, "-u", "test/live_test.py"],
            capture_output=True,
            text=True,
            env=env
        )
        log("STDOUT:")
        log(proc.stdout)
        log("STDERR:")
        log(proc.stderr)
        log(f"Exit Code: {proc.returncode}")
    except Exception as e:
        log(f"Subprocess failed: {e}")

if __name__ == "__main__":
    main()
