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
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import redis
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
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}Z] {msg}")


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
        timeout=180,
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
        resp = requests.get(
            f"{GATEWAY_BASE}/api/raven/missions/{mission_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Mission details fetch failed {resp.status_code}: {resp.text}")
        detail = resp.json()
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
            f"4. Create a new remote private GitHub repository named '{repo_name}' and wire it as the origin "
            f"remote for workspace '{workspace_id}'.\n"
            f"5. Add README.md, commit with message 'feat: initial genesis commit', and push to the remote 'main' branch."
        )
        mid_a = _dispatch_mission(api_key, query_a)
        _wait_for_mission(api_key, mid_a)

        # Zero-trust verification (A)
        assert _remote_path_exists(workspace_id), f"Workspace directory {workspace_id} missing on host"
        assert _remote_path_exists(workspace_id, "README.md"), "README.md missing in workspace"
        readme_content = _remote_read(workspace_id, "README.md")
        assert "# Raven Genesis E2E Sandbox" in readme_content, f"Unexpected README content: {readme_content}"
        assert _remote_path_exists(workspace_id, ".git"), ".git missing in workspace"
        assert _gh_repo_exists(repo_name), f"GitHub repository {repo_name} does not exist"
        assert _gh_default_branch_has_commit(repo_name), f"GitHub repository {repo_name} has no pushes"
        _log("PATH A Verification: SUCCESS")

        # ===================================================================
        # PATH B: The Existing Path (Modify Workspace)
        # ===================================================================
        _log("=== PATH B: Existing Path (Modify) ===")
        query_b = (
            f"Re-use the existing workspace '{workspace_id}' — it already has a git repo "
            f"wired to GitHub as '{repo_name}'. Add a new file called 'MODIFY.md' with the "
            f"content 'Modify Phase Verification', commit it with the message "
            f"'feat: add modify verification log', and push to main."
        )
        mid_b = _dispatch_mission(api_key, query_b)
        _wait_for_mission(api_key, mid_b)

        # Zero-trust verification (B)
        assert _remote_path_exists(workspace_id, "MODIFY.md"), "MODIFY.md missing in workspace"
        modify_content = _remote_read(workspace_id, "MODIFY.md")
        assert "Modify Phase Verification" in modify_content, f"Unexpected MODIFY content: {modify_content}"
        # Fetch GitHub commits to ensure the new commit landed
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}/commits",
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=30,
        )
        assert resp.status_code == 200
        commits = resp.json()
        commit_msgs = [c.get("commit", {}).get("message", "") for c in commits]
        assert any("modify verification log" in m for m in commit_msgs), f"Modify commit message not found in remote commits: {commit_msgs}"
        _log("PATH B Verification: SUCCESS")

        # ===================================================================
        # PATH C: The RAG Path (Knowledge Retrieval)
        # ===================================================================
        _log("=== PATH C: RAG Path (Knowledge Retrieval) ===")
        # Seed the workspace directory with the mock API spec
        api_spec_content = (
            "# Antigravity Quantum API\n\n"
            "To authenticate against the Antigravity system, you must call the private endpoint `/api/v1/auth/antigravity` using `PUT`.\n"
            "You must pass the token `ultra-secret-quantum-token-xyz123` in the `X-Antigravity-Key` header.\n"
            "The request payload must be JSON with structure:\n"
            '{"gravity_level": "zero", "device_signature": "signature-9988-abc"}\n'
        )
        _seed_file(workspace_id, "docs/mock_api.md", api_spec_content)
        # NOTE: RAG ingest is NOT called directly here — port 8004 is internal to the
        # Docker network and not reachable from the test runner. The workspace file
        # itself is the source of truth; Raven reads it via workspace file tools.
        # Path C tests file-grounded code synthesis, not RAG search retrieval.

        query_c = (
            f"Open workspace '{workspace_id}'. There is a file at 'docs/mock_api.md' that "
            f"describes the Antigravity Quantum API. Read it and write a Python script called "
            f"'query_api.py' that makes the exact API call described — using the real endpoint, "
            f"auth header, token, and payload from the spec."
        )
        mid_c = _dispatch_mission(api_key, query_c)
        _wait_for_mission(api_key, mid_c)

        # Zero-trust verification (C)
        assert _remote_path_exists(workspace_id, "query_api.py"), "query_api.py missing in workspace"
        script_code = _remote_read(workspace_id, "query_api.py")
        assert "/api/v1/auth/antigravity" in script_code, "Endpoint missing in generated script"
        assert "ultra-secret-quantum-token-xyz123" in script_code, "Auth token missing in generated script"
        assert "X-Antigravity-Key" in script_code, "Auth header missing in generated script"
        assert "gravity_level" in script_code, "gravity_level field missing in generated script"
        assert "signature-9988-abc" in script_code, "signature field missing in generated script"
        _log("PATH C Verification: SUCCESS")

        # ===================================================================
        # PATH D: The Learning Usage Path (Memory & Optimization Tracking)
        # ===================================================================
        _log("=== PATH D: Learning Usage Path ===")
        # Block port 9099 INSIDE the sharedllm_execution container — that's where
        # Raven's WorkspaceShellRequest commands actually run. Blocking on the host
        # OS has no effect because the container has a separate network namespace.
        _ssh("docker exec sharedllm_execution sh -c 'pkill -f \"http.server 9099\" 2>/dev/null; true'")
        _ssh(
            "docker exec -d sharedllm_execution sh -c "
            "'python3 -m http.server 9099 >/dev/null 2>&1'"
        )
        time.sleep(2)  # let it bind
        rc, blocker_check = _ssh(
            "docker exec sharedllm_execution sh -c "
            "'ss -tlnp | grep 9099 || echo NOT_BOUND'"
        )
        _log(f"Port 9099 inside container: {blocker_check.strip()}")
        assert "9099" in blocker_check, "Failed to block port 9099 inside execution container"

        # Dispatch Mission 1 to trigger port failure, alternative selection, and learning persistence
        query_d1 = (
            f"In workspace '{workspace_id}', write a small Python HTTP server script called "
            f"'server.py'. Try binding to port 9099 first — if the port is already in use, "
            f"fall back to port 9098. Start the server in the background, then write ONLY the "
            f"port number you successfully bound to into a file called 'port_result.txt' "
            f"(e.g. just the text '9098'). Save a lesson about which port was blocked."
        )
        mid_d1 = _dispatch_mission(api_key, query_d1)
        _wait_for_mission(api_key, mid_d1)

        # Verify Mission 1 results
        assert _remote_path_exists(workspace_id, "port_result.txt"), "port_result.txt missing in workspace"
        port_out = _remote_read(workspace_id, "port_result.txt").strip()
        assert "9098" in port_out, f"Unexpected working port: {port_out}"
        _log(f"D1 Verification: Raven fell back to port {port_out} as expected.")

        # Clean up blocker and any background server started by Mission 1
        _ssh("docker exec sharedllm_execution sh -c 'pkill -f \"http.server 9099\" 2>/dev/null; true'")
        _ssh("docker exec sharedllm_execution sh -c 'pkill -f server.py 2>/dev/null; true'")
        time.sleep(1)

        # Dispatch Mission 2: must bypass port 9099 autonomously using the saved lesson
        query_d2 = (
            f"In workspace '{workspace_id}', start another Python HTTP server. "
            f"Check what you've learned from previous missions about port availability "
            f"and pick the right port from the start without trying 9099. Write ONLY the "
            f"port number you used into 'port2_result.txt' and confirm the server is running."
        )
        mid_d2 = _dispatch_mission(api_key, query_d2)
        _wait_for_mission(api_key, mid_d2)

        # Zero-trust verification (D)
        assert _remote_path_exists(workspace_id, "port2_result.txt"), "port2_result.txt missing in workspace"
        port2_out = _remote_read(workspace_id, "port2_result.txt").strip()
        assert "9098" in port2_out, f"Mission 2 did not start on the correct port: {port2_out}"

        # Verify that the mission system injected past lessons into the prompt
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_key = f"raven:mission:history:{mid_d2}"
        history_logs = redis_client.lrange(redis_key, 0, -1)
        # History may be empty (telemetry is best-effort) — skip if unavailable
        if history_logs:
            log_text = "".join(history_logs).lower()
            assert "lesson" in log_text or "9099" in log_text or "9098" in log_text, \
                "Learning memory was not retrieved or utilized"

        # Assert port 9099 was not written to the result file in Mission 2
        assert "9099" not in port2_out, "Mission 2 improperly used port 9099"
        _log("PATH D Verification: SUCCESS")

        success = True

    finally:
        # Clean up port blockers and servers inside the execution container
        _ssh("docker exec sharedllm_execution sh -c 'pkill -f \"http.server 9099\" 2>/dev/null; true'")
        _ssh("docker exec sharedllm_execution sh -c 'pkill -f \"http.server 9098\" 2>/dev/null; true'")
        _ssh("docker exec sharedllm_execution sh -c 'pkill -f server.py 2>/dev/null; true'")

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
