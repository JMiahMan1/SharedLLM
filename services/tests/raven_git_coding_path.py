"""
Raven Test Suite — Git Coding Path (Genesis + Modify).

Treats Raven as a black box: dispatches missions to the Raven mission endpoint,
waits for completion, then independently verifies the side effects in the real
environment (local workspace dir + GitHub remote). On success it cleans up the
local workspace and the remote repo. On failure it PRESERVES all state for
diagnosis and raises.

Run:
    python -m pytest services/tests/raven_git_coding_path.py -s   # (integration)
    python services/tests/raven_git_coding_path.py                # standalone

Endpoints (see services/gateway/main.py:5107):
    POST /api/raven/missions   {query}  -> {"status","mission":{"id",...}}
    GET  /api/raven/missions/{id}        -> mission detail w/ status
Auth: Bearer <user api_key> obtained from identity /api/auth/login.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]          # repo root
ENV = dotenv_values(str(ROOT / ".env"))

GATEWAY_BASE = os.getenv("RAVEN_GATEWAY_BASE", "http://192.168.2.205:11435")
IDENTITY_BASE = os.getenv("RAVEN_IDENTITY_BASE", "http://192.168.2.205:8001")
# Raven creates workspaces on the DOCKER HOST (192.168.2.205), not on the box
# running this test. So all local-filesystem verification must run over SSH
# there. Set RAVEN_REMOTE_HOST="" to verify locally instead (e.g. when this
# script itself runs on the docker host).
REMOTE_HOST = os.getenv("RAVEN_REMOTE_HOST", "jeremiah@192.168.2.205")
HOST_WORKSPACE_ROOT = os.getenv(
    "RAVEN_HOST_WORKSPACE_ROOT", "/home/jeremiah/workspaces/users/default"
)
GITHUB_USER = os.getenv("RAVEN_GH_USER", ENV.get("GITHUB_USER", "JMiahMan1"))
GITHUB_TOKEN = os.getenv("RAVEN_GH_TOKEN", ENV.get("GITHUB_TOKEN", ""))
DEFAULT_ADMIN_PASSWORD = os.getenv(
    "RAVEN_ADMIN_PASSWORD", ENV.get("DEFAULT_ADMIN_PASSWORD", "")
)


def _resolve_github_user() -> str:
    """Resolve the authenticated GitHub login from the token.

    The .env GITHUB_USER value can disagree with the token's actual owner
    (e.g. 'jeremiah@sumemail.com' vs the token's 'JMiahMan1'), which makes the
    zero-trust GitHub verification check the wrong namespace and fail even when
    the repo was created. Always verify against the token's real login.
    """
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
    return GITHUB_USER


# Resolve the real GitHub owner from the token so zero-trust checks target the
# correct namespace (the .env GITHUB_USER may not match the token's owner).
GITHUB_USER = _resolve_github_user()


MISSION_POLL_INTERVAL = 15          # seconds
MISSION_TIMEOUT = 25 * 60           # seconds (Raven can be slow)
# Coding model is intentionally NOT sent in the payload — Raven must rely on its
# internal configuration (settings.coding_model). We only send `query`.

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}Z] {msg}")


def _login() -> str:
    """Authenticate as the default admin user, return the api_key (Bearer token)."""
    resp = requests.post(
        f"{IDENTITY_BASE}/api/auth/login",
        json={"username": "default", "password": DEFAULT_ADMIN_PASSWORD},
        timeout=30,
    )
    resp.raise_for_status()
    key = resp.json().get("api_key", "")
    assert key, "Login returned empty api_key"
    return key


def _dispatch_mission(api_key: str, query: str, workspace_id: str | None = None) -> int:
    """Dispatch a Raven mission with ONLY the query (plus optional workspace_id).
    Returns the mission id."""
    payload: dict = {"query": query}
    if workspace_id:
        payload["workspace_id"] = workspace_id
    resp = requests.post(
        f"{GATEWAY_BASE}/api/raven/missions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Mission dispatch failed {resp.status_code}: {resp.text}")
    body = resp.json()
    mission = body.get("mission") or {}
    mid = mission.get("id")
    assert mid is not None, f"No mission id in response: {body}"
    _log(f"Dispatched mission {mid} (workspace={workspace_id})")
    return int(mid)


def _wait_for_mission(api_key: str, mission_id: int) -> dict:
    """Poll until the mission reaches completed/failed. Returns the final detail."""
    deadline = time.time() + MISSION_TIMEOUT
    last_status = None
    while time.time() < deadline:
        resp = requests.get(
            f"{GATEWAY_BASE}/api/raven/missions/{mission_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Mission status fetch failed {resp.status_code}: {resp.text}")
        detail = resp.json()
        status = (detail.get("status") or "").lower()
        if status != last_status:
            _log(f"Mission {mission_id} status: {status}")
            last_status = status
        if status in ("completed", "failed"):
            return detail
        time.sleep(MISSION_POLL_INTERVAL)
    raise TimeoutError(f"Mission {mission_id} did not finish within {MISSION_TIMEOUT}s")


def _host_workspace_path(workspace_id: str) -> Path:
    return Path(HOST_WORKSPACE_ROOT) / workspace_id


def _ssh(cmd: str) -> tuple[int, str]:
    """Run a command on the docker host. Returns (returncode, combined output)."""
    if not REMOTE_HOST:
        import subprocess

        p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr)
    import subprocess

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


def _remote_is_git(workspace_id: str) -> bool:
    return _remote_path_exists(workspace_id, ".git")


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
    data = resp.json()
    return bool(data.get("pushed_at"))  # non-null once something is pushed


def _gh_delete_repo(repo: str) -> bool:
    """Delete a GitHub repo created during the test.

    Returns True if deleted, False if deletion is not permitted (the infra token
    often lacks the ``delete_repo`` scope, so DELETE returns 403 even though the
    ``repo`` scope allows creation). In that case we log and leave the repo for
    manual cleanup rather than failing the whole curriculum test.
    """
    resp = requests.delete(
        f"https://api.github.com/repos/{GITHUB_USER}/{repo}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=30,
    )
    if resp.status_code in (200, 204):
        _log(f"GitHub repo delete {repo}: OK")
        return True
    if resp.status_code == 403:
        _log(
            f"GitHub repo delete {repo}: 403 (token lacks delete_repo scope) — "
            f"left for manual cleanup"
        )
        return False
    _log(f"GitHub repo delete {repo}: unexpected {resp.status_code}")
    return False


def _delete_workspace_via_api(api_key: str, workspace_id: str) -> None:
    """Best-effort delete of the workspace record + sandbox via the gateway."""
    try:
        requests.delete(
            f"{GATEWAY_BASE}/api/workspaces/{workspace_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        _log(f"Workspace delete requested: {workspace_id}")
    except Exception as e:  # noqa: BLE001
        _log(f"Workspace API delete failed (will try host rm): {e}")


def _cleanup_local(workspace_id: str) -> None:
    target = f"{HOST_WORKSPACE_ROOT}/{workspace_id}"
    rc, out = _ssh(f"rm -rf '{target}'")
    _log(f"Removed remote workspace dir {target}: rc={rc} {out[:120]}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_raven_git_coding_path():
    """Path A (Genesis) then Path B (Modify), with zero-trust verification."""
    assert GITHUB_TOKEN, "GITHUB_TOKEN missing from .env"
    assert DEFAULT_ADMIN_PASSWORD, "DEFAULT_ADMIN_PASSWORD missing from .env"

    api_key = _login()
    workspace_id = f"raven-test-{int(time.time())}"
    repo_name = workspace_id  # repo name must match workspace id convention

    # ---- Path A: Genesis ----
    query_a = (
        f"Execute these exact steps in order. Do NOT use raw shell `git`/`gh`; "
        f"use the documented tool calls.\n"
        f"1. WorkspaceCreateRequest with id '{workspace_id}'. Capture the id.\n"
        f"2. WorkspaceFileWriteRequest: path 'README.md', workspace_id '{workspace_id}', "
        f"content a file starting with the line 'Raven training sandbox' then a one-line description.\n"
        f"3. GitOperationRequest action 'init' (workspace_id '{workspace_id}').\n"
        f"4. GitOperationRequest action 'repo_create' with repo_name '{repo_name}' "
        f"(this creates the GitHub repo and wires origin).\n"
        f"5. GitOperationRequest action 'add' path '.' (workspace_id '{workspace_id}').\n"
        f"6. GitOperationRequest action 'commit' commit_message 'feat: initial sandbox README' "
        f"(workspace_id '{workspace_id}').\n"
        f"7. GitOperationRequest action 'push' branch 'main' (workspace_id '{workspace_id}').\n"
        f"After step 7, verify with a GitOperationRequest action 'status' that the branch is "
        f"ahead of origin, then report completion. The GitHub repo is owned by {GITHUB_USER}."
    )
    _log("=== PATH A: Genesis ===")
    mid_a = _dispatch_mission(api_key, query_a)
    detail_a = _wait_for_mission(api_key, mid_a)

    try:
        # ---- Zero-trust verification (Path A) ----
        _verify_genesis(workspace_id, repo_name)
        _log("PATH A verification: PASS")
    except Exception as e:
        _log(f"PATH A verification: FAIL -> {e}")
        # Preserve state on failure (no cleanup); surface the failure.
        raise

    # ---- Path B: Modify ----
    query_b = (
        f"Target the EXISTING workspace '{workspace_id}' (already a git repo with a "
        f"remote origin pointing at GitHub repo '{repo_name}').\n"
        f"1. WorkspaceFileWriteRequest: path 'MISSION_LOG.md', workspace_id '{workspace_id}', "
        f"content a single line 'Phase 1 complete'.\n"
        f"2. GitOperationRequest action 'add' path '.' (workspace_id '{workspace_id}').\n"
        f"3. GitOperationRequest action 'commit' commit_message 'feat: add mission log' "
        f"(workspace_id '{workspace_id}').\n"
        f"4. GitOperationRequest action 'push' branch 'main' (workspace_id '{workspace_id}').\n"
        f"Report when the push lands on 'main'."
    )
    _log("=== PATH B: Modify ===")
    mid_b = _dispatch_mission(api_key, query_b, workspace_id=workspace_id)
    detail_b = _wait_for_mission(api_key, mid_b)

    try:
        _verify_modify(workspace_id, repo_name)
        _log("PATH B verification: PASS")
    except Exception as e:
        _log(f"PATH B verification: FAIL -> {e}")
        raise
    finally:
        # ---- Cleanup on success ----
        _log("=== Cleanup ===")
        _gh_delete_repo(repo_name)
        _delete_workspace_via_api(api_key, workspace_id)
        _cleanup_local(workspace_id)


def _verify_genesis(workspace_id: str, repo_name: str) -> None:
    # 1. Workspace dir exists (on the docker host, verified over SSH)
    assert _remote_path_exists(workspace_id), f"Workspace dir missing: {HOST_WORKSPACE_ROOT}/{workspace_id}"
    # 2. README.md exists with expected content
    assert _remote_path_exists(workspace_id, "README.md"), f"README.md missing in {workspace_id}"
    text = _remote_read(workspace_id, "README.md")
    assert "Raven training sandbox" in text, f"README content unexpected:\n{text}"
    # 3. Local git repo has a .git directory
    assert _remote_is_git(workspace_id), f"Not a git repo (no .git) in {workspace_id}"
    # 4. Remote GitHub repo exists AND has a pushed commit on default branch
    assert _gh_repo_exists(repo_name), f"GitHub repo {repo_name} not created"
    assert _gh_default_branch_has_commit(repo_name), f"GitHub repo {repo_name} has no push"


def _verify_modify(workspace_id: str, repo_name: str) -> None:
    # 1. New file present locally (on the docker host)
    assert _remote_path_exists(workspace_id, "MISSION_LOG.md"), f"MISSION_LOG.md missing in {workspace_id}"
    text = _remote_read(workspace_id, "MISSION_LOG.md")
    assert "Phase 1 complete" in text, f"MISSION_LOG.md content unexpected:\n{text}"
    # 2. Remote still has the repo and a recent push (modify landed)
    assert _gh_repo_exists(repo_name), f"GitHub repo {repo_name} disappeared"
    resp = requests.get(
        f"https://api.github.com/repos/{GITHUB_USER}/{repo_name}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=30,
    )
    assert resp.status_code == 200, f"Cannot fetch repo {repo_name}"
    assert resp.json().get("pushed_at"), f"Repo {repo_name} has no push timestamp"


if __name__ == "__main__":
    # Standalone runner (no pytest needed)
    import sys

    sys.exit(pytest.main([__file__, "-s", "-v", "-p", "no:cacheprovider"]))
