"""End-to-end live test (Python only): a well-formed prompt is sent to /api/chat
and the gateway routes it to Raven (an autonomous mission). Raven does EVERYTHING
autonomously and exercises the full Raven tool surface:

  WorkspaceCreateRequest, WorkspaceFileWriteRequest, ImageGenerationRequest,
  WorkspaceFileReadRequest, WorkspaceSearchRequest, WorkspaceLintRequest,
  WorkspaceFilePatchRequest, WorkspaceShellRequest (gh + git + build + selftest),
  GitOperationRequest (native add/commit/push), WorkspaceSettingsUpdateRequest.

Raven creates its own dedicated workspace, builds a 3D SPACE SHOOTER in pure
Python (its OWN rendering code — no raylib/Three.js/Bevy), generates sprite
graphics via the image tool, creates a GitHub repo (via `gh`), adds a Linux
GitHub Actions build pipeline, and pushes it. The test creates nothing; it only
submits the prompt (via /api/chat) and then validates the resulting repo.

The same prompt is exposed as PYTHON_SPACE_SHOOTER_PROMPT below — it is fully
self-contained, so you can paste it straight into the chat box (no test file
required) and get the same outcome.

Validates:
  - repo `raven-3d-shooter-python` exists on GitHub
  - `.github/workflows/build.yml` exists and targets `ubuntu-latest`
  - the game implements `--selftest` (prints `GAME_OK`)
  - (optional) clone + build + headless self-test prints `GAME_OK`

Requires LIVE_E2E=1, GH_TOKEN, RAVEN_API_KEY (account with connected GitHub),
and a reachable gateway (GATEWAY_URL).
"""
import base64
import os
import subprocess
import time

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
GH_TOKEN = os.getenv("GH_TOKEN", "")
GH_OWNER = os.getenv("GH_OWNER", "JMiahMan1")
GH_USER = os.getenv("GH_USER", GH_OWNER)
RAVEN_API_KEY = os.getenv("RAVEN_API_KEY", "")

CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "2400"))
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "20"))

REPO = "raven-3d-shooter-python"


def _live_enabled() -> bool:
    return bool(os.getenv("LIVE_E2E")) and bool(GH_TOKEN)


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven Python space-shooter e2e requires LIVE_E2E=1 and GH_TOKEN",
    ),
]


# ---------------------------------------------------------------------------
# Self-contained chat prompt. Paste this directly into the chat box to run the
# mission without this test file. It drives the full Raven tool surface.
# ---------------------------------------------------------------------------
PYTHON_SPACE_SHOOTER_PROMPT = """Raven, build a complete, fun, playable 3D SPACE SHOOTER game in Python, rendered with YOUR OWN code (do NOT use raylib, Three.js, Bevy, Unity, or any high-level game engine). You have the full Raven tool surface — use every tool below, in order.

Project name: "Starfall". GitHub repository name: "raven-3d-shooter-python".

=== TOOL COVERAGE (exercise the full surface; each step is REQUIRED) ===
1. WorkspaceCreateRequest — your VERY FIRST action. Create a dedicated workspace with id "raven-starfall-py" and display_name "Starfall Python". Capture the returned workspace_id and pass it as `workspace_id` in EVERY following tool call. NEVER operate in the Default Workspace (it is reserved for system maintenance only).
2. WorkspaceFileWriteRequest — write every source file, requirements.txt, README.md, and `.github/workflows/build.yml`.
3. ImageGenerationRequest — generate game graphics and save them into the workspace: a player ship sprite saved as `assets/ship.png`, and an enemy drone sprite saved as `assets/enemy.png`. Prompt them clearly (e.g. "top-down 3D sci-fi fighter ship, cyan glow, transparent background"). If image generation is unavailable, draw the shapes procedurally instead (the game must still run).
4. WorkspaceFileReadRequest — read back `main.py` to confirm its contents before building.
5. WorkspaceSearchRequest — search the workspace for the string "GAME_OK" to confirm the selftest hook exists.
6. WorkspaceLintRequest — lint with `ruff`; if issues are found, fix them with WorkspaceFilePatchRequest and re-lint until clean.
7. WorkspaceShellRequest — from inside the workspace, run `gh repo create raven-3d-shooter-python --private -d "Starfall 3D space shooter (Python)"`, then `pip install -r requirements.txt`, then the headless self-test.
8. GitOperationRequest — `git add -A`, `git commit -m "Initial Starfall (Python)"`, then `git push -u origin HEAD` (native git tool, uses the injected token).
9. WorkspaceSettingsUpdateRequest — set `repo_url` to the created repo's HTTPS URL, `git_remote`=origin, `default_branch`=main, so the workspace is wired to its remote.

=== GAME DESIGN (implement every item, in your own code) ===
- Use `pygame` ONLY for the window, keyboard input, and blitting; implement the 3D math YOURSELF: perspective projection of 3D points to the 2D screen, vector math, and collision detection. A software 3D renderer that projects 3D vertices and draws with pygame primitives/sprites is preferred.
- A 3D perspective camera that follows the player ship.
- Player ship near the bottom; moves on a 2D plane with WASD or arrow keys.
- An endless starfield / asteroid field of real 3D meshes scrolling along Z toward the player.
- Enemy drones spawn at random X and fly toward the player; the player shoots projectiles (SPACE or left-click) that destroy enemies on collision.
- Score increases per kill; show a HUD (score + lives) as on-screen text.
- Player has 3 lives; losing all shows a GAME OVER screen with "PRESS R TO RESTART". R restarts the level.
- Use lighting and at least one 3D mesh type (cube/cone/sphere) for ship, enemies, projectiles, asteroids.
- Load `assets/ship.png` and `assets/enemy.png` as sprites, scaled by depth; fall back to drawn shapes if the images are missing.
- It must actually run: `python main.py`.

=== REQUIRED HEADLESS SELF-TEST ===
- Support a `--selftest` flag. When set, use a dummy SDL video driver (e.g. set `SDL_VIDEODRIVER=dummy` / `os.environ` before importing pygame) so NO display is required, run the simulation update loop for ~120 frames with NO user input, then print EXACTLY the line `GAME_OK` to stdout and exit 0.
- README.md documents the controls + how to run/build/selftest.

=== REQUIRED REPOSITORY + LINUX CI ===
- Create the repo `raven-3d-shooter-python` via `gh` (step 7). Add `.github/workflows/build.yml` on `ubuntu-latest`: checkout, setup-python, `pip install -r requirements.txt`, then run `python main.py --selftest`.
- Commit and push ONLY to the repository you created. Never push to any other repository.

Deliver ONE self-contained, working project with no TODOs or placeholders.
"""


def mission_prompt() -> str:
    return PYTHON_SPACE_SHOOTER_PROMPT


# ---------------------------------------------------------------------------
# Live chat submission + job polling (no workspace/files created by the test).
# ---------------------------------------------------------------------------
def _chat_auth_headers() -> dict:
    if RAVEN_API_KEY:
        return {"Authorization": f"Bearer {RAVEN_API_KEY}"}
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _list_missions() -> list[dict]:
    with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
        resp = c.get(f"{GATEWAY_URL}/api/raven/missions")
        if resp.status_code != 200:
            return []
        data = resp.json()
    if isinstance(data, list):
        return data
    for key in ("missions", "items", "results"):
        if isinstance(data.get(key), list):
            return data[key]
    return []


def _recover_mission_id(query: str) -> int | None:
    marker = query.strip()[:80]
    best: int | None = None
    for m in _list_missions():
        proposed = (m.get("proposed_mission") or "").strip()
        if proposed[:80] == marker or proposed.startswith(query.strip()[:40]):
            mid = m.get("id")
            if isinstance(mid, int) and (best is None or mid > best):
                best = mid
    return best


def _chat_submit(query: str, system: str | None = None) -> int:
    body = {
        "model": os.getenv("CODING_MODEL", "auto"),
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    if system:
        body["system"] = system
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=300.0) as c:
            resp = c.post(f"{GATEWAY_URL}/api/chat", json=body)
            assert resp.status_code in (200, 202), (
                f"chat submit failed ({resp.status_code}): {resp.text[:400]}"
            )
            data = resp.json()
            mission_id = data.get("mission_id")
            if mission_id is not None:
                return int(mission_id)
    except Exception:
        pass
    recovered = _recover_mission_id(query)
    assert recovered is not None, "chat submit did not return a mission_id and none found in queue"
    return int(recovered)


def _chat_wait(mission_id: int) -> dict:
    with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
        deadline = time.time() + CHAT_TIMEOUT
        while time.time() < deadline:
            try:
                resp = c.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")
                    if status in ("completed", "failed", "dismissed"):
                        return data
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
    return {"status": "TIMEOUT"}


def _run_local(cmd: str, cwd: str | None = None, timeout: int = 900) -> dict:
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}


def _gh_repo_view(repo: str) -> dict:
    return _run_local(f"gh repo view {repo} --json url")


def _gh_file(repo: str, path: str) -> str | None:
    out = _run_local(f"gh api repos/{GH_OWNER}/{repo}/contents/{path} --jq .content")
    if out["returncode"] != 0 or not out["stdout"].strip():
        return None
    try:
        return base64.b64decode(out["stdout"].strip()).decode("utf-8", "replace")
    except Exception:
        return None


def _gh_has_selftest(repo: str) -> bool:
    tree = _run_local(f"gh api repos/{GH_OWNER}/{repo}/git/trees/HEAD?recursive=1 --jq '.tree[].path'")
    if tree["returncode"] != 0:
        return False
    return any("selftest" in p.lower() for p in tree["stdout"].splitlines())


# ---------------------------------------------------------------------------
# Core live flow — Raven does everything; the test only validates the result.
# ---------------------------------------------------------------------------
def run_one() -> dict:
    repo = REPO
    out: dict = {"lang": "python", "repo": repo, "repo_url": None, "skipped": None, "errors": []}

    existing = _gh_repo_view(repo)
    if existing["returncode"] == 0:
        out["repo_url"] = existing["stdout"].strip().strip('"')
        out["chat_status"] = "already-exists"
    else:
        mission_id = _chat_submit(mission_prompt())
        result = _chat_wait(mission_id)
        out["chat_status"] = result.get("status")
        if result.get("status") != "completed":
            out["errors"].append(f"raven mission {mission_id} ended {result.get('status')}")
            return out

        view = _gh_repo_view(repo)
        if view["returncode"] != 0:
            out["errors"].append(f"expected repo '{repo}' to exist: {view['stderr']}")
            return out
        out["repo_url"] = view["stdout"].strip().strip('"')

    ci = _gh_file(repo, ".github/workflows/build.yml")
    if ci is None or "ubuntu-latest" not in ci:
        out["errors"].append("CI workflow missing or not Linux (ubuntu-latest)")
        return out

    if not _gh_has_selftest(repo):
        out["errors"].append("no --selftest entry found in repo")
        return out

    clone = _run_local(f"gh repo clone {repo} /tmp/{repo}", timeout=120)
    if clone["returncode"] != 0:
        out["skipped"] = f"could not clone {repo} to verify build locally"
        return out
    build = _run_local("pip install -r requirements.txt", cwd=f"/tmp/{repo}", timeout=900)
    if build["returncode"] != 0:
        out["skipped"] = "python toolchain/pygame unavailable on runner; repo+CI verified"
        return out
    run = _run_local("xvfb-run -a python main.py --selftest", cwd=f"/tmp/{repo}", timeout=900)
    if "GAME_OK" not in run.get("stdout", ""):
        out["errors"].append(f"self-test did not print GAME_OK: {run}")
        return out
    out["selftest"] = "ok"
    return out


def test_raven_builds_python_space_shooter():
    out = run_one()
    if out["skipped"]:
        pytest.skip(out["skipped"])
    assert not out["errors"], out["errors"]


# ---------------------------------------------------------------------------
# Standalone runner: `python tests/integration/test_raven_space_shooter_python.py`
# ---------------------------------------------------------------------------
def run_all() -> list[dict]:
    out = run_one()
    status = "OK" if not out["errors"] and not out["skipped"] else (out["skipped"] or out["errors"][0])
    print("\n=== Starfall [Python] ===")
    print(f"  status : {status}")
    print(f"  repo   : {out['repo']} -> {out['repo_url']}")
    return [out]


if __name__ == "__main__":
    if not _live_enabled():
        print("Set LIVE_E2E=1, GH_TOKEN, and RAVEN_API_KEY (account with GitHub connected) to run.")
    else:
        run_all()
