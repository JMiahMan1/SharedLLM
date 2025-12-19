import requests
import time
import sys
import subprocess

HEALTH_URL = "http://192.168.2.211:11435/health"
MAX_RETRIES = 12 # 2 minutes total (10s * 12) just to be safe
WAIT_SECONDS = 10

def check_health():
    print(f"Checking health at {HEALTH_URL}...")
    try:
        resp = requests.get(HEALTH_URL, timeout=5)
        if resp.status_code == 200:
            print("Server is UP!")
            return True
        else:
            print(f"Server returned status: {resp.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Connection failed: {e}")
    return False

def get_remote_logs():
    print("\nXXX SERVER STARTUP FAILED - FETCHING LOGS XXX")
    try:
        # Fetch last 100 lines to catch startup errors
        cmd = ["ssh", "jeremiah@192.168.2.211", "docker logs --tail 100 unified_rag_api"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        print("--- REMOTE LOGS START ---")
        print(result.stdout)
        print(result.stderr)
        print("--- REMOTE LOGS END ---")
    except Exception as e:
        print(f"Failed to fetch logs: {e}")

print("Waiting for server to become healthy...")
for i in range(MAX_RETRIES):
    if check_health():
        sys.exit(0)
    print(f"Server not ready. Waiting {WAIT_SECONDS}s... ({i+1}/{MAX_RETRIES})")
    time.sleep(WAIT_SECONDS)

# If we get here, we timed out
get_remote_logs()
sys.exit(1)
