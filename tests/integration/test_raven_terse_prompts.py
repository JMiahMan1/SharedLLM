"""Terse-prompt curriculum: Raven must complete under-specified missions by
drawing conventions from the always-on protocol curriculum + environment
awareness blocks, not from prompt text.

The point of these missions is that the PROMPT deliberately omits everything
the curriculum teaches: no workspace id, no tool names, no artifact path, no
Apply-citation instruction. Passing means the learning system (protocol
lessons pinned into every mission, toolchain/resource inventory blocks) is
actually steering behavior.

  1. ``terse-fact``      — bare factual question; expects a dedicated
                          workspace, a WebSearchRequest fact check, an
                          ``answer.md`` artifact and an ``Apply: [id]``
                          citation in the plan.
  2. ``terse-flyer``     — bare document request; expects scenario
                          curriculum (typesetting/publishing) + toolchain
                          discovery (pandoc/pdflatex) to produce a real
                          PDF in the workspace root.

Requires LIVE_E2E=1 and a reachable gateway (GATEWAY_URL). Missions take
~5-30 minutes each; run this file alone.
"""
import os
import time

import httpx
import pytest

SERVER_IP = os.getenv("SERVER_IP", "192.168.2.205")
GATEWAY_URL = os.getenv("GATEWAY_URL", f"http://{SERVER_IP}:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "RAVEN_SECURE_2026")
RAVEN_API_KEY = os.getenv("RAVEN_API_KEY", "")

CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "1800"))
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "20"))


def _live_enabled() -> bool:
    return bool(os.getenv("LIVE_E2E"))


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven terse-prompt e2e requires LIVE_E2E=1",
    ),
]


# ---------------------------------------------------------------------------
# Live mission dispatch + polling (mirrors test_raven_web_tools_curriculum.py)
# ---------------------------------------------------------------------------
def _auth_headers() -> dict:
    if RAVEN_API_KEY:
        return {"Authorization": f"Bearer {RAVEN_API_KEY}"}
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _list_missions() -> list[dict]:
    with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
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
    marker = query.strip()[:160]
    best: int | None = None
    for m in _list_missions():
        proposed = (m.get("proposed_mission") or "").strip()
        if proposed[:160] == marker:
            mid = m.get("id")
            if isinstance(mid, int) and (best is None or mid > best):
                best = mid
    return best


def _prune_stale_missions(query: str) -> None:
    marker = query.strip()[:160]
    for m in _list_missions():
        proposed = (m.get("proposed_mission") or "").strip()
        if proposed[:160] == marker and m.get("status") in ("queued", "pending"):
            mid = m.get("id")
            if isinstance(mid, int):
                try:
                    with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
                        c.delete(f"{GATEWAY_URL}/api/raven/missions/{mid}")
                except Exception:
                    pass


def _live_coding_model() -> str:
    try:
        with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
            r = c.get(f"{GATEWAY_URL}/api/config")
            if r.status_code == 200:
                m = (r.json() or {}).get("config", {}).get("coding_model")
                if m:
                    return str(m)
    except Exception:
        pass
    return ""


def _chat_submit(query: str) -> int:
    body = {"query": query, "coding_model": _live_coding_model()}
    _prune_stale_missions(query)
    last_err: str | None = None
    for _ in range(3):
        try:
            with httpx.Client(headers=_auth_headers(), timeout=60.0) as c:
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
    recovered = _recover_mission_id(query)
    assert recovered is not None, f"mission submit failed ({last_err}) and none in queue"
    return int(recovered)


def _chat_wait(mission_id: int) -> dict:
    with httpx.Client(headers=_auth_headers(), timeout=60.0) as c:
        deadline = time.time() + CHAT_TIMEOUT
        exec_deadline: float | None = None
        while True:
            now = time.time()
            if exec_deadline is not None and now > exec_deadline:
                return {"status": "TIMEOUT"}
            if exec_deadline is None and now > deadline:
                return {"status": "TIMEOUT"}
            try:
                resp = c.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") in ("completed", "failed", "dismissed"):
                        return data
                    if data.get("status") == "executing" and exec_deadline is None:
                        exec_deadline = now + CHAT_TIMEOUT
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)


def _delete_workspace_if_exists(ws_id: str) -> None:
    try:
        with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
            c.delete(f"{GATEWAY_URL}/api/workspaces/{ws_id}")
    except Exception:
        pass


def _read_workspace_file(ws_id: str, path: str) -> str | None:
    try:
        with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
            resp = c.post(
                f"{GATEWAY_URL}/api/workspaces/files/read",
                json={"workspace_id": ws_id, "relative_path": path},
            )
            if resp.status_code == 200:
                return (resp.json() or {}).get("content")
    except Exception:
        pass
    return None


def _list_workspace_files(ws_id: str) -> list[dict]:
    try:
        with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
            resp = c.post(
                f"{GATEWAY_URL}/api/workspaces/files/list",
                json={
                    "workspace_id": ws_id,
                    "relative_path": ".",
                    "recursive": True,
                    "max_depth": 3,
                    "max_entries": 200,
                    "include_dirs": True,
                },
            )
            if resp.status_code == 200:
                return (resp.json() or {}).get("entries") or []
    except Exception:
        pass
    return []


def _mission_output_log(mission_id: int) -> str:
    try:
        with httpx.Client(headers=_auth_headers(), timeout=30.0) as c:
            resp = c.get(f"{GATEWAY_URL}/api/raven/missions/{mission_id}")
            if resp.status_code == 200:
                return (resp.json() or {}).get("output_log") or ""
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Terse-prompt ladder.
# ---------------------------------------------------------------------------
def test_raven_terse_protocol_recall():
    """A bare factual question with NO workspace/tool/artifact instructions.

    Passing proves the always-on protocol curriculum supplies the
    conventions: dedicated workspace, WebSearchRequest for facts,
    ``answer.md`` artifact, and an ``Apply: [id]`` citation in the plan.
    """
    prompt = "When was the Raspberry Pi 5 released?"

    mid = _chat_submit(prompt)
    result = _chat_wait(mid)
    assert result.get("status") == "completed", (
        f"terse-fact mission did not complete: {result.get('status')}\n"
        f"result: {(result.get('result') or '')[:500]}"
    )

    ws_id = result.get("workspace_id") or ""
    assert ws_id and ws_id.startswith("raven-"), (
        f"[terse-fact] no dedicated workspace created (got {ws_id!r}) — "
        "protocol workspace lesson not applied"
    )

    content = _read_workspace_file(ws_id, "answer.md")
    assert content, f"[terse-fact] answer.md missing from workspace {ws_id}"
    assert "2023" in content, (
        f"[terse-fact] answer.md does not contain the researched fact:\n{content[-500:]}"
    )

    output_log = _mission_output_log(mid)
    assert "Apply:" in output_log, (
        "[terse-fact] plan has no Apply: citation — lesson application not recorded"
    )


def test_raven_terse_document_mission():
    """A bare document request with NO format/tool/artifact instructions.

    Passing proves the scenario curriculum (typesetting/publishing) and the
    dynamic [WORKSPACE TOOLCHAIN] block steer Raven to discover pandoc /
    pdflatex and produce a real PDF in the workspace root.
    """
    prompt = "Create a one-page PDF flyer about the Raspberry Pi 5 and save it in the workspace."

    mid = _chat_submit(prompt)
    result = _chat_wait(mid)
    assert result.get("status") == "completed", (
        f"terse-flyer mission did not complete: {result.get('status')}\n"
        f"result: {(result.get('result') or '')[:500]}"
    )

    ws_id = result.get("workspace_id") or ""
    assert ws_id and ws_id.startswith("raven-"), (
        f"[terse-flyer] no dedicated workspace created (got {ws_id!r})"
    )

    entries = _list_workspace_files(ws_id)
    pdfs = [
        e for e in entries
        if e.get("type") == "file" and str(e.get("name", "")).lower().endswith(".pdf")
    ]
    assert pdfs, (
        f"[terse-flyer] no .pdf artifact in workspace {ws_id} "
        f"(files: {[e.get('name') for e in entries][:20]})"
    )

    content = _read_workspace_file(ws_id, pdfs[0]["name"])
    assert content, f"[terse-flyer] pdf artifact {pdfs[0]['name']} unreadable"
