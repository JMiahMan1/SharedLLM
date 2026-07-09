# services/execution/handlers/repo_guard.py
"""
Repo-write guardrail: prevents autonomous/test workspaces from pushing to
protected repositories (e.g. the production SharedLLM repo).

Why this exists:
    A Raven mission that said "create a new repo" was run inside a workspace
    that had been cloned from SharedLLM (the test had bound the workspace to
    SharedLLM.git). The model committed to the existing checkout and ran
    `git push -u origin HEAD` through the shell handler, pushing to SharedLLM's
    `microservices` branch. Nothing on the server stopped it.

Policy:
    * A small set of PROTECTED repositories must never receive pushes from an
      arbitrary workspace.
    * ONLY the explicitly-designated SharedLLM development workspace(s) may push
      there. That is the supported path for "Raven fixes a bug in SharedLLM"
      later — it runs in that specific workspace.
    * Every other workspace (including all test workspaces) is hard-blocked from
      pushing to / setting a remote to a protected repo, regardless of whether
      the push goes through the git API or a raw shell `git push`.

Configuration (env-overridable):
    PROTECTED_REPO_PATTERNS      comma-separated URL substrings (default SharedLLM)
    SHAREDLLM_DEV_WORKSPACE_IDS  comma-separated workspace ids allowed to push
"""
import os
import re

PROTECTED_REPO_PATTERNS: list[str] = [
    p.strip()
    for p in os.getenv(
        "PROTECTED_REPO_PATTERNS", "JMiahMan1/SharedLLM,JMiahMan1/SharedLLM.git"
    ).split(",")
    if p.strip()
]

SHAREDLLM_DEV_WORKSPACE_IDS: set[str] = {
    w.strip()
    for w in os.getenv("SHAREDLLM_DEV_WORKSPACE_IDS", "sharedllm,sharedllm-dev").split(",")
    if w.strip()
}

# Matches:  git remote add origin <url>   /   git remote set-url origin <url>
_REMOTE_SET_RE = re.compile(r"\bgit\s+remote\s+(?:add|set-url)\s+\S+\s+(\S+)", re.IGNORECASE)


def is_protected_repo(url: str | None) -> bool:
    if not url:
        return False
    u = (url or "").lower()
    return any(p.lower() in u for p in PROTECTED_REPO_PATTERNS)


def push_to_protected_allowed(workspace_id: str | None, remote_url: str | None) -> tuple[bool, str]:
    """Return (allowed, reason).

    Protected repos may only be pushed to from a designated dev workspace.
    Non-protected repos (e.g. a freshly created raven-e2e-<ts> repo) are always
    allowed.
    """
    if not is_protected_repo(remote_url):
        return True, ""
    if workspace_id and workspace_id in SHAREDLLM_DEV_WORKSPACE_IDS:
        return True, ""
    return False, (
        f"BLOCKED: pushing to protected repository '{remote_url}' is not permitted "
        f"from workspace '{workspace_id}'. Only the designated SharedLLM development "
        f"workspace ({', '.join(sorted(SHAREDLLM_DEV_WORKSPACE_IDS))}) may push there. "
        f"Use a dedicated workspace for this change."
    )


def extract_remote_url_from_command(command: str) -> str | None:
    """If the command explicitly sets a remote URL, return it; else None.

    When None is returned the caller should resolve the configured origin via
    `git remote get-url origin` before deciding.
    """
    m = _REMOTE_SET_RE.search(command or "")
    if m:
        return m.group(1)
    return None
