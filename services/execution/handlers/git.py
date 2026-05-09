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
import shlex
from typing import Optional

log = logging.getLogger("execution.git")

# The SharedLLM workspace is bind-mounted here from the host.
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspace/SharedLLM")

# Fix: Mark workspace as safe to avoid 'dubious ownership' errors in Docker
# We do this once at module load
os.system(f"git config --global --add safe.directory {WORKSPACE_ROOT}")
log.info(f"Marked {WORKSPACE_ROOT} as safe.directory")


async def _run_git(args: list[str], cwd: str = WORKSPACE_ROOT) -> dict:
    """
    Run a git command as a subprocess and return stdout/stderr/returncode.
    args — list of git sub-command + arguments (NOT including 'git' itself).
    """
    cmd = ["git"] + args
    log.info(f"[Git] Running: {' '.join(shlex.quote(a) for a in cmd)} in {cwd}")
    try:
        env = os.environ.copy()
        # Fix: Ignore bad permissions on config file by using -F /dev/null
        env["GIT_SSH_COMMAND"] = "ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -F /dev/null"
        
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
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def _ok(action: str, detail: dict) -> dict:
    return {"status": "SUCCESS", "message": f"git {action} completed.", "service": "git", "detail": detail}


def _fail(action: str, detail: dict) -> dict:
    return {"status": "FAILURE", "message": f"git {action} failed.", "service": "git", "detail": detail}


async def handle_git(req) -> dict:
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
    commit_message: Optional[str] = getattr(req, "commit_message", None)
    branch: str = getattr(req, "branch", "microservices") or "microservices"
    log_count: int = int(getattr(req, "log_count", 10) or 10)
    user_context = getattr(req, "user_context", None)
    is_admin: bool = getattr(user_context, "is_admin", False) if user_context else False

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
        r = await _run_git(["diff", "--stat"])
        if r["returncode"] != 0:
            return _fail("diff", r)
        return _ok("diff", {"diff_stat": r["stdout"]})

    elif action == "add":
        r = await _run_git(["add", path])
        if r["returncode"] != 0:
            return _fail("add", r)
        return _ok("add", {"added_path": path, **r})

    elif action == "commit":
        if not commit_message:
            return _fail("commit", {"error": "commit_message is required for 'commit' action."})
        # Conventional commit message enforcement
        if not any(commit_message.startswith(p) for p in (
            "feat:", "fix:", "chore:", "docs:", "refactor:", "test:", "perf:", "ci:"
        )):
            commit_message = f"fix: {commit_message}"
        r = await _run_git(["commit", "-m", commit_message])
        if r["returncode"] != 0:
            return _fail("commit", r)
        return _ok("commit", {"commit_message": commit_message, **r})

    elif action == "pull":
        r = await _run_git(["pull", "origin", branch])
        if r["returncode"] != 0:
            return _fail("pull", r)
        return _ok("pull", {"branch": branch, **r})

    elif action == "push":
        if not is_admin:
            return {
                "status": "FAILURE",
                "message": "Push requires admin privileges. Please authenticate as an admin user.",
                "service": "git",
                "detail": {"error": "insufficient_permissions"},
            }
        r = await _run_git(["push", "origin", branch])
        if r["returncode"] != 0:
            return _fail("push", r)
        return _ok("push", {"branch": branch, **r})

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
