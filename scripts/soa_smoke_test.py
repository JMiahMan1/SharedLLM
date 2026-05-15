import subprocess
import os

def log(msg):
    print(f"[SMOKE] {msg}")

def run():
    log("Starting manual git pull diagnostic...")
    try:
        # We are in /app, but the repo is at /workspace/SharedLLM
        repo_path = "/workspace/SharedLLM"
        log(f"Working directory: {repo_path}")
        
        # 1. Fetch
        log("Running git fetch...")
        res = subprocess.run(["git", "fetch", "origin"], cwd=repo_path, capture_output=True, text=True)
        log(f"Fetch status: {res.returncode}")
        
        # 2. Reset to microservices branch
        log("Running git reset --hard origin/microservices...")
        res = subprocess.run(["git", "reset", "--hard", "origin/microservices"], cwd=repo_path, capture_output=True, text=True)
        log(f"Reset status: {res.returncode}")
        log(f"Reset output: {res.stdout}")
        log(f"Reset error: {res.stderr}")
        
        # 3. Pull (just in case)
        log("Running git pull...")
        res = subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True, text=True)
        log(f"Pull status: {res.returncode}")
        
        log("Git update complete.")
    except Exception as e:
        log(f"ERROR during git update: {e}")

if __name__ == "__main__":
    run()
