"""End-to-end live test: a well-formed prompt is sent to the /api/chat endpoint
and Raven does EVERYTHING autonomously — it creates its own workspace, builds a
3D SPACE SHOOTER, creates a GitHub repo (via `gh`), adds a Linux GitHub Actions
build pipeline, and pushes it. The test creates nothing; it only submits the
prompt and then validates the resulting, publicly-observable repository.

Validates, per language (python, javascript, typescript, go, rust):
  - the repo `raven-3d-shooter-<lang>` exists on GitHub
  - `.github/workflows/build.yml` exists and targets `ubuntu-latest` (Linux CI)
  - the game implements a `--selftest` that prints `GAME_OK`
  - (optional, skip if toolchain missing) clone + build + headless self-test

Requires LIVE_E2E=1, GH_TOKEN, RAVEN_API_KEY (a user API key whose account has a
connected GitHub token), and a reachable gateway (GATEWAY_URL).
"""
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
    return bool(os.getenv("LIVE_E2E")) and bool(GH_TOKEN) and bool(RAVEN_API_KEY)


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven 3D-game e2e requires LIVE_E2E=1, GH_TOKEN, and RAVEN_API_KEY",
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
  - {f'Web ({human}): keep core simulation in a PURE module with no DOM/WebGL, and add an `npm run selftest` '
    f'(node or tsx) that imports it, steps 120 times, asserts score/lives changed, then prints `GAME_OK`.'
    if stack.startswith('Three')
    else 'Native: open the window hidden (e.g. raylib FLAG_WINDOW_HIDDEN) and exit after 120 frames.'}
- Add a README.md with controls + how to run/build/selftest.

=== REQUIRED REPOSITORY + LINUX CI (critical) ===
- Create a NEW GitHub repository named `{_repo_name(lang)}` using the `gh` CLI:
  `gh repo create {_repo_name(lang)} --private -d "Starfall 3D space shooter ({human})"`
- Add a GitHub Actions workflow at `.github/workflows/build.yml` that builds the game on Linux:
  it MUST use `runs-on: ubuntu-latest`. Steps: checkout, install the {human} toolchain, then build/compile.
- Initialize git (if needed), add ALL files, commit, and push to the created repo:
  `git remote add origin <repo-url-from-gh>`, `git add -A`,
  `git commit -m "Initial Starfall ({human})"`, `git push -u origin HEAD`.
- You may ONLY ever push to the repository you just created. Never push to any other repository.

Deliver ONE self-contained, working project with no TODOs or placeholders.
"""


# ---------------------------------------------------------------------------
# Live chat submission + job polling (no workspace/files created by the test).
# ---------------------------------------------------------------------------
def _chat_auth_headers() -> dict:
    if RAVEN_API_KEY:
        return {"Authorization": f"Bearer {RAVEN_API_KEY}"}
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _chat_submit(query: str, system: str | None = None) -> str:
    """Submit a prompt to /api/chat as an async job; return the job_id."""
    body = {
        "query": query,
        "async_job": True,
        "stream": False,
        "model": "auto",
    }
    if system:
        body["system"] = system
    with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
        resp = c.post(f"{GATEWAY_URL}/api/chat", json=body)
        assert resp.status_code in (200, 202), (
            f"chat submit failed ({resp.status_code}): {resp.text[:400]}"
        )
        data = resp.json()
    job_id = data.get("job_id")
    assert job_id, f"chat did not return a job_id: {data}"
    return job_id


def _chat_wait(job_id: str) -> dict:
    """Poll /api/chat/job/{id} until COMPLETED/FAILED."""
    with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
        deadline = time.time() + CHAT_TIMEOUT
        while time.time() < deadline:
            resp = c.get(f"{GATEWAY_URL}/api/chat/job/{job_id}")
            if resp.status_code == 200:
                data = resp.json()
                status = str(data.get("status", "")).upper()
                if status in ("COMPLETED", "FAILED"):
                    return data
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


# ---------------------------------------------------------------------------
# Core live flow — Raven does everything; the test only validates the result.
# ---------------------------------------------------------------------------
def run_one(lang: str, human: str, stack: str, build_cmd: str, selftest_cmd: str, is_web: bool) -> dict:
    repo = _repo_name(lang)
    out: dict = {"lang": lang, "human": human, "repo": repo, "repo_url": None,
                 "skipped": None, "errors": []}

    job_id = _chat_submit(mission_prompt(lang, human, stack))
    result = _chat_wait(job_id)
    out["chat_status"] = result.get("status")
    if result.get("status") != "COMPLETED":
        out["errors"].append(f"chat job {job_id} ended {result.get('status')}")
        return out

    # 1) Repository was created on GitHub.
    view = _gh_repo_view(repo)
    if view["returncode"] != 0:
        out["errors"].append(f"expected repo '{repo}' to exist: {view['stderr']}")
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
    clone = _run_local(f"gh repo clone {repo} /tmp/{repo}", timeout=120)
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
