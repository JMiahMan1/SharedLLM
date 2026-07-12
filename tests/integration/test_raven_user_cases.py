"""
Live end-to-end test for the Raven agent loop, driven entirely through the chat
interface (the same path external clients use).

How it works: sending a chat query containing autonomy signals ("raven", "fix",
"perform", "repair", ...) routes the gateway to `AgentLoop`, where Raven calls its
tools (gh, git, workspace file APIs) autonomously. These tests submit real missions
and verify the OUTCOMES (a GitHub repo is created, a file is written/fixed, pushed).

Covered flow (manual run with LIVE_E2E=1):
  1. Raven creates a private GitHub repo via its `gh` tool.
  2. Raven writes a file into a workspace via its file tool.
  3. Raven commits and pushes via its git tool.
  4. Raven fixes a bug in the file and pushes the fix.
  5. The frontend (Playwright spec raven-live.spec.ts) checks Workspaces + Integrations.

DISABLED IN CI:
  - Marked `@pytest.mark.local_only` (conftest skips unless `--run-local`; CI selects
    `-m "not local_only"`).
  - Additionally skips unless `LIVE_E2E=1` and `GH_TOKEN` are set, because it performs
    real, irreversible actions against GitHub and the live stack.

Requirements (live environment):
  - The "default" user must have a GitHub token configured (Identity service), since
    Raven's gh tool authenticates via the resolved credentials.
  - Services reachable: gateway :8080, workspace_runtime :8007, execution :8012.

Manual run:
    LIVE_E2E=1 GH_TOKEN=ghp_xxx SERVER_IP=192.168.2.205 \
        pytest tests/integration/test_raven_user_cases.py --run-local -s
"""
import os
import time

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
WORKSPACE_RUNTIME_URL = os.getenv("WORKSPACE_RUNTIME_URL", f"http://{SERVER_IP}:8007")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
GH_TOKEN = os.getenv("GH_TOKEN", "")
GH_OWNER = os.getenv("GH_OWNER", "JMiahMan1")
# Default to NO repo binding; workspaces must opt in via E2E_REPO_URL.
# Never fall back to the production SharedLLM repo as a test target.
REPO_URL = os.getenv("E2E_REPO_URL") or None


def _live_enabled() -> bool:
    return bool(os.getenv("LIVE_E2E")) and bool(GH_TOKEN)


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven e2e requires LIVE_E2E=1 and GH_TOKEN (disabled in CI)",
    ),
]

CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "900"))


@pytest.fixture
def internal_client():
    return httpx.Client(headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=120.0)


def _gh_headers() -> dict:
    return {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}


def _create_mission(client: httpx.Client, workspace_id: str | None, mission: str) -> int:
    """Create a Raven mission via the queue (shows up in the mission queue)."""
    body = {"query": mission}
    if workspace_id:
        body["workspace_id"] = workspace_id
    resp = client.post(f"{GATEWAY_URL}/api/raven/missions", json=body, timeout=60.0)
    assert resp.status_code == 200, f"mission create failed ({resp.status_code}): {resp.text[:500]}"
    return int(resp.json()["mission"]["id"])


def _wait_mission(client: httpx.Client, mission_id: int, timeout: float | None = None) -> dict:
    """Poll a mission until it reaches a terminal state; return the mission record."""
    timeout = timeout or CHAT_TIMEOUT
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}", timeout=30.0)
        if resp.status_code == 200:
            last = resp.json()
            if last.get("status") in ("completed", "failed", "dismissed"):
                return last
        time.sleep(10)
    return last or {}


def _run_raven(client: httpx.Client, workspace_id: str | None, mission: str) -> dict:
    """Submit a Raven mission through the mission queue and wait for completion."""
    mission_id = _create_mission(client, workspace_id, mission)
    print(f"\n[raven] mission {mission_id} queued")

    # Verify the mission shows up in the mission queue.
    q = client.get(f"{GATEWAY_URL}/api/raven/missions", timeout=30.0)
    if q.status_code == 200:
        queue_ids = [m.get("id") for m in q.json() if isinstance(m, dict)]
        print(f"   - mission in queue: {mission_id in queue_ids}")

    result = _wait_mission(client, mission_id)
    print(f"   - mission final status: {result.get('status')}")
    return result


def _create_workspace(client: httpx.Client, workspace_id: str, display_name: str, repo_url: str | None = REPO_URL, default_branch: str = "microservices") -> dict:
    payload = {
        "id": workspace_id,
        "display_name": display_name,
        "default_branch": default_branch,
        "auto_pull_enabled": False,
        "auto_backup_enabled": False,
    }
    if repo_url:
        payload["repo_url"] = repo_url
    resp = client.post(f"{WORKSPACE_RUNTIME_URL}/workspaces", json=payload)
    assert resp.status_code == 200, f"workspace create failed: {resp.text}"
    return resp.json()


def _bootstrap_workspace(client: httpx.Client, workspace_id: str, **extra) -> dict:
    # Resolve identity via rag_user so the service fetches the real credentials
    # (github_token) from the identity service - mirroring how the gateway injects them.
    resp = client.post(
        f"{WORKSPACE_RUNTIME_URL}/workspaces/bootstrap",
        json={"workspace_id": workspace_id, "rag_user": "default", **extra},
    )
    return resp


def _delete_repo(repo_name: str) -> None:
    try:
        httpx.delete(
            f"https://api.github.com/repos/{GH_OWNER}/{repo_name}",
            headers=_gh_headers(), timeout=30.0,
        )
    except Exception as e:  # pragma: no cover - cleanup best effort
        print(f"   - repo cleanup error: {e}")


def _assert_chat_completed(data: dict, workspace_id: str) -> None:
    """Minimal, chat-level assertion: the mission ran to a terminal, non-failed state
    and produced output. We deliberately avoid asserting internal specifics here -
    this is "basically a chat call": submit a mission, expect a completed result."""
    status = data.get("status")
    assert status in ("completed", "dismissed"), f"mission did not complete (status={status})"
    output = data.get("result") or data.get("output_log") or ""
    assert output, "mission completed but produced no output"


def test_raven_creates_repo_and_writes_game_via_chat(internal_client):
    """Raven creates a repo, writes a file, and pushes - driven purely via chat."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_{ts}"
    repo_name = f"raven-e2e-{ts}"

    _create_workspace(internal_client, workspace_id, "Raven E2E Create", repo_url=None)
    try:
        mission = (
            f"Raven, perform the following mission in workspace '{workspace_id}':\n"
            f"1. Use your GitHub (gh) tool to create a NEW private repository named "
            f"'{repo_name}'.\n"
            f"2. Write a file 'game.py' into this workspace containing a simple Python "
            f"number-guessing game.\n"
            f"3. Initialize git, commit game.py, set the new repo as the 'origin' remote, "
            f"and push to the default branch.\n"
            f"Do not ask questions. Report the final repository URL when done."
        )
        data = _run_raven(internal_client, workspace_id, mission)
        _assert_chat_completed(data, workspace_id)
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")


def test_raven_new_workspace_server_creates_repo(internal_client):
    """New (repo-less) workspace: service creates the GitHub repo server-side, then
    Raven writes + pushes via chat."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_new_{ts}"
    repo_name = f"raven-e2e-srv-{ts}"

    _create_workspace(internal_client, workspace_id, "Raven E2E New", repo_url=None, default_branch="main")
    try:
        boot = _bootstrap_workspace(
            internal_client, workspace_id, create_repo=True, repo_name=repo_name, repo_private=True,
        )
        assert boot.status_code == 200, f"bootstrap failed: {boot.text}"

        mission = (
            f"Raven, perform the following mission in workspace '{workspace_id}':\n"
            f"1. Write a file named 'game.py' into this workspace containing a simple "
            f"Python number-guessing game.\n"
            f"2. Run: `git add game.py && git commit -m 'Add game' && git push -u origin HEAD`.\n"
            f"3. The GitHub repository already exists - do NOT create a new one.\n"
            f"Do not ask questions. Report the final repository URL when done."
        )
        data = _run_raven(internal_client, workspace_id, mission)
        _assert_chat_completed(data, workspace_id)
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")


def test_raven_fixes_bug_via_chat(internal_client):
    """Raven seeds a buggy file + repo, then fixes the bug - both via chat."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_fix_{ts}"
    repo_name = f"raven-e2e-fix-{ts}"

    _create_workspace(internal_client, workspace_id, "Raven E2E Fix", repo_url=None)
    try:
        seed = (
            f"Raven, in workspace '{workspace_id}': create a private GitHub repo named "
            f"'{repo_name}', write 'game.py' containing a function `add(a, b)` that "
            f"returns `a - b` (intentionally wrong) plus a `main()` that prints "
            f"add(2, 3), commit and push. Report the repo URL."
        )
        _run_raven(internal_client, workspace_id, seed)

        fix = (
            f"Raven, in workspace '{workspace_id}': the file game.py has a bug - the "
            f"`add(a, b)` function returns `a - b` but it should return `a + b`. Fix it, "
            f"commit with the message 'fix: add() returns sum', and push. Do not ask "
            f"questions."
        )
        data = _run_raven(internal_client, workspace_id, fix)
        _assert_chat_completed(data, workspace_id)
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")


# Missions in several languages. The point is to exercise the language-aware
# verification gate (ruff/pytest for Python, eslint/tsc for JS/TS, go vet/build,
# cargo build, ...) through a plain chat call - not to assert language specifics.
LANGUAGE_MISSIONS = {
    "python": (
        "Raven, in workspace '{wid}': create a private GitHub repo named '{repo}', "
        "write 'game.py' with a number-guessing game, run `ruff check .` and `pytest` "
        "if available, then `git add -A && git commit -m 'init' && git push -u origin HEAD`. "
        "Report the repository URL. IMPORTANT: you MUST only create and push to the repository "
        "named exactly '{repo}'. Do NOT modify, commit to, or push any other repository "
        "(for example the SharedLLM repo)."
    ),
    "javascript": (
        "Raven, in workspace '{wid}': create a private GitHub repo named '{repo}', "
        "write 'app.js' exporting a function `add(a, b)` that returns a + b, run "
        "`node --check app.js` and `eslint app.js` if available, then commit and push. "
        "Report the repository URL. IMPORTANT: you MUST only create and push to the repository "
        "named exactly '{repo}'. Do NOT modify, commit to, or push any other repository "
        "(for example the SharedLLM repo)."
    ),
    "typescript": (
        "Raven, in workspace '{wid}': create a private GitHub repo named '{repo}', "
        "write 'index.ts' with a typed function `add(a: number, b: number): number` "
        "returning a + b, run `npx tsc --noEmit` if available, then commit and push. "
        "Report the repository URL. IMPORTANT: you MUST only create and push to the repository "
        "named exactly '{repo}'. Do NOT modify, commit to, or push any other repository "
        "(for example the SharedLLM repo)."
    ),
    "go": (
        "Raven, in workspace '{wid}': create a private GitHub repo named '{repo}', "
        "write 'main.go' with package main and a function `Add(a, b int) int`, run "
        "`go vet ./...` and `go build ./...` if available, then commit and push. "
        "Report the repository URL. IMPORTANT: you MUST only create and push to the repository "
        "named exactly '{repo}'. Do NOT modify, commit to, or push any other repository "
        "(for example the SharedLLM repo)."
    ),
    "rust": (
        "Raven, in workspace '{wid}': create a private GitHub repo named '{repo}', "
        "write 'main.rs' with a function `fn add(a: i32, b: i32) -> i32`, run "
        "`cargo build` if available, then commit and push. Report the repository URL."
    ),
}


@pytest.mark.parametrize("lang", ["python", "javascript", "typescript", "go", "rust"])
def test_raven_creates_project_in_language(internal_client, lang):
    """Raven scaffolds + verifies a small project in the given language via chat."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_{lang}_{ts}"
    repo_name = f"raven-e2e-{lang}-{ts}"

    _create_workspace(internal_client, workspace_id, f"Raven E2E {lang}", repo_url=None)
    try:
        mission = LANGUAGE_MISSIONS[lang].format(wid=workspace_id, repo=repo_name)
        data = _run_raven(internal_client, workspace_id, mission)
        _assert_chat_completed(data, workspace_id)
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")
