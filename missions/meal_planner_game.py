#!/usr/bin/env python3
"""
Mission: Build a "Pantry Chef" Game - A Household Recipe Discovery Tool

This mission creates a simple web game where family members can:
1. Input ingredients they have on hand
2. Get recipe suggestions from the household's recipe collection (Nextcloud/Calibre)
3. Play a "chopped" style challenge: make something with 3 random ingredients
4. Save favorite discoveries back to the household recipe collection

The game runs in a Raven workspace and can be exposed via port forwarding for family access.
"""

import os
import json
import httpx
import time
import sys

# Mission specification for Raven
MISSION_SPEC = {
    "query": """
Build a "Pantry Chef" household recipe discovery game.

## Requirements

### Core Gameplay
1. **Ingredient Input**: Family members enter ingredients they have (text input, comma-separated)
2. **Recipe Matching**: Query the household's recipe collection (Nextcloud Recipes folder, Calibre cookbooks) for matches
3. **Chopped Challenge Mode**: "Here are 3 random ingredients - make something!" with a timer
4. **Save Favorites**: Save discovered recipes to household collection with ratings/notes

### Technical Stack
- **Frontend**: Single HTML file with vanilla JS (mobile-friendly, works on tablets in kitchen)
- **Backend**: Python FastAPI service in workspace (exposed via port forwarding)
- **Data**: Read from Nextcloud/Calibre via execution service tools
- **Persistence**: Save game state and favorites to workspace files

### Implementation Steps
1. Create workspace via WorkspaceCreateRequest
2. Set up GitHub repo via gh tool (private, for version control)
3. Write backend API (FastAPI) with endpoints:
   - GET /api/ingredients/suggest - autocomplete from recipe corpus
   - POST /api/recipes/match - find recipes by ingredients
   - POST /api/chopped/start - generate 3 random ingredients + timer
   - POST /api/favorites - save/load family favorites
4. Write frontend (index.html + app.js) with:
   - Ingredient input with autocomplete
   - Recipe cards with match scores
   - Chopped mode with countdown timer
   - Favorites panel (localStorage + sync to workspace)
5. Add recipe corpus ingestion script (read Nextcloud/Calibre, build search index)
6. Test locally, then push to GitHub
7. Document deployment (port expose via WorkspaceExposePortRequest)

### Household Integration
- Uses execution service: StorageFileReadRequest (Nextcloud recipes), CalibreRequest (cookbooks)
- Uses workspace tools: WorkspaceFileWriteRequest, WorkspaceShellRequest, WorkspaceExposePortRequest
- Uses git/gh tools for version control
- Family accesses via JarvisLab > Workspaces > pantry-chef > Open Port

## Success Criteria
- [ ] Workspace created and GitHub repo initialized
- [ ] Backend runs and serves API on port 8080
- [ ] Frontend loads and works on mobile/tablet
- [ ] Recipe matching returns relevant results from household corpus
- [ ] Chopped mode generates valid ingredient triples
- [ ] Favorites persist across sessions
- [ ] Code pushed to GitHub with clean history
- [ ] Deployment documented in README.md
""",
    "slug": "pantry-chef-game",
    "priority": 2,
    "coding_model": None,  # Use configured default
    "workspace_id": None,  # Let Raven create one
}


def dispatch_mission(gateway_url: str, internal_secret: str, mission: dict = MISSION_SPEC) -> dict:
    """Dispatch a mission to the Raven queue via gateway API."""
    headers = {
        "X-Internal-Secret": internal_secret,
        "Content-Type": "application/json"
    }
    
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{gateway_url}/api/raven/missions",
            json=mission,
            headers=headers
        )
        resp.raise_for_status()
        return resp.json()


def wait_for_mission(gateway_url: str, internal_secret: str, mission_id: int, timeout: int = 1800) -> dict:
    """Poll mission status until completion."""
    headers = {"X-Internal-Secret": internal_secret}
    deadline = time.time() + timeout
    
    with httpx.Client(timeout=30.0) as client:
        while time.time() < deadline:
            resp = client.get(
                f"{gateway_url}/api/raven/missions/{mission_id}",
                headers=headers
            )
            if resp.status_code == 200:
                mission = resp.json()
                status = mission.get("status")
                print(f"  Mission {mission_id}: {status} - {mission.get('current_step', '')}")
                if status in ("completed", "failed", "dismissed"):
                    return mission
            time.sleep(15)
    
    return {"status": "timeout", "mission_id": mission_id}


def main():
    gateway_url = os.getenv("GATEWAY_URL", "http://ai.local:8080")
    internal_secret = os.getenv("INTERNAL_SECRET")
    
    if not internal_secret:
        print("ERROR: INTERNAL_SECRET environment variable required")
        sys.exit(1)
    
    print(f"Dispatching mission to {gateway_url}...")
    print(f"Mission: {MISSION_SPEC['slug']}")
    print()
    
    result = dispatch_mission(gateway_url, internal_secret, MISSION_SPEC)
    mission_data = result.get("mission", {})
    mission_id = mission_data.get("id")
    
    print(f"Mission dispatched: #{mission_id}")
    print(f"Track at: {gateway_url}/api/raven/missions/{mission_id}")
    print()
    
    print("Waiting for completion...")
    final = wait_for_mission(gateway_url, internal_secret, mission_id)
    
    print()
    print(f"Final status: {final.get('status')}")
    if final.get("status") == "completed":
        print("✅ Mission completed successfully!")
        print(f"Workspace: {final.get('workspace_id')}")
        print(f"GitHub repo: {final.get('repo_url')}")
    else:
        print(f"❌ Mission failed: {final.get('error', 'unknown')}")
        sys.exit(1)


if __name__ == "__main__":
    main()