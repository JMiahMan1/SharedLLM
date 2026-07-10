# services/execution/handlers/gh.py
"""
GhHandler — Allows Raven and external clients to run `gh` (GitHub CLI) commands
from within a workspace. This is the tool used to create repositories, open PRs,
manage issues, etc.

Security:
    - Only an allowlisted set of `gh` subcommands is permitted.
    - Destructive operations (auth, secret, ssh-key, api, repo delete/archive,
      pr close) are blocked.
    - All commands run confined to the resolved workspace path.
    - Authentication is injected via GH_TOKEN from the user_context (no shell
      token leakage); auth state is never read or written by the agent.
    - Args are passed as a list to subprocess (no shell injection).
"""
import asyncio
import logging
import os
import shlex

from fastapi import HTTPException
import requests

from services.config import WORKSPACE_ROOT, WORKSPACE_RUNTIME_SVC_URL, INTERNAL_SECRET

try:
    from handlers.workspace import _require_capability, _resolve_workspace_info, resolve_safe_path
    from schemas import GhRequest
except ImportError:
    from ..schemas import GhRequest
    from .workspace import _require_capability, _resolve_workspace_info, resolve_safe_path

log = logging.getLogger("execution.gh")

# Subcommands that are always allowed (read or safe create operations).
ALLOWED_SUBCOMMANDS = {
    "repo", "pr", "issue", "workflow", "release", "gist", "label",
    "milestone", "search", "status", "version",
}

# Subcommands that are never allowed (credential / destructive surface).
BLOCKED_SUBCOMMANDS = {
    "auth", "secret", "ssh-key", "api", "extension", "copilot", "billing",
    "alias", "config", "ruleset",
}

# Blocked <subcommand> <action> pairs (e.g. "repo delete").
BLOCKED_ACTIONS = {
    ("repo", "delete"),
    ("repo", "archive"),
    ("repo", "deploy-key"),
    ("repo", "sync"),
    ("pr", "close"),
    ("pr", "ready"),
    ("release", "delete"),
    ("issue", "delete"),
    ("workflow", "disable"),
    ("workflow", "delete"),
}

# Actions that require write capability on the workspace.
WRITE_ACTIONS = {
    ("repo", "create"),
    ("repo", "fork"),
    ("repo", "clone"),
    ("pr", "create"),
    ("pr", "merge"),
    ("pr", "reopen"),
    ("pr", "edit"),
    ("issue", "create"),
    ("issue", "edit"),
    ("issue", "close"),
    ("issue", "reopen"),
    ("issue", "comment"),
    ("release", "create"),
    ("release", "upload"),
    ("release", "edit"),
    ("label", "create"),
    ("label", "delete"),
    ("label", "edit"),
    ("milestone", "create"),
    ("milestone", "delete"),
    ("milestone", "edit"),
    ("gist", "create"),
    ("workflow", "run"),
    ("workflow", "enable"),
}


def _ok(message: str, detail: dict) -> dict:
    return {"status": "SUCCESS", "message": message, "service": "gh", "detail": detail}


def _fail(message: str, detail: dict) -> dict:
    return {"status": "FAILURE", "message": message, "service": "gh", "detail": detail}


def _validate(args: list[str]) -> tuple[str, str] | None:
    """Return (subcommand, action) if allowed, else None (caller raises)."""
    if not args:
        return None
    sub = args[0]
    action = args[1] if len(args) > 1 else ""
    if sub in BLOCKED_SUBCOMMANDS:
        return None
    if sub not in ALLOWED_SUBCOMMANDS:
        return None
    if (sub, action) in BLOCKED_ACTIONS:
        return None
    return (sub, action)


async def _run_gh(args: list[str], cwd: str, env_override: dict | None, timeout: int) -> dict:
    cmd = ["gh", *args]
    safe_cmd = [("[REDACTED]" if "github_pat_" in a or a.startswith("ghp_") else a) for a in cmd]
    log.info(f"[gh] Running: {' '.join(shlex.quote(a) for a in safe_cmd)} in {cwd}")
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except TimeoutError:
        return {"returncode": -1, "stdout": "", "stderr": f"gh command timed out after {timeout}s."}
    except FileNotFoundError:
        return {"returncode": -1, "stdout": "", "stderr": "gh CLI is not installed in this environment."}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


async def handle_gh(req: GhRequest) -> dict:
    args = list(req.args or [])
    if not args:
        return _fail("No gh arguments provided.", {"error": "empty_args"})

    validated = _validate(args)
    if validated is None:
        redacted = " ".join(a if "github_pat_" not in a and not a.startswith("ghp_") else "[REDACTED]" for a in args)
        return _fail(
            f"gh subcommand '{' '.join(args[:2])}' is not permitted.",
            {"error": "blocked_subcommand", "attempted": redacted},
        )
    sub, action = validated

    workspace_id = getattr(req, "workspace_id", None)
    user_context = getattr(req, "user_context", None)
    github_token = getattr(user_context, "github_token", None) if user_context else None
    if not github_token and isinstance(user_context, dict):
        github_token = user_context.get("github_token")

    # Auth pre-check: any write/remote GitHub action requires an authenticated token.
    requires_auth = (sub, action) in WRITE_ACTIONS or sub in {"repo", "pr", "release", "workflow", "gist"}
    if requires_auth and not github_token:
        return _fail(
            "GitHub authentication required: no github_token present in the user context. "
            f"Cannot perform 'gh {' '.join(args[:2])}'. Connect a GitHub account in Settings.",
            {"error": "auth_required", "subcommand": sub, "action": action},
        )

    try:
        ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_context)
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"[gh] workspace resolution failed: {e}")
        ws_root, ws_details = WORKSPACE_ROOT, {}

    safe_cwd = req.cwd or "."
    abs_cwd = resolve_safe_path(safe_cwd, ws_root or WORKSPACE_ROOT or ".")

    # Capability check
    required_cap = "git_write" if (sub, action) in WRITE_ACTIONS else "git_status"
    if ws_details:
        try:
            _require_capability(ws_details, required_cap)
        except HTTPException:
            # Fall back to read capability for read-only operations
            if required_cap != "git_status":
                _require_capability(ws_details, "read")

    # Auth injection: prefer the user's GitHub token via GH_TOKEN (no credential state touched)
    env_override: dict[str, str] = {}
    if github_token:
        env_override["GH_TOKEN"] = github_token
        # Use token auth and never prompt / write to a credential store
        env_override["GH_ENTERPRISE_TOKEN"] = github_token
    env_override["GH_PROMPT_DISABLED"] = "1"

    timeout = min(getattr(req, "timeout", 120) or 120, 300)
    result = await _run_gh(args, abs_cwd, env_override, timeout)

    if result["returncode"] != 0:
        return _fail(f"gh {' '.join(args[:2])} failed.", result)

    # When a repository was just created, bind it to the workspace so the
    # subsequent `git push` is permitted by the per-workspace guardrail (which
    # only allows pushes to the workspace's designated repo_url). Without this,
    # "create repo then push" would be blocked.
    if sub == "repo" and action == "create":
        repo_name = _extract_repo_name(args)
        if repo_name:
            created_url = await _created_repo_url(repo_name, abs_cwd, env_override, timeout)
            if created_url:
                await _bind_workspace_repo(workspace_id, created_url)

    return _ok(f"gh {' '.join(args[:2])} completed.", result)


# Value-taking `gh repo create` flags whose immediate next token is NOT the name.
_REPO_CREATE_VALUE_FLAGS = {
    "--source", "-s", "--description", "-d", "--homepage", "-h",
    "--team", "-t", "--template", "--license", "-l", "--gitignore",
}


def _extract_repo_name(args: list[str]) -> str | None:
    """Extract the repo name from `gh repo create <name> [flags]`."""
    i = 2  # skip ["repo", "create"]
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            if a in _REPO_CREATE_VALUE_FLAGS and i + 1 < len(args):
                i += 2
                continue
            i += 1
            continue
        return a
    return None


async def _created_repo_url(repo_name: str, cwd: str, env_override: dict, timeout: int) -> str | None:
    """Resolve the HTTPS URL of a freshly created repo via `gh repo view`."""
    view = await _run_gh(
        ["repo", "view", repo_name, "--json", "url", "-q", ".url"],
        cwd, env_override, timeout,
    )
    if view["returncode"] == 0 and view["stdout"].strip():
        return view["stdout"].strip()
    return None


async def _bind_workspace_repo(workspace_id: str | None, repo_url: str) -> None:
    """Best-effort: PATCH the workspace's repo_url to the created repo."""
    if not workspace_id:
        return
    try:
        resp = await asyncio.to_thread(
            requests.patch,
            f"{WORKSPACE_RUNTIME_SVC_URL}/workspaces/{workspace_id}",
            json={"repo_url": repo_url},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info(f"[gh] Bound workspace {workspace_id} to created repo {repo_url}")
        else:
            log.warning(
                f"[gh] Failed to bind workspace {workspace_id} to {repo_url}: "
                f"{resp.status_code} {resp.text[:200]}"
            )
    except Exception as e:
        log.warning(f"[gh] Exception binding workspace {workspace_id} to {repo_url}: {e}")
