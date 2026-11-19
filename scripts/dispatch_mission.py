#!/usr/bin/env python3
"""
Dispatch a Raven mission to the live server.

Usage:
    export GATEWAY_URL=http://<server>:8080      # or pass --gateway
    export INTERNAL_SECRET=<from .env on server> # or DEPLOY_HOST=user@host to read it over ssh
    python scripts/dispatch_mission.py missions/space_shooter_python.py
    python scripts/dispatch_mission.py missions/meal_planner_game.py

Or with custom mission:
    python scripts/dispatch_mission.py --query "Build a todo app" --slug "todo-app"
"""

import os
import sys
import json
import time
import argparse
import httpx
from pathlib import Path

# Live server configuration: from the environment, never written here.
DEFAULT_GATEWAY_URL = os.getenv("GATEWAY_URL", "")
DEPLOY_HOST = os.getenv("DEPLOY_HOST", "")
DEPLOY_DIR = os.getenv("DEPLOY_DIR", "SharedLLM")


def load_mission_spec(path: str) -> dict:
    """Load mission spec from a Python file."""
    # Execute the file and extract MISSION_SPEC
    namespace = {}
    with open(path, 'r') as f:
        exec(f.read(), namespace)
    return namespace.get("MISSION_SPEC", {})


def dispatch_mission(gateway_url: str, internal_secret: str, mission: dict) -> dict:
    headers = {"X-Internal-Secret": internal_secret, "Content-Type": "application/json"}
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(f"{gateway_url}/api/raven/missions", json=mission, headers=headers)
        resp.raise_for_status()
        return resp.json()


def wait_for_mission(gateway_url: str, internal_secret: str, mission_id: int, timeout: int = 3600) -> dict:
    headers = {"X-Internal-Secret": internal_secret}
    deadline = time.time() + timeout
    
    with httpx.Client(timeout=30.0) as client:
        print(f"Polling mission {mission_id} (timeout: {timeout}s)...")
        while time.time() < deadline:
            resp = client.get(f"{gateway_url}/api/raven/missions/{mission_id}", headers=headers)
            if resp.status_code == 200:
                m = resp.json()
                status = m.get("status", "unknown")
                step = m.get("current_step", "")
                print(f"  [{time.strftime('%H:%M:%S')}] Mission {mission_id}: {status} - {step}")
                if status in ("completed", "failed", "dismissed", "killed"):
                    return m
            elif resp.status_code == 404:
                print(f"  Mission {mission_id} not found (may be starting up)")
            else:
                print(f"  Error: HTTP {resp.status_code}")
            time.sleep(20)
    
    return {"status": "timeout", "mission_id": mission_id}


def get_internal_secret() -> str:
    """Get INTERNAL_SECRET from environment or server .env"""
    secret = os.getenv("INTERNAL_SECRET")
    if secret:
        return secret
    
    # Try to read from server
    if not DEPLOY_HOST:
        return ""
    import subprocess
    try:
        result = subprocess.run(
            ["ssh", DEPLOY_HOST, f"grep INTERNAL_SECRET {DEPLOY_DIR}/.env | cut -d= -f2"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    
    return ""


def main():
    parser = argparse.ArgumentParser(description="Dispatch Raven mission to live server")
    parser.add_argument("mission_file", nargs="?", help="Path to mission Python file")
    parser.add_argument("--query", help="Custom mission query")
    parser.add_argument("--slug", help="Mission slug")
    parser.add_argument("--priority", type=int, default=1, help="Priority (1=high, 2=normal)")
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY_URL, help="Gateway URL")
    parser.add_argument("--no-wait", action="store_true", help="Don't wait for completion")
    parser.add_argument("--timeout", type=int, default=3600, help="Timeout in seconds")
    
    args = parser.parse_args()
    if not args.gateway:
        parser.error("no gateway URL: pass --gateway or set GATEWAY_URL")
    
    internal_secret = get_internal_secret()
    if not internal_secret:
        print("ERROR: INTERNAL_SECRET not found. Set it, or DEPLOY_HOST (user@host) for ssh access to the server.")
        sys.exit(1)
    
    # Build mission spec
    if args.mission_file:
        mission = load_mission_spec(args.mission_file)
        print(f"Loaded mission from {args.mission_file}: {mission.get('slug', 'unknown')}")
    elif args.query:
        mission = {
            "query": args.query,
            "slug": args.slug or args.query.lower().replace(" ", "-")[:50],
            "priority": args.priority,
        }
    else:
        parser.error("Either mission_file or --query required")
    
    print(f"Gateway: {args.gateway}")
    print(f"Mission: {mission.get('slug')}")
    print(f"Priority: {mission.get('priority', 1)}")
    print()
    
    # Dispatch
    print("Dispatching mission...")
    result = dispatch_mission(args.gateway, internal_secret, mission)
    mission_data = result.get("mission", {})
    mission_id = mission_data.get("id")
    
    if not mission_id:
        print(f"ERROR: Failed to dispatch: {result}")
        sys.exit(1)
    
    print(f"✅ Mission #{mission_id} dispatched!")
    print(f"Track: {args.gateway}/api/raven/missions/{mission_id}")
    print(f"Logs:  {args.gateway}/api/raven/missions/{mission_id}/logs")
    print(f"Stream: {args.gateway}/api/raven/missions/{mission_id}/stream")
    print()
    
    if args.no_wait:
        print("Not waiting (--no-wait). Check status manually.")
        return
    
    # Wait for completion
    final = wait_for_mission(args.gateway, internal_secret, mission_id, args.timeout)
    
    print()
    status = final.get("status", "unknown")
    if status == "completed":
        print("🎉 Mission completed successfully!")
        ws = final.get("workspace_id")
        repo = final.get("repo_url")
        if ws:
            print(f"Workspace: {ws}")
        if repo:
            print(f"GitHub repo: {repo}")
    elif status == "failed":
        print(f"❌ Mission failed: {final.get('error', 'unknown error')}")
        sys.exit(1)
    elif status == "killed":
        print("🛑 Mission was killed")
        sys.exit(1)
    elif status == "timeout":
        print("⏱️ Mission timed out (still running)")
        sys.exit(1)
    else:
        print(f"Status: {status}")
        sys.exit(1)


if __name__ == "__main__":
    main()