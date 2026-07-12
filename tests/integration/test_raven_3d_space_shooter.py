"""End-to-end live test: a well-formed prompt is sent to the /api/chat endpoint
and the gateway routes it to Raven (as an autonomous mission). Raven does
EVERYTHING autonomously - it creates its own workspace, builds a 3D SPACE
SHOOTER, creates a GitHub repo (via `gh`), adds a Linux GitHub Actions build
pipeline, and pushes it. The test creates nothing; it only submits the prompt
(via /api/chat) and then validates the resulting, publicly-observable repo.

The chat request shows up in the Raven queue (/api/raven/missions); the test
polls that endpoint for completion.

Validates, per language (python, javascript, typescript, go, rust):
  - the repo `raven-3d-shooter-<lang>` exists on GitHub
  - `.github/workflows/build.yml` exists and targets `ubuntu-latest` (Linux CI)
  - the game implements a `--selftest` that prints `GAME_OK`
  - (optional, skip if toolchain missing) clone + build + headless self-test

Requires LIVE_E2E=1, GH_TOKEN, RAVEN_API_KEY (a user API key whose account has a
connected GitHub token), and a reachable gateway (GATEWAY_URL).
"""
import json
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

CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "1800"))
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "20"))


def _live_enabled() -> bool:
    # RAVEN_API_KEY is optional: when unset the test sends no auth and the
    # gateway resolves the request to the admin identity (which carries the
    # GitHub token), as confirmed by a live probe.
    return bool(os.getenv("LIVE_E2E")) and bool(GH_TOKEN)


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven 3D-game e2e requires LIVE_E2E=1 and GH_TOKEN",
    ),
]


# (lang, human language, stack, local build cmd, local selftest cmd, is_web)
LANGUAGES = [
    ("python", "Python", "raylib (Python 3D window)",
     "pip install -r requirements.txt",
     "xvfb-run -a python main.py --selftest", False),
    ("javascript", "JavaScript", "Three.js + Vite (WebGL browser)",
     "npm install && npm run build",
     "npm run selftest", True),
    ("typescript", "TypeScript", "Three.js + Vite (WebGL browser)",
     "npm install && npm run build",
     "npm run selftest", True),
    ("go", "Go", "raylib-go (native 3D window)",
     "go mod download && go build ./...",
     "xvfb-run -a go run . --selftest", False),
    ("rust", "Rust", "Bevy (or raylib-rs) native 3D",
     "cargo build",
     "xvfb-run -a cargo run -- --selftest", False),
]

REPO_PREFIX = "raven-3d-shooter-"


def _repo_name(lang: str) -> str:
    return f"{REPO_PREFIX}{lang}"


# ---------------------------------------------------------------------------
# Well-formed chat prompt. The query carries an autonomy signal ("Raven, ...")
# so the gateway routes it to the autonomous AgentLoop (which uses tools to do
# all the work). The full game/CI spec lives here; Raven writes everything.
# ---------------------------------------------------------------------------
def mission_prompt(lang: str, human: str, stack: str) -> str:
    return f"""Raven, build a complete, fun, playable 3D SPACE SHOOTER game in {human} using {stack}.
Project name: "Starfall". You have a workspace, a shell, file tools, the `gh` CLI, and git.

CRITICAL EXECUTION ORDER - perform these steps IN ORDER and do NOT skip ahead or loop on a single file:
Step 0: WorkspaceCreateRequest with id `raven-3d-shooter-{lang}` + a display_name. Capture the returned `workspace_id` and pass it as `workspace_id` to EVERY later WorkspaceFileWriteRequest / WorkspaceShellRequest / WorkspaceSettingsUpdateRequest. NEVER use the Default Workspace.
Step 1: `gh repo create raven-3d-shooter-{lang} --private -d "Starfall 3D space shooter ({human})" 2>&1 || echo REPO_EXISTS`. If REPO_EXISTS, `gh repo clone raven-3d-shooter-{lang} .` (or `git clone <url> .`) inside the workspace.
Step 2: Write these files ONCE (WorkspaceFileWriteRequest, workspace_id set): requirements.txt, main.py, README.md, and .github/workflows/build.yml (rules below). main.py's FIRST line MUST be exactly `#!/usr/bin/env python3` then a docstring, then code - never code before the shebang. Keep main.py <= 400 lines. Write each file exactly once.
Step 3 - PUSH FIRST (mandatory; do this before any polishing): `git init` (if needed) -> `git add -A` -> `git commit -m "Initial Starfall ({human})"` -> `git push -u origin HEAD` (use `git push --force` ONLY if a plain push is rejected). You MUST reach a successful `git push` before doing anything else. Do not proceed past Step 3 until `git push` reports the branch was pushed.
Step 4: ONLY after the push succeeds, run the headless self-test and confirm `GAME_OK`. If it fails, FIX the bug with a NEW WorkspaceFileWrite (overwrite the file) + a NEW `git commit` + `git push`. You may overwrite main.py at most ONE more time (never more than twice total).
Step 5: FINAL VERIFICATION - `gh repo view raven-3d-shooter-{lang}` and confirm your files are on GitHub. Only then report done. If push/verify fails, keep retrying the git steps. Do NOT claim success otherwise.

You may ONLY push to raven-3d-shooter-{lang}. Never push elsewhere.

=== GAME DESIGN (implement every item) ===
- 3D perspective camera that follows the player ship.
- Player ship near the bottom; moves on a 2D plane with WASD or arrow keys.
- An endless starfield / asteroid field scrolls toward the player (real 3D meshes moving along Z).
- Enemy drones spawn at random X and fly toward the player. The player shoots projectiles with
  SPACE or left-click; projectiles travel forward and destroy enemies on collision.
- Score increases per kill; show a HUD (score + lives) as on-screen text.
- Player has 3 lives; losing all shows a GAME OVER screen with "PRESS R TO RESTART". R restarts.
- Use lighting and at least one 3D mesh type (cube/cone/sphere) for ship, enemies, projectiles, asteroids.
- It must actually run: `python main.py` / `npm run dev` / `go run .` / `cargo run`.

=== REQUIRED HEADLESS SELF-TEST (for automated grading) ===
- Support a `--selftest` flag (or `SELFTEST=1`). When set, run the simulation update loop for ~120
  frames with NO user input and NO visible window (hidden/minimized or headless), then print EXACTLY
  the line `GAME_OK` to stdout and exit 0. Never require a display for selftest.
  - Python: use `pygame` and set `SDL_VIDEODRIVER=dummy` (no raylib needed) so it runs
    headless; exit after ~120 frames. For other languages use the native 3D lib.
- CRITICAL SELFTEST RULE (do not violate): Your `run_selftest()` function MUST NOT call any
  windowing or input functions - this includes `InitWindow`, `BeginDrawing`, `EndDrawing`,
  `BeginMode3D`, `EndMode3D`, `IsKeyDown`, `IsKeyPressed`, `IsMouseButtonDown`, or any raylib/pygame
  input/poll function. It must ONLY run your pure game-logic `update()` step (movement, enemy/asteroid
  spawning, bullet motion, collisions, scoring, lives) using FIXED/synthetic input values, then
  `print("GAME_OK")` and `sys.exit(0)`. Importing your rendering library at module load is fine, but
  the selftest code path must never touch the display or keyboard. If `run_selftest` references an
  undefined input symbol (e.g. `IsKeyDown`) it will crash with NameError and never print GAME_OK.
- Do NOT write exploratory/probe scripts (e.g. `_explore.py`) to discover the API - write the game
  directly against the `raylib` Python package. Add a README.md with controls + how to run/build/selftest.

=== REQUIRED WORKSPACE (critical - do this FIRST) ===
- Your VERY FIRST action must be `WorkspaceCreateRequest` with a unique id derived
   from the project (e.g. `raven-3d-shooter-{lang}`) and a `display_name`.
   This workspace id should match the repository name you will create next.
  Capture the returned `workspace_id` and pass it as `workspace_id` in EVERY following
  `WorkspaceFileWriteRequest`, `WorkspaceShellRequest`, and `WorkspaceSettingsUpdateRequest`.
  NEVER operate in the Default Workspace - it is reserved for system maintenance only.
  (This is protocol Step 0; the gateway will reject file/shell/git operations until you
  have acquired a dedicated workspace.)

=== REQUIRED REPOSITORY + LINUX CI (critical) ===
- Create a NEW GitHub repository named `{_repo_name(lang)}` using the `gh` CLI FROM INSIDE
  the dedicated workspace you just created:
  `gh repo create {_repo_name(lang)} --private -d "Starfall 3D space shooter ({human})"`
- If `gh repo create` reports the repository ALREADY exists (e.g. a prior
  run left an empty shell or a previous build), do NOT fail and do NOT create a different
  repo: instead `cd` into your workspace and `gh repo clone {_repo_name(lang)} .`
  (or `git clone <url> .`), overwrite all project files with your new build, then
  `git add -A && git commit -m "Initial Starfall ({human})" && git push -u origin HEAD`
  (use `git push --force` ONLY if a plain push is rejected by the remote).
- Add a GitHub Actions workflow at `.github/workflows/build.yml` that builds AND TESTS the
  game on Linux: it MUST use `runs-on: ubuntu-latest`. Steps: checkout, install the
  {human} toolchain, install a headless display (`sudo apt-get update && sudo apt-get install -y xvfb`
  for native builds), build/compile, then run the self-test and FAIL the job if stdout lacks `GAME_OK`
  (e.g. for native Python: `xvfb-run -a python main.py --selftest | tee selftest.log &&
  grep -q GAME_OK selftest.log`).
- Initialize git (if needed), add ALL files, commit, and push to the created repo:
  `git remote add origin <repo-url-from-gh>`, `git add -A`,
  `git commit -m "Initial Starfall ({human})"`, `git push -u origin HEAD`.
- You may ONLY ever push to the repository you just created. Never push to any other repository.
- FINAL VERIFICATION (do not report done until this passes): after pushing, run
  `gh repo view {_repo_name(lang)}` and `git -C . ls-files` to confirm your files are on
  GitHub. Only then report the mission complete. If the push or verification fails, keep
  retrying the git steps - do NOT claim success.

Deliver ONE self-contained, working project with no TODOs or placeholders.
"""


# ---------------------------------------------------------------------------
# Live chat submission + job polling (no workspace/files created by the test).
# ---------------------------------------------------------------------------
def _chat_auth_headers() -> dict:
    if RAVEN_API_KEY:
        return {"Authorization": f"Bearer {RAVEN_API_KEY}"}
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _live_coding_model() -> str:
    """Pull the coding model from the live gateway/Identity config (the settings of
    the live page) rather than hardcoding a model or defaulting to 'auto'.

    Returns the configured ``coding_model`` string, or "" if it cannot be read
    (the gateway then falls back to its own config DB value).
    """
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
            r = c.get(f"{GATEWAY_URL}/api/config")
            if r.status_code == 200:
                cfg = (r.json() or {}).get("config", {})
                m = cfg.get("coding_model")
                if m:
                    return str(m)
    except Exception:
        pass
    return ""


def _list_missions() -> list[dict]:
    """GET /api/raven/missions (unauthenticated-ish; gateway accepts the same
    internal secret the submit uses). Returns the list of mission records.
    """
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
    """The gateway enqueues the Raven mission server-side but may be slow to
    return the 202 (the in-process worker saturates the event loop). Poll the
    queue for the newest mission whose prompt matches this submission.
    """
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
    """Submit a prompt to Raven as an autonomous mission via the dedicated
    mission API (``POST /api/raven/missions``).

    This endpoint enqueues the mission and returns a clean 200 JSON with the
    ``mission_id`` -- it does NOT stream (unlike ``/api/chat``, which
    streams a long-running Raven task and would block the client for the whole
    run). Raven does all the work; the test only observes the outcome.

    Because the gateway can be slow to return (the in-process Raven worker
    shares the event loop), a submit failure is treated as "mission may
    still have been created server-side" and we recover the id by polling
    the queue.
    """
    body = {
        "query": query,
        "coding_model": _live_coding_model(),
    }
    if system:
        body["system"] = system
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
            resp = c.post(f"{GATEWAY_URL}/api/raven/missions", json=body)
            assert resp.status_code in (200, 201, 202), (
                f"mission submit failed ({resp.status_code}): {resp.text[:400]}"
            )
            data = resp.json()
            mission = data.get("mission") or {}
            mission_id = mission.get("id") or data.get("mission_id")
            if mission_id is not None:
                return int(mission_id)
    except Exception as e:
        print(f"[warn] mission submit error: {e}")
    recovered = _recover_mission_id(query)
    assert recovered is not None, (
        "mission submit did not return a mission_id and none found in queue"
    )
    return int(recovered)


def _chat_wait(mission_id: int) -> dict:
    """Poll /api/raven/missions/{id} until completed/failed/dismissed.

    Survives transient network errors (e.g. a brief gateway flap): a failed
    GET is treated as "still running" and we keep polling until the deadline.
    """
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
                pass  # transient network error; keep polling
            time.sleep(POLL_INTERVAL)
    return {"status": "TIMEOUT"}


def _run_local(cmd: str, cwd: str | None = None, timeout: int = 600) -> dict:
    """Run a command on the test runner (used to validate the public repo)."""
    env = {**os.environ, "GH_TOKEN": GH_TOKEN}
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": f"timed out after {timeout}s"}
    except FileNotFoundError as e:
        return {"returncode": -1, "stdout": "", "stderr": f"command not found: {e}"}


def _gh_repo_view(repo: str) -> dict:
    return _run_local(f"gh repo view {repo} --json url")


def _gh_file(repo: str, path: str) -> str | None:
    """Fetch a repo file's decoded contents via gh api, or None if missing."""
    out = _run_local(f"gh api repos/{GH_OWNER}/{repo}/contents/{path} --jq .content")
    if out["returncode"] != 0 or not out["stdout"].strip():
        return None
    import base64
    try:
        return base64.b64decode(out["stdout"].strip()).decode("utf-8", "replace")
    except Exception:
        return None


def _gh_has_selftest(repo: str) -> bool:
    tree = _run_local(f"gh api repos/{GH_OWNER}/{repo}/git/trees/HEAD?recursive=1 --jq '.tree[].path'")
    if tree["returncode"] != 0:
        return False
    paths = tree["stdout"].splitlines()
    return any("selftest" in p.lower() for p in paths)


def _gh_run_check(repo: str, timeout: int = 1200) -> dict:
    """Wait for the latest GitHub Actions run on the repo to finish, then report
    its conclusion. The CI workflow (written by Raven) is the authoritative
    check that the game actually builds and self-tests (prints GAME_OK).
    """
    deadline = time.time() + timeout
    last: dict | None = None
    while time.time() < deadline:
        out = _run_local(
            f"gh run list --repo {GH_OWNER}/{repo} --limit 1 --json status,conclusion,url,headBranch"
        )
        if out["returncode"] == 0 and out["stdout"].strip():
            try:
                runs = json.loads(out["stdout"])
            except Exception:
                runs = []
            if runs:
                last = runs[0]
                if last.get("status") == "completed":
                    return last
        time.sleep(30)
    return last or {"status": "TIMEOUT"}


# ---------------------------------------------------------------------------
# Core live flow - Raven does everything; the test only validates the result.
# ---------------------------------------------------------------------------
def run_one(lang: str, human: str, stack: str, build_cmd: str, selftest_cmd: str, is_web: bool) -> dict:
    repo = _repo_name(lang)
    out: dict = {"lang": lang, "human": human, "repo": repo, "repo_url": None,
                 "skipped": None, "errors": []}

    # The test NEVER pre-validates or shortcuts. It submits the prompt to the
    # gateway (which routes it to Raven as an autonomous mission) and lets Raven
    # do 100% of the work: create/adopt the workspace, build the game, create
    # the GitHub repo, add CI, commit and push. The test only observes the
    # publicly-visible outcome afterwards.
    mission_id = _chat_submit(mission_prompt(lang, human, stack))
    result = _chat_wait(mission_id)
    out["chat_status"] = result.get("status")
    if result.get("status") != "completed":
        out["errors"].append(f"raven mission {mission_id} ended {result.get('status')}")
        return out

    # 1) Repository was created (by Raven) on GitHub.
    view = _gh_repo_view(repo)
    if view["returncode"] != 0:
        out["errors"].append(f"expected repo '{repo}' to exist after Raven ran: {view['stderr']}")
        return out
    out["repo_url"] = view["stdout"].strip().strip('"')

    # 2) Linux CI pipeline exists and targets ubuntu-latest.
    ci = _gh_file(repo, ".github/workflows/build.yml")
    if ci is None or "ubuntu-latest" not in ci:
        out["errors"].append("CI workflow missing or not Linux (ubuntu-latest)")
        return out

    # 3) Game ships a self-test entry.
    if not _gh_has_selftest(repo):
        out["errors"].append("no --selftest entry found in repo")
        return out

    # 4) Optional: clone + build + headless self-test on the runner.
    clone = _run_local(f"rm -rf /tmp/{repo} && gh repo clone {repo} /tmp/{repo}", timeout=120)
    if clone["returncode"] != 0:
        out["skipped"] = f"could not clone {repo} to verify build locally"
        return out
    build = _run_local(build_cmd, cwd=f"/tmp/{repo}", timeout=900)
    if build["returncode"] != 0:
        out["skipped"] = f"build/toolchain for {human} unavailable on runner; repo+CI verified"
        return out
    run = _run_local(selftest_cmd, cwd=f"/tmp/{repo}", timeout=900)
    if "GAME_OK" not in run.get("stdout", ""):
        out["errors"].append(f"self-test did not print GAME_OK: {run}")
        return out
    out["selftest"] = "ok"

    # 5) Authoritative: the GitHub Actions CI run MUST pass (build + selftest
    #    prints GAME_OK). This is the real "tested and working" gate - Raven's
    #    own workflow proves the game builds and runs on Linux, not just that
    #    files exist.
    ci = _gh_run_check(repo)
    if ci.get("status") != "completed":
        out["errors"].append(f"CI run never completed: {ci}")
        return out
    if ci.get("conclusion") != "success":
        out["errors"].append(
            f"CI run concluded '{ci.get('conclusion')}' (expected success): {ci.get('url')}"
        )
        return out
    out["ci"] = "pass"
    return out


@pytest.mark.parametrize("lang,human,stack,build_cmd,selftest_cmd,is_web", LANGUAGES,
                         ids=[row[0] for row in LANGUAGES])
def test_raven_builds_3d_space_shooter(lang, human, stack, build_cmd, selftest_cmd, is_web):
    out = run_one(lang, human, stack, build_cmd, selftest_cmd, is_web)
    if out["skipped"]:
        pytest.skip(out["skipped"])
    assert not out["errors"], out["errors"]


# ---------------------------------------------------------------------------
# Standalone runner: `python tests/integration/test_raven_3d_space_shooter.py`
# ---------------------------------------------------------------------------
def run_all() -> list[dict]:
    results = []
    for (lang, human, stack, build_cmd, selftest_cmd, is_web) in LANGUAGES:
        print(f"\n=== Starfall [{human}] ===")
        out = run_one(lang, human, stack, build_cmd, selftest_cmd, is_web)
        status = "OK" if not out["errors"] and not out["skipped"] else (out["skipped"] or out["errors"][0])
        print(f"  status : {status}")
        print(f"  repo   : {out['repo']} -> {out['repo_url']}")
        results.append(out)
    print("\n==================== PLAYABLE REPOS ====================")
    for r in results:
        if r["repo_url"]:
            print(f"  {r['lang']:>10}: {r['repo_url']}")
    return results


if __name__ == "__main__":
    if not _live_enabled():
        print("Set LIVE_E2E=1, GH_TOKEN, and RAVEN_API_KEY (account with GitHub connected) to run.")
    else:
        run_all()
