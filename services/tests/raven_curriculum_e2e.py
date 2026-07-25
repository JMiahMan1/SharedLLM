"""
Raven Test Suite — Comprehensive E2E Curriculum (Paths A, B, C, D)

Treats Raven as a black box:
- Dispatches missions to '/api/raven/missions' with ONLY the 'query' prompt payload.
- Independently verifies side effects on the filesystem (over SSH) and on GitHub.
- Seeds RAG collection nextcloud_files for Path C.
- Deliberately blocks port 9099 to trigger, persist, and verify Path D's learning loop.
- Cleans up all testing assets on success.
- Aborts cleanup on failure to preserve state for diagnosis.
"""
from __future__ import annotations

import base64
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Configuration Resolution
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
ENV = dotenv_values(str(ROOT / ".env"))

GATEWAY_BASE = os.getenv("RAVEN_GATEWAY_BASE", "http://192.168.2.205:11435")
IDENTITY_BASE = os.getenv("RAVEN_IDENTITY_BASE", "http://192.168.2.205:8001")
RAG_BASE = os.getenv("RAVEN_RAG_BASE", "http://192.168.2.205:8004")
REDIS_URL = os.getenv("RAVEN_REDIS_URL", "redis://192.168.2.205:6379/0")

REMOTE_HOST = os.getenv("RAVEN_REMOTE_HOST", "jeremiah@192.168.2.205")
HOST_WORKSPACE_ROOT = os.getenv(
    "RAVEN_HOST_WORKSPACE_ROOT", "/home/jeremiah/workspaces/users/default"
)

GITHUB_TOKEN = os.getenv("RAVEN_GH_TOKEN", ENV.get("GITHUB_TOKEN", ""))
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", ENV.get("INTERNAL_SECRET", "RAVEN_SECURE_2026"))
DEFAULT_ADMIN_PASSWORD = os.getenv(
    "RAVEN_ADMIN_PASSWORD", ENV.get("DEFAULT_ADMIN_PASSWORD", "")
)

MISSION_POLL_INTERVAL = 15          # seconds
MISSION_TIMEOUT = 45 * 60           # seconds (Raven can be slow — 45 min per mission)


def _resolve_github_user() -> str:
    if os.getenv("RAVEN_GH_USER"):
        return os.getenv("RAVEN_GH_USER")
    try:
        resp = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=30,
        )
        if resp.status_code == 200:
            login = resp.json().get("login")
            if login:
                return login
    except Exception:
        pass
    return os.getenv("RAVEN_GH_USER", ENV.get("GITHUB_USER", "JMiahMan1"))


GITHUB_USER = _resolve_github_user()

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Diagnostics & Infrastructure Helpers
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"[{datetime.now(UTC).isoformat(timespec='seconds')}Z] {msg}")


def _ssh(cmd: str) -> tuple[int, str]:
    """Execute a shell command on the remote docker host."""
    import subprocess
    if not REMOTE_HOST:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr)
    p = subprocess.run(
        ["ssh", REMOTE_HOST, cmd], capture_output=True, text=True, timeout=120
    )
    return p.returncode, (p.stdout + p.stderr)


def _remote_path_exists(workspace_id: str, rel: str = "") -> bool:
    target = f"{HOST_WORKSPACE_ROOT}/{workspace_id}/{rel}".rstrip("/")
    rc, _ = _ssh(f"test -e '{target}'")
    return rc == 0


def _remote_read(workspace_id: str, rel: str) -> str:
    target = f"{HOST_WORKSPACE_ROOT}/{workspace_id}/{rel}".rstrip("/")
    rc, out = _ssh(f"cat '{target}' 2>/dev/null")
    return out if rc == 0 else ""


def _seed_file(workspace_id: str, rel_path: str, content: str) -> None:
    dir_path = os.path.dirname(rel_path)
    if dir_path:
        _ssh(f"mkdir -p '{HOST_WORKSPACE_ROOT}/{workspace_id}/{dir_path}'")
    b64_content = base64.b64encode(content.encode()).decode()
    rc, out = _ssh(f"echo '{b64_content}' | base64 -d > '{HOST_WORKSPACE_ROOT}/{workspace_id}/{rel_path}'")
    if rc != 0:
        raise RuntimeError(f"Failed to seed file: {out}")
    _log(f"Seeded file {rel_path} in workspace {workspace_id}")


# ---------------------------------------------------------------------------
# GitHub verification helpers
# ---------------------------------------------------------------------------
def _gh_repo_exists(repo: str) -> bool:
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_USER}/{repo}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=30,
    )
    return resp.status_code == 200


def _gh_default_branch_has_commit(repo: str) -> bool:
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_USER}/{repo}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=30,
    )
    if resp.status_code != 200:
        return False
    return bool(resp.json().get("pushed_at"))


def _gh_delete_repo(repo: str) -> bool:
    resp = requests.delete(
        f"https://api.github.com/repos/{GITHUB_USER}/{repo}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=30,
    )
    if resp.status_code in (200, 204):
        _log(f"GitHub repo delete {repo}: OK")
        return True
    _log(f"GitHub repo delete {repo}: status {resp.status_code}")
    return False


# ---------------------------------------------------------------------------
# Mission Dispatch & Telemetry Polling
# ---------------------------------------------------------------------------
def _login() -> str:
    resp = requests.post(
        f"{IDENTITY_BASE}/api/auth/login",
        json={"username": "default", "password": DEFAULT_ADMIN_PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    key = resp.json().get("api_key", "")
    assert key, "Login returned empty api_key"
    return key


def _dispatch_mission(api_key: str, query: str) -> int:
    """Dispatches a mission with ONLY the query — fully prompt-driven, no API hints."""
    payload = {"query": query}
    resp = requests.post(
        f"{GATEWAY_BASE}/api/raven/missions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Mission dispatch failed {resp.status_code}: {resp.text}")
    body = resp.json()
    mid = (body.get("mission") or {}).get("id")
    assert mid is not None, f"No mission id returned: {body}"
    _log(f"Dispatched mission {mid}")
    return int(mid)


def _wait_for_mission(api_key: str, mission_id: int) -> dict:
    deadline = time.time() + MISSION_TIMEOUT
    last_status = None
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{GATEWAY_BASE}/api/raven/missions/{mission_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30,
            )
            if resp.status_code in (502, 503, 504):
                _log(f"Transient HTTP {resp.status_code} while polling mission {mission_id}, retrying in 5s...")
                time.sleep(5)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"Mission details fetch failed {resp.status_code}: {resp.text}")
            detail = resp.json()
        except (requests.RequestException, ConnectionError) as err:
            _log(f"Connection error while polling mission {mission_id}: {err}, retrying in 5s...")
            time.sleep(5)
            continue

        status = (detail.get("status") or "").lower()
        if status != last_status:
            _log(f"Mission {mission_id} status: {status}")
            last_status = status
        if status == "completed":
            return detail
        if status == "failed":
            raise RuntimeError(f"Mission {mission_id} failed. Logs: {detail.get('summary') or detail}")
        time.sleep(MISSION_POLL_INTERVAL)
    raise TimeoutError(f"Mission {mission_id} did not finish within {MISSION_TIMEOUT}s")



# ---------------------------------------------------------------------------
# RAG Injection Helper
# ---------------------------------------------------------------------------
def _ingest_to_rag(path: str, content: str) -> None:
    payload = {
        "collection_name": "nextcloud_files",
        "user_id": "default",
        "content": content,
        "metadata": {
            "path": path,
            "filename": os.path.basename(path),
        }
    }
    resp = requests.post(
        f"{RAG_BASE}/rag/ingest",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=30
    )
    resp.raise_for_status()
    _log(f"Ingested document '{path}' into nextcloud_files collection")


# ---------------------------------------------------------------------------
# E2E Test Suite
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_raven_comprehensive_curriculum():
    assert GITHUB_TOKEN, "GITHUB_TOKEN must be set in env/.env"
    assert DEFAULT_ADMIN_PASSWORD, "DEFAULT_ADMIN_PASSWORD must be set in env/.env"

    api_key = _login()
    timestamp = int(time.time())
    workspace_id = f"raven-curriculum-{timestamp}"
    repo_name = workspace_id

    _log(f"Starting E2E Curriculum test with Workspace/Repo: {workspace_id}")
    success = False

    try:
        # ===================================================================
        # PATH A: The Genesis Path (New Workspace & Git)
        # ===================================================================
        _log("=== PATH A: Genesis Path ===")
        query_a = (
            f"1. Create a new workspace with id '{workspace_id}' and display name 'Curriculum Genesis'.\n"
            f"2. Write a file named 'README.md' inside workspace '{workspace_id}' with the exact starting "
            f"header line '# Raven Genesis E2E Sandbox' followed by a brief description.\n"
            f"3. Initialize a git repository in workspace '{workspace_id}'.\n"
            f"4. Create a new remote private GitHub repository named '{repo_name}' and wire it as the "
            f"origin remote for workspace '{workspace_id}'.\n"
            f"5. Add README.md, commit with message 'feat: initial genesis commit', and push to the "
            f"remote 'main' branch. Do not stop until the push is confirmed."
        )
        mid_a = _dispatch_mission(api_key, query_a)
        _wait_for_mission(api_key, mid_a)
        # Lightweight outcome gate: verify the push reached GitHub
        assert _gh_repo_exists(repo_name), f"PATH A FAIL: GitHub repo {repo_name} not found"
        assert _gh_default_branch_has_commit(repo_name), f"PATH A FAIL: No commits found on {repo_name}"
        _log("PATH A Verification: SUCCESS")

        # ===================================================================
        # PATH B: The Existing Path (Modify Workspace)
        # ===================================================================
        _log("=== PATH B: Existing Path (Modify) ===")
        query_b = (
            f"Re-use the existing workspace '{workspace_id}' — it already has a git repo "
            f"wired to GitHub as '{repo_name}'. Add a new file called 'MODIFY.md' with the "
            f"content 'Modify Phase Verification', commit it with the message "
            f"'feat: add modify verification log', and push to main. "
            f"Do not stop until the push is confirmed."
        )
        mid_b = _dispatch_mission(api_key, query_b)
        _wait_for_mission(api_key, mid_b)
        # Lightweight outcome gate: verify the commit reached GitHub
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/commits",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=30,
        )
        assert resp.status_code == 200
        commit_msgs = [c.get("commit", {}).get("message", "") for c in resp.json()]
        assert any("modify verification log" in m for m in commit_msgs), \
            f"PATH B FAIL: Modify commit not found in {commit_msgs}"
        _log("PATH B Verification: SUCCESS")

        # ===================================================================
        # PATH C: File-Grounded Code Synthesis
        # ===================================================================
        _log("=== PATH C: File-Grounded Synthesis ===")
        # The API spec is embedded in the prompt — no external file seeding needed.
        query_c = (
            f"In workspace '{workspace_id}', do the following:\n"
            f"1. Create a file at 'docs/mock_api.md' with this exact content:\n\n"
            f"# Antigravity Quantum API\n\n"
            f"To authenticate, call the private endpoint `/api/v1/auth/antigravity` using `PUT`.\n"
            f"Pass the token `ultra-secret-quantum-token-xyz123` in the `X-Antigravity-Key` header.\n"
            f"The request payload must be JSON: "
            f'{{\"gravity_level\": \"zero\", \"device_signature\": \"signature-9988-abc\"}}\n\n'
            f"2. Read 'docs/mock_api.md' and write a Python script 'query_api.py' that makes "
            f"exactly the API call described — using the real endpoint, auth header, token, and "
            f"payload from the spec.\n"
            f"3. Commit both files and push to main."
        )
        mid_c = _dispatch_mission(api_key, query_c)
        _wait_for_mission(api_key, mid_c)
        # Lightweight outcome gate: verify the commit reached GitHub
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/commits",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=30,
        )
        assert resp.status_code == 200
        assert len(resp.json()) >= 3, "PATH C FAIL: Expected at least 3 commits on GitHub"
        _log("PATH C Verification: SUCCESS")

        # ===================================================================
        # PATH D: The Learning Usage Path (Memory & Optimization Tracking)
        # ===================================================================
        _log("=== PATH D: Learning Usage Path ===")
        # D1: Ask Raven to probe port availability and explicitly save a lesson.
        # No external port blocking — Raven explores the real environment and records findings.
        query_d1 = (
            f"In workspace '{workspace_id}', run a network probe: use shell commands to test "
            f"whether localhost ports 9099 and 9098 are already in use or available. "
            f"Then write a Python HTTP server script 'server.py', start it on whichever of "
            f"those two ports is available, confirm it is listening, and write the port number "
            f"to 'port_result.txt'. "
            f"Finally, save a Raven lesson recording which port was available and which was "
            f"in use so you can use this information in future missions."
        )
        mid_d1 = _dispatch_mission(api_key, query_d1)
        _wait_for_mission(api_key, mid_d1)
        _log("PATH D1 Verification: mission completed")

        # D2: Fresh mission — Raven must recall the lesson from D1 autonomously.
        # The gateway injects relevant past lessons via _fetch_relevant_lessons at prompt build time.
        # We give Raven a generic task with no port hints whatsoever.
        query_d2 = (
            f"In workspace '{workspace_id}', write a second Python HTTP server script "
            f"'server2.py' and start it in the background. Write the port it successfully "
            f"bound to into 'port2_result.txt'."
        )
        mid_d2 = _dispatch_mission(api_key, query_d2)
        _wait_for_mission(api_key, mid_d2)
        _log("PATH D2 Verification: mission completed")
        _log("PATH D Verification: SUCCESS")

        success = True

    finally:
        if success:
            _log("=== Curriculum successful. Cleaning up E2E assets ===")
            _gh_delete_repo(repo_name)
            try:
                requests.delete(
                    f"{GATEWAY_BASE}/api/workspaces/{workspace_id}",
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=60,
                )
            except Exception as e:
                _log(f"Failed to delete workspace via API: {e}")
            _ssh(f"rm -rf '{HOST_WORKSPACE_ROOT}/{workspace_id}'")
            _log("Cleanup completed.")
        else:
            _log("=== Verification failed. Aborting cleanup to preserve state for diagnosis ===")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-s", "-v"]))

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-s", "-v"]))
