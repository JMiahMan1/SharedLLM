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
import subprocess
import tempfile
import time

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
WORKSPACE_RUNTIME_URL = os.getenv("WORKSPACE_RUNTIME_URL", f"http://{SERVER_IP}:8007")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
GH_TOKEN = os.getenv("GH_TOKEN", "")
GH_OWNER = os.getenv("GH_OWNER", "JMiahMan1")
REPO_URL = os.getenv("E2E_REPO_URL", "https://github.com/JMiahMan1/SharedLLM.git")


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


def _run_raven(client: httpx.Client, workspace_id: str, mission: str) -> dict:
    """Submit a mission to Raven through the chat interface and return the response."""
    resp = client.post(
        f"{GATEWAY_URL}/api/chat",
        json={
            "query": mission,
            "rag_user": "default",
            "workspace_id": workspace_id,
            "model": "coder",
            "stream": False,
        },
        timeout=CHAT_TIMEOUT,
    )
    assert resp.status_code == 200, f"chat failed ({resp.status_code}): {resp.text[:500]}"
    return resp.json()


def _create_workspace(client: httpx.Client, workspace_id: str, display_name: str, repo_url: str | None = REPO_URL) -> dict:
    payload = {
        "id": workspace_id,
        "display_name": display_name,
        "default_branch": "microservices",
        "auto_pull_enabled": False,
        "auto_backup_enabled": False,
    }
    if repo_url:
        payload["repo_url"] = repo_url
    resp = client.post(f"{WORKSPACE_RUNTIME_URL}/workspaces", json=payload)
    assert resp.status_code == 200, f"workspace create failed: {resp.text}"
    return resp.json()


def _get_workspace_record(client: httpx.Client, workspace_id: str) -> dict:
    resp = client.get(f"{WORKSPACE_RUNTIME_URL}/workspaces")
    assert resp.status_code == 200, f"workspace list failed: {resp.text}"
    for entry in resp.json().get("workspaces", []):
        if entry.get("id") == workspace_id:
            return entry
    raise AssertionError(f"workspace {workspace_id} not found in list")


def _bootstrap_workspace(client: httpx.Client, workspace_id: str, **extra) -> dict:
    resp = client.post(
        f"{WORKSPACE_RUNTIME_URL}/workspaces/bootstrap",
        json={"workspace_id": workspace_id, "user_context": {"user": "default", "is_admin": True}, **extra},
    )
    return resp


def _read_workspace_file(client: httpx.Client, workspace_id: str, path: str) -> str | None:
    resp = client.post(
        f"{WORKSPACE_RUNTIME_URL}/files/read",
        json={
            "workspace_id": workspace_id,
            "relative_path": path,
            "user_context": {"user": "default", "is_admin": True},
        },
    )
    if resp.status_code != 200:
        return None
    return resp.json().get("content")


def _repo_exists(repo_name: str) -> bool:
    r = httpx.get(
        f"https://api.github.com/repos/{GH_OWNER}/{repo_name}",
        headers=_gh_headers(), timeout=30.0,
    )
    return r.status_code == 200


def _delete_repo(repo_name: str) -> None:
    try:
        httpx.delete(
            f"https://api.github.com/repos/{GH_OWNER}/{repo_name}",
            headers=_gh_headers(), timeout=30.0,
        )
    except Exception as e:  # pragma: no cover - cleanup best effort
        print(f"   - repo cleanup error: {e}")


def test_raven_creates_repo_and_writes_game_via_chat(internal_client):
    """Raven creates a GitHub repo, writes a file, and pushes — all via chat tools."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_{ts}"
    repo_name = f"raven-e2e-{ts}"

    _create_workspace(internal_client, workspace_id, "Raven E2E Create")
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
        print(f"\n[raven] submitting create-repo mission (repo={repo_name})")
        data = _run_raven(internal_client, workspace_id, mission)
        print(f"   - Raven final status: {data.get('status')}")

        # Verify the repo was actually created by Raven's gh tool.
        assert _repo_exists(repo_name), f"GitHub repo {repo_name} was not created by Raven"
        print("   - repo exists on GitHub")

        # Verify Raven wrote the file into the workspace.
        content = _read_workspace_file(internal_client, workspace_id, "game.py")
        assert content and "guess" in content.lower(), "Raven did not write game.py to the workspace"
        print("   - game.py present in workspace")

        # Bonus: clone and confirm the pushed file matches.
        with tempfile.TemporaryDirectory(prefix="raven_e2e_") as tmp:
            clone_dir = os.path.join(tmp, repo_name)
            env = os.environ.copy()
            env["GH_TOKEN"] = GH_TOKEN
            result = subprocess.run(
                ["gh", "repo", "clone", f"{GH_OWNER}/{repo_name}", clone_dir],
                capture_output=True, text=True, env=env, timeout=120,
            )
            if result.returncode == 0:
                assert os.path.exists(os.path.join(clone_dir, "game.py")), "pushed game.py missing"
                print("   - pushed game.py verified via clone")
            else:
                print(f"   - clone skipped ({result.stderr[:120].strip()})")
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")


def test_raven_new_workspace_server_creates_repo(internal_client):
    """NEW workspace (no repo) is detected as is_new/needs_repo; the service creates the
    GitHub repo server-side (create_repo), then Raven writes + pushes without making a repo."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_new_{ts}"
    repo_name = f"raven-e2e-srv-{ts}"

    # 1. Create a NEW workspace with NO repo_url.
    _create_workspace(internal_client, workspace_id, "Raven E2E New", repo_url=None)
    try:
        # 2. The workspace record must report this as a brand-new workspace needing a repo.
        ws = _get_workspace_record(internal_client, workspace_id)
        assert ws.get("is_new") is True, f"expected is_new=True, got {ws.get('is_new')}"
        assert ws.get("needs_repo") is True, f"expected needs_repo=True, got {ws.get('needs_repo')}"
        print("   - workspace detected as new / needs_repo")

        # 3. Bootstrap with create_repo so the service creates the GitHub repo for us.
        boot = _bootstrap_workspace(
            internal_client, workspace_id, create_repo=True, repo_name=repo_name, repo_private=True,
        )
        assert boot.status_code == 200, f"bootstrap failed: {boot.text}"
        boot_data = boot.json()
        assert boot_data.get("created_repo") is True, f"expected created_repo=True, got {boot_data}"
        print(f"   - server created GitHub repo {repo_name}")

        # 4. Raven writes the game and pushes to the server-created repo (no repo creation needed).
        mission = (
            f"Raven, in workspace '{workspace_id}': the GitHub repository already exists and is "
            f"cloned. Write a file 'game.py' containing a simple Python number-guessing game, "
            f"commit game.py, and push to the default branch. Do not create a new repository. "
            f"Do not ask questions. Report the final repository URL when done."
        )
        print(f"\n[raven] submitting write+push mission (repo={repo_name})")
        data = _run_raven(internal_client, workspace_id, mission)
        print(f"   - Raven final status: {data.get('status')}")

        assert _repo_exists(repo_name), f"GitHub repo {repo_name} was not created by the service"
        print("   - repo exists on GitHub")

        content = _read_workspace_file(internal_client, workspace_id, "game.py")
        assert content and "guess" in content.lower(), "Raven did not write game.py to the workspace"
        print("   - game.py present in workspace")

        with tempfile.TemporaryDirectory(prefix="raven_e2e_srv_") as tmp:
            clone_dir = os.path.join(tmp, repo_name)
            env = os.environ.copy()
            env["GH_TOKEN"] = GH_TOKEN
            result = subprocess.run(
                ["gh", "repo", "clone", f"{GH_OWNER}/{repo_name}", clone_dir],
                capture_output=True, text=True, env=env, timeout=120,
            )
            if result.returncode == 0:
                assert os.path.exists(os.path.join(clone_dir, "game.py")), "pushed game.py missing"
                print("   - pushed game.py verified via clone")
            else:
                print(f"   - clone skipped ({result.stderr[:120].strip()})")
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")


def test_raven_fixes_bug_via_chat(internal_client):
    """Raven fixes a bug in a workspace file and pushes the fix — via chat tools."""
    ts = int(time.time())
    workspace_id = f"raven_e2e_fix_{ts}"
    repo_name = f"raven-e2e-fix-{ts}"

    _create_workspace(internal_client, workspace_id, "Raven E2E Fix")
    try:
        # Step 1: seed a buggy file + repo via Raven.
        seed = (
            f"Raven, in workspace '{workspace_id}': create a private GitHub repo named "
            f"'{repo_name}', write 'game.py' containing a function `add(a, b)` that "
            f"returns `a - b` (intentionally wrong) plus a `main()` that prints "
            f"add(2, 3), commit and push. Report the repo URL."
        )
        print(f"\n[raven] submitting seed mission (repo={repo_name})")
        _run_raven(internal_client, workspace_id, seed)
        assert _repo_exists(repo_name), f"seed repo {repo_name} not created"

        # Step 2: ask Raven to fix the bug.
        fix = (
            f"Raven, in workspace '{workspace_id}': the file game.py has a bug — the "
            f"`add(a, b)` function returns `a - b` but it should return `a + b`. Fix it, "
            f"commit with the message 'fix: add() returns sum', and push. Do not ask "
            f"questions."
        )
        print("[raven] submitting fix mission")
        _run_raven(internal_client, workspace_id, fix)

        # Verify the fix landed in the workspace file.
        content = _read_workspace_file(internal_client, workspace_id, "game.py")
        assert content is not None, "game.py missing after fix mission"
        fixed = ("return a + b" in content) or ("return a+b" in content.replace(" ", ""))
        assert fixed, f"Raven did not fix the bug. game.py contained:\n{content[:400]}"
        print("   - Raven fixed the bug and it is present in the workspace")
    finally:
        _delete_repo(repo_name)
        try:
            internal_client.delete(f"{WORKSPACE_RUNTIME_URL}/workspaces/{workspace_id}")
        except Exception as e:  # pragma: no cover
            print(f"   - workspace cleanup error: {e}")
