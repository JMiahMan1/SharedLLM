#!/usr/bin/env python3
"""
Mission: Build a Python Space Shooter Game (Pygame)

A classic arcade space shooter for the family - playable on desktop/tablet.
Raven will create the game, test it, and push to GitHub.
"""

import os
import sys
import time

import httpx

MISSION_SPEC = {
    "query": """
Build a Python space shooter game using pygame.

## Game Design
- **Genre**: Classic vertical scrolling shooter (Galaga/Galaxian style)
- **Player**: Spaceship at bottom, moves left/right, shoots up
- **Enemies**: Waves of aliens with different patterns (straight, zigzag, diving)
- **Power-ups**: Rapid fire, spread shot, shield, extra life
- **Scoring**: Points for kills, wave bonuses, combo multipliers
- **Levels**: 5 waves increasing in difficulty, then infinite survival
- **Controls**: Arrow keys / WASD to move, Space to shoot, P to pause

## Technical Requirements
1. **Workspace**: Create dedicated workspace via WorkspaceCreateRequest
2. **GitHub**: Private repo via gh tool, commit early and often
3. **Code Structure**:
   - main.py: Game loop, state machine (menu/playing/paused/gameover)
   - entities.py: Player, Enemy, Bullet, PowerUp classes
   - waves.py: Wave definitions and spawning logic
   - audio.py: Sound effects via pygame.mixer (generate procedurally if no assets)
   - config.py: Constants, difficulty settings
4. **Assets**: Procedural graphics (pygame.draw), generated sounds
5. **Testing**: Self-test mode (--selftest) runs 30s simulation, verifies no crashes
6. **Packaging**: requirements.txt, README.md with controls

## Implementation Steps
1. WorkspaceCreateRequest (id: pantry-chef, display_name: "Pantry Chef Game")
2. gh repo create pantry-chef --private
3. Write core game files in batches (entities → waves → main → audio)
4. WorkspaceShellRequest: pip install pygame, run --selftest
5. WorkspaceLintRequest: ruff check
6. git add/commit/push after each major milestone
7. Final: WorkspaceExposePortRequest for port 8080 (if web build via pygbag)

## Success Criteria
- [ ] Game runs without errors for 60+ seconds in self-test
- [ ] All 5 waves completable, survival mode works
- [ ] Power-ups spawn and function correctly
- [ ] Score persists, high score saved to workspace file
- [ ] Clean GitHub history with descriptive commits
- [ ] README.md with controls and deployment notes
- [ ] No lint errors (ruff clean)
""",
    "slug": "space-shooter-python",
    "priority": 1,
}



def _mission_user() -> str:
    """The username (or id) that owns the mission, from MISSION_USER."""
    user = os.getenv("MISSION_USER", "").strip()
    if not user:
        print("ERROR: MISSION_USER required (the user who owns the mission)")
        sys.exit(1)
    return user

def dispatch_mission(gateway_url: str, internal_secret: str, mission: dict = MISSION_SPEC) -> dict:
    headers = {"X-Internal-Secret": internal_secret, "X-User-Id": _mission_user(), "Content-Type": "application/json"}
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(f"{gateway_url}/api/raven/missions", json=mission, headers=headers)
        resp.raise_for_status()
        return resp.json()


def wait_for_mission(gateway_url: str, internal_secret: str, mission_id: int, timeout: int = 1800) -> dict:
    headers = {"X-Internal-Secret": internal_secret, "X-User-Id": _mission_user()}
    deadline = time.time() + timeout
    with httpx.Client(timeout=30.0) as client:
        while time.time() < deadline:
            resp = client.get(f"{gateway_url}/api/raven/missions/{mission_id}", headers=headers)
            if resp.status_code == 200:
                m = resp.json()
                status = m.get("status")
                print(f"  Mission {mission_id}: {status} - {m.get('current_step', '')}")
                if status in ("completed", "failed", "dismissed"):
                    return m
            time.sleep(15)
    return {"status": "timeout"}


def main():
    gateway_url = os.getenv("GATEWAY_URL", "http://ai.local:8080")
    internal_secret = os.getenv("INTERNAL_SECRET")
    if not internal_secret:
        print("ERROR: INTERNAL_SECRET required")
        sys.exit(1)

    print(f"Dispatching: {MISSION_SPEC['slug']}")
    result = dispatch_mission(gateway_url, internal_secret, MISSION_SPEC)
    mission_id = result.get("mission", {}).get("id")
    print(f"Mission #{mission_id} dispatched")

    final = wait_for_mission(gateway_url, internal_secret, mission_id)
    print(f"Final: {final.get('status')}")
    if final.get("status") != "completed":
        sys.exit(1)


if __name__ == "__main__":
    main()
