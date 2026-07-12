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
    ("python", "Python", "pygame (SDL2; headless via SDL_VIDEODRIVER=dummy)",
     "pip install -r requirements.txt",
     "SDL_VIDEODRIVER=dummy python main.py --selftest", False),
    ("javascript", "JavaScript", "Three.js + Vite (WebGL, runs in browser)",
     "npm install && npm run build",
     "npm run selftest", True),
    ("typescript", "TypeScript", "Three.js + Vite (WebGL, runs in browser)",
     "npm install && npm run build",
     "npm run selftest", True),
    ("go", "Go", "raylib-go (native 3D, GLFW/Wayland)",
     "go mod download && go build ./...",
     "xvfb-run -a go run . --selftest", False),
    ("rust", "Rust", "Bevy (wgpu/Wayland) or raylib-rs native 3D",
     "cargo build",
     "xvfb-run -a cargo run -- --selftest", False),
]

REPO_PREFIX = "raven-3d-shooter-"


def _repo_name(lang: str) -> str:
    return f"{REPO_PREFIX}{lang}"


# ---------------------------------------------------------------------------
# Well-formed chat prompt. The query carries an autonomy signal ("Raven, ...")
# so the gateway routes it to the autonomous AgentLoop (which uses tools to do
# all the work). Raven writes the game, repo, CI and docs; the test only checks
# the observable result (repo + Linux CI that prints GAME_OK + shipped binary).
# ---------------------------------------------------------------------------
def mission_prompt(lang: str, human: str, stack: str) -> str:
    return f"""Raven, build a complete, fun, playable 3D SPACE SHOOTER called "Starfall" in {human} using {stack}.

Do 100% of the work yourself — you have a workspace, a shell, file tools, the `gh` CLI and git.

Execution order:
1. Create a dedicated workspace with id `raven-3d-shooter-{lang}` + a display_name. Use its
   `workspace_id` for EVERY file/shell/git call. NEVER use the Default Workspace.
2. `gh repo create raven-3d-shooter-{lang} --private -d "Starfall 3D space shooter ({human})"`.
   If it already exists, `gh repo clone raven-3d-shooter-{lang} .` and overwrite all project files.
3. Write the game, a README.md (install/run/controls), and `.github/workflows/build.yml`.
   Commit and push (only to this repo).
4. Verify with `gh repo view raven-3d-shooter-{lang}` that everything is on GitHub before done.

Game design (implement all):
- 3D perspective camera follows the player ship; player moves on a 2D plane (WASD/arrows).
- Endless starfield/asteroid field scrolling in 3D; enemy drones spawn and approach the player.
- Fire with SPACE/click; projectiles destroy enemies on collision; score + lives HUD.
- 3 lives; GAME OVER with "PRESS R TO RESTART" (R restarts).
- Lighting + 3D meshes (cube/cone/sphere) for ship, enemies, projectiles, asteroids.

Headless self-test (automated grading):
- Support `--selftest` (or `SELFTEST=1`): run the PURE game-logic update loop for ~120 frames with
  synthetic input and NO window, print EXACTLY `GAME_OK`, exit 0. The selftest must NOT open a window
  or read input (no {stack} window/init/input calls) — only your update/movement/collision/scoring
  logic, then `print("GAME_OK")`. Module-level imports of the render lib are fine.

Platform + packaging (important):
- It MUST run on modern Linux with Wayland (e.g. Fedora 42). Python: `SDL_VIDEODRIVER=wayland` (or
  dummy); native builds: rely on GLFW/winit Wayland support; web: any browser.
- Ship an easy run path: a single binary for Go/Rust (`go build` / `cargo build --release`),
  `pip install -r requirements.txt && python main.py` for Python, `npm install && npm run dev` for
  web. Document it clearly in README.md.
- Linux CI (`.github/workflows/build.yml`, `runs-on: ubuntu-latest`): install the {human} toolchain
  AND required system libs (xvfb, libgl1-mesa-dev, libglu1-mesa-dev, pkg-config), build, run the
  headless `--selftest` (fail the job if stdout lacks `GAME_OK`), then do a short real-launch smoke
  test (run the game headless a few seconds and confirm it starts/renders without crashing) to catch
  runtime errors the logic-only selftest misses.

For web (Three.js): provide a headless self-test that exercises the game logic and prints `GAME_OK`
(e.g. a node script or headless-browser run), and have CI fail if `GAME_OK` is absent.

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

    # 4) Best-effort LOCAL clone + build + headless self-test. This is a bonus
    #    verification only — it is SKIPPED (not fatal) when the language toolchain
    #    or a display is unavailable on the test runner. The authoritative gate is
    #    the GitHub Actions CI run in step 5, which needs no local toolchain.
    clone = _run_local(f"rm -rf /tmp/{repo} && gh repo clone {repo} /tmp/{repo}", timeout=120)
    if clone["returncode"] == 0:
        build = _run_local(build_cmd, cwd=f"/tmp/{repo}", timeout=900)
        if build["returncode"] == 0:
            run = _run_local(selftest_cmd, cwd=f"/tmp/{repo}", timeout=900)
            if "GAME_OK" in run.get("stdout", ""):
                out["selftest"] = "ok (local)"
            else:
                out["local_note"] = "local self-test did not print GAME_OK (CI is authoritative)"
        else:
            out["local_note"] = f"build/toolchain for {human} unavailable on runner"
    else:
        out["local_note"] = f"could not clone {repo} locally"

    # 5) Authoritative: the GitHub Actions CI run MUST pass (build + selftest
    #    prints GAME_OK). This is the real "tested and working" gate - Raven's
    #    own workflow proves the game builds and runs on Linux, not just that
    #    files exist. It runs regardless of local toolchain availability.
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
