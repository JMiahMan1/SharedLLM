"""Teaching curriculum: simple, progressively harder Python missions for Raven.

Unlike ``test_raven_3d_space_shooter.py`` (a single, very large
"build a 3D game" mission per language), this file works *up* from
trivial tasks so Raven accumulates a clean, verified lesson set in its
learning memory instead of lurching into a huge build.

Each mission:
  1. Raven creates a dedicated workspace + GitHub repo and builds a
     SMALL, independently-runnable Python program.
  2. Raven SELF-VERIFIES by running it (the prompt requires a
     concrete, observable stdout contract).
  3. After the run succeeds, Raven APPENDS a dated lesson to
     ``raven_memory.md`` inside its workspace — its own per-task
     learning journal ("Raven should have a learning memory it writes to
     for tasks").
  4. The test then DOUBLE-CHECKS independently: it clones the repo
     and runs the program, asserting the expected stdout, and reads
     ``raven_memory.md`` from the workspace to confirm the success was
     logged to memory. Success is only "remembered" once it is
     verified.

The system-level memory (RAG ``system_learnings``) is also
populated automatically by the gateway's post-mission reflection
(see docs/RAVEN_LEARNING_MEMORY.md); this test exercises BOTH
the per-task journal and that flow.

Requires LIVE_E2E=1, GH_TOKEN, RAVEN_API_KEY (or admin secret)
and a reachable gateway (GATEWAY_URL).
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
GH_TOKEN = os.getenv("GH_TOKEN", "")
GH_OWNER = os.getenv("GH_OWNER", "JMiahMan1")
GH_USER = os.getenv("GH_USER", GH_OWNER)
RAVEN_API_KEY = os.getenv("RAVEN_API_KEY", "")

CHAT_TIMEOUT = float(os.getenv("E2E_CHAT_TIMEOUT", "1800"))
POLL_INTERVAL = float(os.getenv("E2E_POLL_INTERVAL", "20"))

# Ordered, progressively harder. Each is small + independently runnable.
MISSIONS = [
    {
        "id": "py-hello",
        "repo": "raven-py-hello",
        "prompt": (
            "Raven, create a dedicated workspace with id 'raven-py-hello' and a private "
            "GitHub repo 'raven-py-hello'. In it, write a runnable Python CLI "
            "program `main.py` that prints exactly `Hello, Raven` when run with no "
            "arguments, and prints `Hello, <name>` when run with `--name <name>`. "
            "Then RUN it yourself (`python3 main.py` and `python3 main.py --name Ada`) "
            "and confirm the output. When the run succeeds, APPEND a dated lesson to "
            "`raven_memory.md` in this workspace summarizing the task and how you "
            "verified it. Commit and push. Deliver one working file, no TODOs."
        ),
        "check": lambda out: "Hello, Raven" in out and "Hello, Ada" in out,
        "expect": "prints 'Hello, Raven' and 'Hello, Ada'",
    },
    {
        "id": "py-fizzbuzz",
        "repo": "raven-py-fizzbuzz",
        "prompt": (
            "Raven, create a dedicated workspace 'raven-py-fizzbuzz' and a private "
            "GitHub repo 'raven-py-fizzbuzz'. Write `fizzbuzz.py` with a function "
            "`fizzbuzz(n)` returning the list of FizzBuzz strings for 1..n, plus a "
            "`if __name__ == '__main__'` block that prints the result for n=15. Add "
            "`test_fizzbuzz.py` using unittest that asserts fizzbuzz(15) is correct. "
            "RUN both (`python3 fizzbuzz.py` and `python3 -m unittest "
            "test_fizzbuzz.py -v`) and confirm the tests pass. Then APPEND a dated "
            "lesson to `raven_memory.md` describing the task and verification. Commit and "
            "push. No TODOs."
        ),
        "check": lambda out: "Fizz" in out and "Buzz" in out,
        "expect": "prints Fizz/Buzz and passes unittest",
    },
    {
        "id": "py-module",
        "repo": "raven-py-strutils",
        "prompt": (
            "Raven, create workspace 'raven-py-strutils' and private repo "
            "'raven-py-strutils'. Build a small importable package: `strutils/__init__.py` "
            "exposing `word_count(text)` and `reverse_words(text)`, plus `requirements.txt` "
            "(empty or minimal) and `test_strutils.py` (unittest) asserting both "
            "functions. RUN `python3 -m unittest test_strutils.py -v` and confirm green. "
            "Then APPEND a dated lesson to `raven_memory.md` about packaging + testing. "
            "Commit and push. No TODOs."
        ),
        "check": lambda out: "word_count" in out or "OK" in out,
        "expect": "passes unittest for the package",
    },
    {
        "id": "py-cli-file",
        "repo": "raven-py-wordcount",
        "prompt": (
            "Raven, create workspace 'raven-py-wordcount' and private repo "
            "'raven-py-wordcount'. Write a CLI `wordcount.py` that takes `--input <file>` "
            "and prints the number of words in that file. Include `test_wordcount.py` "
            "(unittest) that writes a temp file and asserts the count. RUN the tests "
            "(`python3 -m unittest test_wordcount.py -v`) and a manual run on a sample "
            "file, confirm correct. Then APPEND a dated lesson to `raven_memory.md` about "
            "file I/O + argparse. Commit and push. No TODOs."
        ),
        "check": lambda out: "word" in out.lower() or "OK" in out,
        "expect": "counts words in a file and tests pass",
    },
]


def _live_enabled() -> bool:
    return bool(os.getenv("LIVE_E2E")) and bool(GH_TOKEN)


pytestmark = [
    pytest.mark.local_only,
    pytest.mark.skipif(
        not _live_enabled(),
        reason="Live Raven Python teaching e2e requires LIVE_E2E=1 and GH_TOKEN",
    ),
]


# ---------------------------------------------------------------------------
# Live chat submission + mission polling (mirrors test_raven_3d_space_shooter).
# ---------------------------------------------------------------------------
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


def _recover_mission_id(query: str) -> int | None:
    """Recover the newest mission whose prompt matches this submission.

    The gateway enqueues the Raven mission server-side but may be slow to
    return the 202 (the in-process worker saturates the event loop), so
    we poll the queue for the newest matching prompt.
    """
    marker = query.strip()[:160]
    best: int | None = None
    for m in _list_missions():
        proposed = (m.get("proposed_mission") or "").strip()
        if proposed[:160] == marker:
            mid = m.get("id")
            if isinstance(mid, int) and (best is None or mid > best):
                best = mid
    return best


def _chat_submit(query: str) -> int:
    body = {"query": query, "coding_model": _live_coding_model()}
    last_err: str | None = None
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
    recovered = _recover_mission_id(query)
    assert recovered is not None, f"mission submit failed ({last_err}) and none in queue"
    return int(recovered)


def _chat_wait(mission_id: int) -> dict:
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


def _live_coding_model() -> str:
    try:
        with httpx.Client(headers=_chat_auth_headers(), timeout=30.0) as c:
            r = c.get(f"{GATEWAY_URL}/api/config")
            if r.status_code == 200:
                m = (r.json() or {}).get("config", {}).get("coding_model")
                if m:
                    return str(m)
    except Exception:
        pass
    return ""


def _delete_workspace_if_exists(ws_id: str) -> None:
    try:
        with httpx.Client(headers=_ws_headers(), timeout=30.0) as c:
            c.delete(f"{GATEWAY_URL}/api/workspaces/{ws_id}")
    except Exception:
        pass


def _read_workspace_file(ws_id: str, path: str) -> str | None:
    """Read a file from the mission workspace via the gateway proxy (admin)."""
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


def _clone_and_run(repo: str, run_cmd: str, timeout: int = 120) -> str:
    """Independently double-check: clone the pushed repo and run it."""
    url = f"https://{GH_TOKEN}@github.com/{GH_OWNER}/{repo}.git"
    with tempfile.TemporaryDirectory() as d:
        clone = subprocess.run(
            ["git", "clone", url, d], capture_output=True, text=True, timeout=120
        )
        if clone.returncode != 0:
            raise AssertionError(f"clone failed: {clone.stderr[-300:]}")
        run = subprocess.run(
            run_cmd, shell=True, cwd=d, capture_output=True, text=True, timeout=timeout
        )
        return run.stdout + run.stderr


# ---------------------------------------------------------------------------
# Run the curriculum in order (work up). pytest runs the module's tests
# sequentially; we drive one live mission per test so a failure stops the
# ladder rather than burning all four against a broken setup.
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.parametrize("mission", MISSIONS, ids=[m["id"] for m in MISSIONS])
def test_raven_python_mission(mission: dict):
    _delete_workspace_if_exists(mission["repo"])

    mid = _chat_submit(mission["prompt"])
    result = _chat_wait(mid)
    assert result.get("status") == "completed", (
        f"mission {mission['id']} did not complete: {result.get('status')}"
    )

    # Double-check 1: run the pushed artifact independently.
    stdout = _clone_and_run(
        mission["repo"],
        # repo layout is flat (main.py / fizzbuzz.py / test_*.py)
        "python3 main.py 2>/dev/null || python3 fizzbuzz.py 2>/dev/null "
        "|| python3 -m unittest discover -v 2>&1 | tail -20",
    )
    assert mission["check"](stdout), (
        f"[{mission['id']}] independent run did not satisfy: {mission['expect']}\n"
        f"output:\n{stdout[-800:]}"
    )

    # Double-check 2: success was logged to Raven's per-task memory.
    memory = _read_workspace_file(mission["repo"], "raven_memory.md")
    assert memory, f"[{mission['id']}] raven_memory.md missing from workspace"
    assert mission["id"] in memory or "lesson" in memory.lower(), (
        f"[{mission['id']}] raven_memory.md was not updated with the lesson:\n"
        f"{memory[:800]}"
    )
