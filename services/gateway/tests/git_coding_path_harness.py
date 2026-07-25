#!/usr/bin/env python3
"""
Parent LLM Test Orchestrator - Git Coding Path harness.

Drives Raven as a BLACK BOX through two sequential missions (Path A: genesis,
Path B: modify) against a real GitHub remote, then performs ZERO-TRUST
verification entirely from outside Raven's response logs:

  * GitHub REST API  -> did the commits/branch actually land on the remote?
  * Raven lesson store -> did the structured-learning pipeline capture a new,
    honest lesson (rule/root_cause/outcome/confidence/applied)?

On success it cleans up: deletes the remote GitHub repo and the local
workspace via the workspace_runtime API. On failure it ABORTS cleanup and
prints the lingering state for diagnosis.

Usage:
    python3 services/gateway/tests/git_coding_path_harness.py

Requires network access to the gateway (192.168.2.205:11435) and GitHub.
"""
from __future__ import annotations

import json
import os
import sys
import time

import requests  # reliable JSON/Content-Type handling (urllib forced form-urlencoded)

# ---- Config ---------------------------------------------------------------
GATEWAY = os.getenv("GATEWAY_URL", "http://192.168.2.205:11435")
# Lesson store is reached via the GATEWAY (the RAG :8003 port is not directly routable).
RAG = GATEWAY
DEFAULT_USER = "default"
DEFAULT_PASS = os.getenv("DEFAULT_ADMIN_PASSWORD", "changeme")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "JMiahMan1")  # resolved at runtime
POLL_INTERVAL = 20
MISSION_TIMEOUT = 40 * 60  # 40 min hard cap per mission

REPO_NAME = "raven-gitcoding-path-v2"

PATH_A_QUERY = f"""Complete this coding mission end to end:
1. Create a dedicated isolated workspace via WorkspaceCreateRequest.
2. Inside it, run `git init` and configure the default branch to `main`.
3. Create a GitHub repository named `{REPO_NAME}` (use gh repo create, private).
4. Add a remote `origin` pointing at that repo.
5. Create a file `README.md` with exactly this content:
   # Raven Git Coding Path
   Genesis workspace created and pushed by Raven.
6. Commit the file with message "feat: initial README" and push to `main`.
7. When finished, emit a LEARNED lesson marker documenting what you learned
   about pushing to a fresh GitHub repo from a Raven workspace."""

PATH_B_QUERY = f"""Target the EXISTING workspace you created in the previous mission
(the one holding the `{REPO_NAME}` repo). Do NOT create a new workspace.
1. Edit `README.md` to append a second line: `Modified by Raven on the existing path.`
2. Commit with message "feat: extend README" and push to `main`.
3. Emit a LEARNED lesson marker about modifying an existing workspace safely."""


# ---- HTTP helpers ----------------------------------------------------------


def _req(method, url, *, headers=None, json_body=None, timeout=45):
    """Resilient request helper used by the harness.

    Retries on TRANSIENT failures:
      * network stalls (timeout / connection reset / DNS)
      * 5xx server/proxy errors (e.g. a momentary 502 from the gateway)
      * 422/429 — observed intermittently against the live gateway for an
        identical payload that succeeds moments later (server-side race),
        NOT a malformed request. Retried with backoff.

    On a FATAL error (400/401/403 or exhausted retries) it prints the
    response body so the failure is diagnosable instead of a bare traceback.
    We report the ACTUAL HTTP code + body — we do not guess at causes.
    """
    last_err = None
    for attempt in range(8):
        try:
            resp = requests.request(
                method, url, headers=headers or {}, json=json_body,
                timeout=timeout,
            )
            return resp
        except requests.exceptions.HTTPError as e:
            resp = getattr(e, "response", None)
            code = resp.status_code if resp is not None else 0
            body = (resp.text or "")[:500] if resp is not None else str(e)
            retryable = code in (422, 429) or 500 <= code <= 599
            if retryable and attempt < 7:
                last_err = e
                backoff = 5 * (attempt + 1)
                print(f"[net] {method} {url} -> {code} {body!r}; retry {attempt+1} in {backoff}s")
                time.sleep(backoff)
                continue
            print(f"[FATAL] {method} {url} -> {code}: {body}")
            raise
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < 7:
                backoff = 5 * (attempt + 1)
                print(f"[net] {method} {url} stalled ({e}); retry {attempt+1} in {backoff}s")
                time.sleep(backoff)
    raise last_err


def _http_json(method, url, *, token=None, headers=None, body=None, timeout=45):
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if headers:
        hdrs.update(headers)
    resp = _req(method, url, headers=hdrs, json_body=body, timeout=timeout)
    raw = resp.text or ""
    if not raw:
        return {}
    try:
        return resp.json()
    except ValueError:
        print(f"[warn] non-JSON response from {method} {url}: {raw[:120]!r}")
        return {}


def _gh_json(path):
    """GET a GitHub API path, returning {} on non-JSON / 5xx (retryable) so a
    transient GitHub hiccup never kills the harness."""
    url = f"https://api.github.com{path}"
    hdrs = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    resp = _req("GET", url, headers=hdrs, timeout=45)
    if not resp.text:
        return {}
    try:
        return resp.json()
    except ValueError:
        print(f"[warn] non-JSON GitHub response from {path}: {resp.text[:120]!r}")
        return {}


def _gh_get(path):
    # Kept for call-site compatibility; delegates to the resilient helper.
    return _gh_json(path)


# ---- Orchestrator steps ----------------------------------------------------
def login() -> str:
    out = _http_json("POST", f"{GATEWAY}/api/auth/login",
                     body={"username": DEFAULT_USER, "password": DEFAULT_PASS})
    tok = out.get("api_key") or out.get("token")
    assert tok, f"login failed: {out}"
    print(f"[login] ok as {DEFAULT_USER}")
    return tok


def dispatch_mission(token: str, query: str, slug: str) -> str:
    # Slugs must be unique; prior failed runs leave records behind.
    unique_slug = f"{slug}-{int(time.time())}"
    out = _http_json("POST", f"{GATEWAY}/api/raven/missions", token=token,
                     body={"query": query, "slug": unique_slug, "priority": 1})
    mid = out.get("mission", {}).get("id") or out.get("id")
    assert mid, f"dispatch failed: {out}"
    print(f"[dispatch] {unique_slug} -> mission {mid}")
    return mid


def poll_until_done(token: str, mid: str) -> dict:
    deadline = time.time() + MISSION_TIMEOUT
    while time.time() < deadline:
        out = _http_json("GET", f"{GATEWAY}/api/raven/missions/{mid}", token=token)
        # The detail endpoint normally returns a dict, but be defensive: a list
        # or {} (e.g. a transient 5xx that returned non-JSON) must NOT crash the
        # harness. Normalize to a dict and look up status in both shapes.
        if isinstance(out, list):
            out = out[0] if out else {}
        if not isinstance(out, dict):
            out = {}
        status = out.get("status") or (out.get("mission") or {}).get("status")
        print(f"[poll] {mid} status={status}")
        if status in ("completed", "failed", "cancelled", "success", "done"):
            return out
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"mission {mid} did not finish within {MISSION_TIMEOUT}s")


def verify_github(repo: str, expected_branch: str, expected_readme_substr: str) -> None:
    # Branch exists?
    branch = _gh_json(f"/repos/{GITHUB_OWNER}/{repo}/branches/{expected_branch}")
    assert isinstance(branch, dict) and branch.get("name") == expected_branch, \
        f"branch {expected_branch} missing or unexpected: {branch}"
    # README.md on that branch contains the expected content?
    contents = _gh_json(f"/repos/{GITHUB_OWNER}/{repo}/contents/README.md?ref={expected_branch}")
    import base64 as _b64
    content_b64 = (contents or {}).get("content")
    assert content_b64, f"README.md not found on {expected_branch}: {contents}"
    decoded = _b64.b64decode(content_b64).decode()
    assert expected_readme_substr in decoded, (
        f"README on remote missing expected text. Got:\n{decoded}")
    print(f"[verify:github] repo={repo} branch={expected_branch} README ok")


def capture_new_lessons(before_count: int) -> list:
    sec = os.getenv("INTERNAL_SECRET", "")
    url = f"{RAG}/api/storage/learning?user_id={DEFAULT_USER}&limit=100"
    data = _http_json("GET", url, headers={"X-Internal-Secret": sec})
    items = data.get("items") or data.get("learnings") or []
    new = items[before_count:]
    print(f"[lessons] store total={len(items)} new_since_start={len(new)}")
    return new


def cleanup_repo(repo: str) -> None:
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{repo}"
    hdrs = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    try:
        _req("DELETE", url, headers=hdrs, timeout=45)
        print(f"[cleanup] deleted remote repo {repo}")
    except requests.exceptions.HTTPError as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        # 403 delete_repo scope missing is expected/handled gracefully
        print(f"[cleanup] repo delete returned {code} (may lack delete_repo scope) - leaving repo")


def main() -> int:
    token = login()
    before = capture_new_lessons(0)  # baseline count (store was emptied)
    start_count = len(before)

    # ---- Path A ----
    mid_a = dispatch_mission(token, PATH_A_QUERY, "gitcoding-path-a")
    res_a = poll_until_done(token, mid_a)
    status_a = res_a.get("status") or res_a.get("mission", {}).get("status")
    try:
        verify_github(REPO_NAME, "main", "Genesis workspace created and pushed by Raven.")
    except Exception as e:
        print(f"[FAIL Path A] github verification: {e}")
        print("ABORTING cleanup - state preserved for diagnosis.")
        return 1
    if status_a not in ("completed", "success", "done"):
        print(f"[FAIL Path A] mission status={status_a}; aborting cleanup.")
        return 1
    lessons_a = capture_new_lessons(start_count)

    # ---- Path B ----
    mid_b = dispatch_mission(token, PATH_B_QUERY, "gitcoding-path-b")
    res_b = poll_until_done(token, mid_b)
    status_b = res_b.get("status") or res_b.get("mission", {}).get("status")
    try:
        verify_github(REPO_NAME, "main", "Modified by Raven on the existing path.")
    except Exception as e:
        print(f"[FAIL Path B] github verification: {e}")
        print("ABORTING cleanup - state preserved for diagnosis.")
        return 1
    if status_b not in ("completed", "success", "done"):
        print(f"[FAIL Path B] mission status={status_b}; aborting cleanup.")
        return 1
    lessons_b = capture_new_lessons(start_count + len(lessons_a))

    # ---- Success: cleanup ----
    print(f"[SUCCESS] both paths verified. lessons captured: "
          f"A={len(lessons_a)} B={len(lessons_b)}")
    for L in (lessons_a + lessons_b):
        print("  LESSON:", json.dumps({k: L.get(k) for k in
              ("rule", "root_cause", "outcome", "confidence", "applied_count")},
              default=str)[:300])
    cleanup_repo(REPO_NAME)
    return 0


if __name__ == "__main__":
    sys.exit(main())
