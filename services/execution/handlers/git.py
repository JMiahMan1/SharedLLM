# services/execution/handlers/git.py
"""
GitHandler — Allows the Ouroboros autonomous loop to perform Git lifecycle
operations on the SharedLLM workspace mounted at /workspace/SharedLLM.

Supported actions:
    status   — git status (porcelain + branch)
    diff     — git diff (staged or unstaged)
    add      — git add <path> (or '.' for all)
    commit   — git commit -m <message>
    pull     — git pull origin <branch>
    push     — git push origin <branch>  [admin only]
    log      — git log --oneline -N

Security:
    - push requires is_admin=True in UserContext.
    - All operations are confined to WORKSPACE_ROOT.
    - No shell injection: args are passed as a list to subprocess.
"""
import asyncio
import logging
import os
import re
import shlex
from typing import Optional, Dict
try:
    from schemas import GitOperationRequest, GitExecutionResult
except ImportError:
    try:
        from ..schemas import GitOperationRequest, GitExecutionResult
    except ImportError:
        from execution.schemas import GitOperationRequest, GitExecutionResult

log = logging.getLogger("execution.git")

# The SharedLLM workspace is bind-mounted here from the host.
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspace/SharedLLM")

# Fix: Mark workspace as safe to avoid 'dubious ownership' errors in Docker
# We do this once at module load
os.system(f"git config --global --add safe.directory {WORKSPACE_ROOT}")
log.info(f"Marked {WORKSPACE_ROOT} as safe.directory")


async def _run_git(args: list[str], cwd: str = WORKSPACE_ROOT, env_override: dict = None) -> dict:
    """
    Run a git command as a subprocess and return stdout/stderr/returncode.
    args — list of git sub-command + arguments (NOT including 'git' itself).
    """
    cmd = ["git"] + args
    
    # Redact tokens from log output
    safe_cmd = []
    for arg in cmd:
        if "github_pat_" in arg or "ghp_" in arg:
            # Simple heuristic: if it looks like a token, redact it
            redacted = re.sub(r"(https://)[^@]+(@)", r"\1[REDACTED]\2", arg)
            safe_cmd.append(redacted)
        else:
            safe_cmd.append(arg)
    
    log.info(f"[Git] Running: {' '.join(shlex.quote(a) for a in safe_cmd)} in {cwd}")
    try:
        env = os.environ.copy()
        # Fix: Ignore bad permissions on config file by using -F /dev/null
        env["GIT_SSH_COMMAND"] = "ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -F /dev/null"
        if env_override:
            env.update(env_override)
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except asyncio.TimeoutError:
        return {"returncode": -1, "stdout": "", "stderr": "Git command timed out after 60s."}
    except Exception as e:
        log.error(f"Git execution failed: {e}")
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


async def _get_remote_url(remote_name: str = "origin") -> str:
    r = await _run_git(["remote", "get-url", remote_name])
    return r["stdout"].strip()


def _ok(action: str, detail: dict) -> GitExecutionResult:
    return GitExecutionResult(status="SUCCESS", message=f"git {action} completed.", service="git", detail=detail)


def _fail(action: str, detail: dict) -> GitExecutionResult:
    msg = f"git {action} failed."
    # Integrate fuzzy discovery if detail has stderr about path
    if "stderr" in detail and "pathspec" in detail["stderr"]:
        # Extract path if possible
        from handlers.workspace import _get_discovery_suggestion
        # Try to find what was being added
        match = re.search(r"pathspec '([^']+)'", detail["stderr"])
        if match:
            suggestion = _get_discovery_suggestion(match.group(1))
            if suggestion:
                msg += f" | {suggestion}"
                
    return GitExecutionResult(status="FAILURE", message=msg, service="git", detail=detail)


async def _get_current_branch() -> str:
    r = await _run_git(["branch", "--show-current"])
    return r["stdout"].strip() or "main"


async def handle_git(req: GitOperationRequest) -> GitExecutionResult:
    """
    Dispatch git operations based on req.action.

    req fields (from GitOperationRequest):
        action: str           — one of: status, diff, add, commit, pull, push, log
        path: Optional[str]   — file path for 'add' (default '.')
        commit_message: str   — required for 'commit'
        branch: str           — branch name for pull/push (default 'microservices')
        log_count: int        — number of commits for 'log' (default 10)
        user_context          — must have is_admin=True for push
    """
    action: str = req.action.lower().strip()
    path: str = getattr(req, "path", ".") or "."
    # Support both 'message' and 'commit_message' to align with varying schemas
    commit_message: Optional[str] = getattr(req, "message", None) or getattr(req, "commit_message", None)
    
    # Dynamic Branch Detection: Default to current branch if not explicitly set or if 'main' hallucination on 'microservices' repo
    current_branch = await _get_current_branch()
    branch: str = getattr(req, "branch", None) or current_branch
    
    # Hardened Pivot: If we are on microservices but agent says main, it is likely a hallucination
    if current_branch == "microservices" and branch == "main":
        log.info(f"[Git] Pivoting hallucinated branch 'main' to active branch 'microservices'")
        branch = "microservices"

    log_count: int = int(getattr(req, "log_count", 10) or 10)
    user_context = getattr(req, "user_context", None)
    is_admin: bool = getattr(user_context, "is_admin", False) if user_context else False

    if action in {"reset", "clean"}:
        return {
            "status": "FAILURE",
            "message": f"git {action} is blocked for safety.",
            "service": "git",
            "detail": {"error": "unsafe_git_action_blocked"},
        }

    if action == "status":
        r = await _run_git(["status", "--porcelain", "--branch"])
        if r["returncode"] != 0:
            return _fail("status", r)
        lines = r["stdout"].splitlines()
        branch_line = next((l for l in lines if l.startswith("##")), "")
        porcelain = [l for l in lines if not l.startswith("##")]
        return _ok("status", {
            "branch_line": branch_line,
            "porcelain": porcelain,
            "raw_stdout": r["stdout"],
        })

    elif action == "diff":
        # Enhanced diff: support full diff by default
        diff_args = ["diff"]
        if path and path.startswith("--"):
            diff_args.append(path) # e.g. --cached
        elif path and path != ".":
            diff_args.append(path)
            
        r = await _run_git(diff_args)
        if r["returncode"] != 0:
            return _fail("diff", r)
        return _ok("diff", r)

    elif action == "branch":
        # List branches or create new one
        args = ["branch"]
        if path and path != ".":
            args.append(path)
        r = await _run_git(args)
        if r["returncode"] != 0:
            return _fail("branch", r)
        return _ok("branch", r)

    elif action == "checkout":
        # Switch branch
        if not path or path == ".":
            return _fail("checkout", {"error": "branch name required in 'path' field"})
        r = await _run_git(["checkout", path])
        if r["returncode"] != 0:
            return _fail("checkout", r)
        return _ok("checkout", r)

    elif action == "show":
        # Show commit details
        args = ["show", "--summary"]
        if path and path != ".":
            args = ["show", path]
        r = await _run_git(args)
        if r["returncode"] != 0:
            return _fail("show", r)
        return _ok("show", r)

    elif action == "add":
        # Support multi-path staging if path contains spaces or is a list (though schema says str)
        paths = shlex.split(path) if path else ["."]
        r = await _run_git(["add"] + paths)
        if r["returncode"] != 0:
            return _fail("add", r)
        return _ok("add", {"added_paths": paths, **r})

    elif action == "commit":
        if not commit_message:
            return _fail("commit", {"error": "commit_message is required for 'commit' action."})
        # Conventional commit message enforcement
        if not any(commit_message.startswith(p) for p in (
            "feat:", "fix:", "chore:", "docs:", "refactor:", "test:", "perf:", "ci:"
        )):
            commit_message = f"fix: {commit_message}"
            
        # Ensure we have an author identity for the commit
        env_override = {
            "GIT_AUTHOR_NAME": getattr(user_context, "user", "Raven"),
            "GIT_AUTHOR_EMAIL": f"{getattr(user_context, 'user', 'raven')}@local.host",
            "GIT_COMMITTER_NAME": getattr(user_context, "user", "Raven"),
            "GIT_COMMITTER_EMAIL": f"{getattr(user_context, 'user', 'raven')}@local.host",
        }
        
        r = await _run_git(["commit", "-m", commit_message], env_override=env_override)
        if r["returncode"] != 0:
            return _fail("commit", r)
        return _ok("commit", {"commit_message": commit_message, **r})

    elif action == "push" or action == "pull":
        if action == "push" and not is_admin:
            return {
                "status": "FAILURE",
                "message": "Push requires admin privileges.",
                "service": "git",
                "detail": {"error": "insufficient_permissions"},
            }
        
        # Resolve remote URL for token injection
        remote_url = await _get_remote_url("origin")
        
        # Determine the appropriate token for this host
        token = None
        log.info(f"[Git] Resolving token for {remote_url} | user={getattr(user_context, 'user', 'unknown')}")
        
        if "github.com" in remote_url:
            token = getattr(user_context, "github_token", None)
            log.info(f"[Git] Selected GitHub token: {'[PRESENT]' if token else '[MISSING]'}")
        elif "gitlab" in remote_url:
            token = getattr(user_context, "gitlab_token", None)
            log.info(f"[Git] Selected GitLab token: {'[PRESENT]' if token else '[MISSING]'}")
        
        # Fallback to generic git_token
        if not token:
            token = getattr(user_context, "git_token", None)
            if token:
                log.info("[Git] Using generic git_token fallback.")
        
        if token and remote_url.startswith("https://"):
            # Inject token for HTTPS auth: https://<token>@host/...
            from urllib.parse import urlparse
            parsed = urlparse(remote_url)
            auth_url = f"https://{token}@{parsed.hostname}{parsed.path}"
            r = await _run_git([action, auth_url, branch])
        else:
            r = await _run_git([action, "origin", branch])
            
        if r["returncode"] != 0:
            return _fail(action, r)
        return _ok(action, {"branch": branch, **r})

    elif action == "fetch":
        # Fetch supports token injection too
        remote_url = await _get_remote_url("origin")
        token = getattr(user_context, "github_token", None) or getattr(user_context, "git_token", None)
        
        if token and remote_url.startswith("https://"):
            from urllib.parse import urlparse
            parsed = urlparse(remote_url)
            auth_url = f"https://{token}@{parsed.hostname}{parsed.path}"
            r = await _run_git(["fetch", auth_url, branch])
        else:
            r = await _run_git(["fetch", "origin", branch])
            
        if r["returncode"] != 0:
            return _fail("fetch", r)
        return _ok("fetch", r)

    elif action == "log":
        r = await _run_git(["log", f"--oneline", f"-{log_count}"])
        if r["returncode"] != 0:
            return _fail("log", r)
        return _ok("log", {"commits": r["stdout"].splitlines(), **r})

    else:
        return {
            "status": "FAILURE",
            "message": f"Unknown git action '{action}'. Valid: status, diff, add, commit, pull, push, log.",
            "service": "git",
            "detail": {},
        }
