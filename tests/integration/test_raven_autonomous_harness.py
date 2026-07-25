"""Autonomous Raven Test Harness & Workspace IDE Auditor.

Executes sequential test paths:
Path A: Genesis & Git Setup
Path B: Incremental Edit
Path C: RAG Document Retrieval
Path D: Learning & Memory Optimization
Path E: Final Host-Accessible Workspace Test
"""
import os
import subprocess
import tempfile
import time

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "RAVEN_SECURE_2026")
RAVEN_API_KEY = os.getenv("RAVEN_API_KEY", "")
CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "1800"))
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "15"))


def _get_gh_token() -> str:
    return os.getenv("GH_TOKEN", "")


def _live_enabled() -> bool:
    return bool(os.getenv("LIVE_E2E")) and bool(_get_gh_token())


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven Autonomous Harness requires LIVE_E2E=1 and GH_TOKEN",
    ),
]


def _chat_auth_headers() -> dict:
    if RAVEN_API_KEY:
        return {"Authorization": f"Bearer {RAVEN_API_KEY}"}
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _ws_headers() -> dict:
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


def _get_max_mission_id() -> int:
    missions = _list_missions()
    ids = [m.get("id") for m in missions if isinstance(m.get("id"), int)]
    return max(ids) if ids else 0


def _recover_mission_id(prompt: str, min_id: int = 0) -> int | None:
    marker = prompt.strip()[:160]
    best: int | None = None
    for m in _list_missions():
        mid = m.get("id")
        if isinstance(mid, int) and mid > min_id:
            proposed = (m.get("proposed_mission") or "").strip()
            if proposed[:160] == marker and (best is None or mid > best):
                best = mid
    return best


def _purge_all_missions() -> None:
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
            missions = _list_missions()
            for m in missions:
                mid = m.get("id")
                if mid:
                    c.delete(f"{GATEWAY_URL}/api/raven/missions/{mid}")
    except Exception:
        pass


def _submit_prompt(prompt: str) -> int:
    """Rule 2: Send ONLY prompt string in payload."""
    min_id = _get_max_mission_id()
    body = {"query": prompt}
    last_err = None
    for _ in range(3):
        try:
            with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
                resp = c.post(f"{GATEWAY_URL}/api/raven/missions", json=body)
                if resp.status_code in (200, 201, 202):
                    data = resp.json()
                    mission = data.get("mission") or {}
                    mid = mission.get("id") or data.get("mission_id")
                    if mid is not None:
                        return int(mid)
            last_err = f"status {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(3)
    recovered = _recover_mission_id(prompt, min_id=min_id)
    assert recovered is not None, f"Mission submit failed ({last_err}) and none in queue"
    return int(recovered)


def _wait_for_mission(mission_id: int) -> dict:
    with httpx.Client(headers=_chat_auth_headers(), timeout=60.0) as c:
        deadline = time.time() + CHAT_TIMEOUT
        while time.time() < deadline:
            try:
                resp = c.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") in ("completed", "failed", "dismissed"):
                        return data
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
    return {"status": "TIMEOUT"}


def _delete_workspace(ws_id: str) -> None:
    try:
        with httpx.Client(headers=_ws_headers(), timeout=30.0) as c:
            c.delete(f"{GATEWAY_URL}/api/workspaces/{ws_id}")
    except Exception:
        pass


def _read_workspace_file(ws_id: str, path: str) -> str | None:
    try:
        with httpx.Client(headers=_ws_headers(), timeout=30.0) as c:
            resp = c.post(
                f"{GATEWAY_URL}/api/workspaces/files/read",
                json={"workspace_id": ws_id, "relative_path": path},
            )
            if resp.status_code == 200:
                return (resp.json() or {}).get("content")
    except Exception:
        pass
    return None


def _clone_and_check_file(repo: str, filename: str) -> str:
    gh_token = _get_gh_token()
    gh_owner = os.getenv("GH_OWNER", "JMiahMan1")
    url = f"https://{gh_token}@github.com/{gh_owner}/{repo}.git"
    with tempfile.TemporaryDirectory() as d:
        clone = subprocess.run(
            ["git", "clone", url, d], capture_output=True, text=True, timeout=120
        )
        assert clone.returncode == 0, f"Git clone failed: {clone.stderr}"
        target = os.path.join(d, filename)
        assert os.path.isfile(target), f"File {filename} missing from repo {repo}"
        with open(target) as f:
            return f.read()


# ---------------------------------------------------------------------------
# Core Sequential Test Paths
# ---------------------------------------------------------------------------

def test_raven_path_a_genesis():
    ws_id = "raven-auto-genesis"
    repo_name = "raven-auto-genesis"
    _purge_all_missions()
    _delete_workspace(ws_id)

    prompt = (
        f"Create a new workspace named '{ws_id}', initialize a Git repository, "
        f"update workspace configuration to point to private GitHub repo '{repo_name}', "
        f"write a `README.md` with title '# Raven Genesis', commit, and push."
    )

    mid = _submit_prompt(prompt)
    res = _wait_for_mission(mid)
    assert res.get("status") == "completed", f"Path A failed: {res}"

    # Verify remote git repo and README content
    content = _clone_and_check_file(repo_name, "README.md")
    assert "Raven Genesis" in content


def test_raven_path_b_incremental_edit():
    ws_id = "raven-auto-genesis"
    repo_name = "raven-auto-genesis"

    prompt = (
        f"Target existing workspace '{ws_id}'. Edit 'README.md' to add a section "
        f"'## Incremental Verification', commit, and push."
    )

    mid = _submit_prompt(prompt)
    res = _wait_for_mission(mid)
    assert res.get("status") == "completed", f"Path B failed: {res}"

    content = _clone_and_check_file(repo_name, "README.md")
    assert "Incremental Verification" in content


def test_raven_path_c_rag_retrieval():
    ws_id = "raven-auto-rag"
    _delete_workspace(ws_id)

    prompt = (
        f"In workspace '{ws_id}', build a Python module `config_spec.py` that "
        f"implements `get_system_secret_key()` returning 'RAVEN_SECURE_2026' "
        f"and `get_dns_port()` returning 15353. Execute `python3 config_spec.py` "
        f"to verify."
    )

    mid = _submit_prompt(prompt)
    res = _wait_for_mission(mid)
    assert res.get("status") == "completed", f"Path C failed: {res}"

    content = _read_workspace_file(ws_id, "config_spec.py")
    assert content is not None
    assert "RAVEN_SECURE_2026" in content
    assert "15353" in content


def test_raven_path_d_learning_optimization():
    ws_id = "raven-auto-learning"
    _delete_workspace(ws_id)

    # Mission 1: Trigger constraint trap (busy port attempt or recovery)
    prompt1 = (
        f"In workspace '{ws_id}', write `server.py` and run it on port 8080. "
        f"If port 8080 is occupied or fails, handle the exception gracefully, "
        f"bind to port 9876 instead, and write 'PORT=9876' into `port.txt`."
    )

    mid1 = _submit_prompt(prompt1)
    res1 = _wait_for_mission(mid1)
    assert res1.get("status") == "completed", f"Path D mission 1 failed: {res1}"

    # Retest mission 2: Ensure Raven applies learned lesson
    prompt2 = (
        f"In workspace '{ws_id}', write `server2.py`. Use the learned port strategy "
        f"to write 'PORT=9876' into `port2.txt`."
    )

    mid2 = _submit_prompt(prompt2)
    res2 = _wait_for_mission(mid2)
    assert res2.get("status") == "completed", f"Path D mission 2 failed: {res2}"

    content = _read_workspace_file(ws_id, "port2.txt")
    assert content is not None
    assert "9876" in content


def test_raven_path_e_host_accessible_workspace():
    ws_id = "raven-auto-hostport"
    _delete_workspace(ws_id)

    prompt = (
        f"In workspace '{ws_id}', spin up an HTTP web service in `app.py` running on "
        f"port 8899 that returns JSON `{{\"status\": \"ok\", \"agent\": \"Raven\"}}`. "
        f"Start it in the background. Expose container port 8899 to host port 9899."
    )

    mid = _submit_prompt(prompt)
    res = _wait_for_mission(mid)
    assert res.get("status") == "completed", f"Path E failed: {res}"

    # Verify direct HTTP request to Host IP:Host Port from test runner
    url = f"http://{SERVER_IP}:9899"
    time.sleep(2)
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(url)
        assert resp.status_code == 200, f"Host port endpoint returned {resp.status_code}"
        data = resp.json()
        assert data.get("status") == "ok"
        assert data.get("agent") == "Raven"
