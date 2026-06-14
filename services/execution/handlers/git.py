# services/execution/handlers/git.py
"""
GitHandler — Allows the Ouroboros autonomous loop to perform Git lifecycle
operations on workspaces.

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
    - All operations are confined to the resolved workspace path.
    - No shell injection: args are passed as a list to subprocess.
"""
import asyncio
import logging
import os
import re
import shlex
from fastapi import HTTPException
from services.config import WORKSPACE_ROOT, WORKSPACE_RUNTIME_SVC_URL, INTERNAL_SECRET
from typing import Optional
try:
    from schemas import GitOperationRequest, GitExecutionResult
except ImportError:
    from ..schemas import GitOperationRequest, GitExecutionResult

log = logging.getLogger("execution.git")

# Fix: Mark workspace root as safe to avoid 'dubious ownership' errors in Docker
os.system(f"git config --global --add safe.directory {WORKSPACE_ROOT}")
os.system(f"git config --global --add safe.directory '{WORKSPACE_ROOT}/*'")
log.info(f"Marked {WORKSPACE_ROOT} and subdirectories as safe.directory")


async def _resolve_workspace_path(
    workspace_id: Optional[str] = None,
    user_context: Optional[dict] = None,
    required_capability: Optional[str] = None
) -> str:
    """Resolve workspace path from workspace_runtime service and check capability.
    
    Priority:
    1. Explicit workspace_id
    2. Workspace marked as is_default=True
    3. First available workspace with git capabilities
    4. Fallback to WORKSPACE_ROOT
    """
    import httpx
    
    # Try to resolve specific workspace
    if workspace_id:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                user_ctx = user_context or {"user": "system", "is_admin": True}
                if hasattr(user_ctx, "model_dump"):
                    user_ctx = user_ctx.model_dump()
                elif hasattr(user_ctx, "dict"):
                    user_ctx = user_ctx.dict()
                    
                resp = await client.post(
                    f"{WORKSPACE_RUNTIME_SVC_URL}/workspace/resolve",
                    json={"workspace_id": workspace_id, "user_context": user_ctx},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "SUCCESS":
                        workspace = data["workspace"]
                        if required_capability:
                            identity = workspace.get("resolved_identity") or {}
                            if not identity.get("is_admin"):
                                capabilities = workspace.get("capabilities", [])
                                if required_capability not in capabilities:
                                    raise HTTPException(
                                        status_code=403,
                                        detail=f"Workspace '{workspace.get('id')}' does not allow capability '{required_capability}'"
                                    )
                        return workspace["resolved_path"]
                else:
                    try:
                        err_detail = resp.json().get("detail", resp.text)
                    except Exception:
                        err_detail = resp.text
                    raise HTTPException(status_code=resp.status_code, detail=err_detail)
        except HTTPException:
            raise
        except Exception as e:
            log.warning(f"Failed to resolve workspace {workspace_id}: {e}")
    
    # Fallback: list workspaces and find default or first available
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{WORKSPACE_RUNTIME_SVC_URL}/workspaces",
                params={"rag_user": "system"},
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                data = resp.json()
                workspaces = data.get("workspaces", []) if isinstance(data, dict) else data
                
                # First try to find the default workspace
                for ws in workspaces:
                    if ws.get("is_default") and ws.get("resolved_path"):
                        log.info(f"Using default workspace: {ws.get('id')} -> {ws['resolved_path']}")
                        return ws["resolved_path"]
                
                # Then try first workspace with git capabilities
                for ws in workspaces:
                    if ws.get("resolved_path") and "git_status" in ws.get("capabilities", []):
                        log.info(f"Using first git-capable workspace: {ws.get('id')} -> {ws['resolved_path']}")
                        return ws["resolved_path"]
                
                # Last resort: first workspace with any resolved path
                for ws in workspaces:
                    if ws.get("resolved_path"):
                        log.info(f"Using first available workspace: {ws.get('id')} -> {ws['resolved_path']}")
                        return ws["resolved_path"]
    except Exception as e:
        log.warning(f"Failed to list workspaces: {e}")
    
    log.warning(f"No workspace found, falling back to WORKSPACE_ROOT: {WORKSPACE_ROOT}")
    return WORKSPACE_ROOT


async def _run_git(args: list[str], cwd: str = WORKSPACE_ROOT, env_override: dict | None = None) -> dict:
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


async def _get_remote_url(remote_name: str = "origin", cwd: str = WORKSPACE_ROOT) -> str:
    r = await _run_git(["remote", "get-url", remote_name], cwd=cwd)
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


async def _get_current_branch(cwd: str = WORKSPACE_ROOT) -> str:
    r = await _run_git(["branch", "--show-current"], cwd=cwd)
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
        workspace_id: Optional[str] — workspace to operate on (uses default if not specified)
    """
    action: str = req.action.lower().strip()
    required_capability = "git_status"
    if action in {"add", "commit", "pull", "push", "checkout", "merge"}:
        required_capability = "git_write"

    # Resolve workspace path first
    workspace_id = getattr(req, "workspace_id", None)
    user_context = getattr(req, "user_context", None)
    workspace_path = await _resolve_workspace_path(workspace_id, user_context, required_capability)
    log.info(f"[Git] Resolved workspace path: {workspace_path} (workspace_id={workspace_id})")
    
    path: str = getattr(req, "path", ".") or "."
    # Support both 'message' and 'commit_message' to align with varying schemas
    commit_message: Optional[str] = getattr(req, "message", None) or getattr(req, "commit_message", None)
    
    # Dynamic Branch Detection: Default to current branch if not explicitly set or if 'main' hallucination on 'microservices' repo
    current_branch = await _get_current_branch(cwd=workspace_path)
    branch: str = getattr(req, "branch", None) or current_branch
    
    # Hardened Pivot: If we are on microservices but agent says main, it is likely a hallucination
    if current_branch == "microservices" and branch == "main":
        log.info("[Git] Pivoting hallucinated branch 'main' to active branch 'microservices'")
        branch = "microservices"

    log_count: int = int(getattr(req, "log_count", 10) or 10)
    user_context = getattr(req, "user_context", None)
    is_admin: bool = getattr(user_context, "is_admin", False) if user_context else False

    if action in {"reset", "clean"}:
        return GitExecutionResult(status="FAILURE", message=f"git {action} is blocked for safety.", service="git", detail={"error": "unsafe_git_action_blocked"})

    if action == "status":
        r = await _run_git(["status", "--porcelain", "--branch"], cwd=workspace_path)
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
            
        r = await _run_git(diff_args, cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("diff", r)
        return _ok("diff", r)

    elif action == "branch":
        # List branches or create new one
        args = ["branch"]
        if path and path != ".":
            args.append(path)
        r = await _run_git(args, cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("branch", r)
        return _ok("branch", r)

    elif action == "checkout":
        # Switch branch
        if not path or path == ".":
            return _fail("checkout", {"error": "branch name required in 'path' field"})
        r = await _run_git(["checkout", path], cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("checkout", r)
        return _ok("checkout", r)

    elif action == "show":
        # Show commit details
        args = ["show", "--summary"]
        if path and path != ".":
            args = ["show", path]
        r = await _run_git(args, cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("show", r)
        return _ok("show", r)

    elif action == "add":
        # Support multi-path staging if path contains spaces or is a list (though schema says str)
        paths = shlex.split(path) if path else ["."]
        r = await _run_git(["add"] + paths, cwd=workspace_path)
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
        
        r = await _run_git(["commit", "-m", commit_message], cwd=workspace_path, env_override=env_override)
        if r["returncode"] != 0:
            return _fail("commit", r)
        return _ok("commit", {"commit_message": commit_message, **r})

    elif action == "push" or action == "pull":
        if action == "push" and not is_admin:
            return GitExecutionResult(status="FAILURE", message="Push requires admin privileges.", service="git", detail={"error": "insufficient_permissions"})
        
        # Resolve remote URL for token injection
        remote_url = await _get_remote_url("origin", cwd=workspace_path)
        
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
            r = await _run_git([action, auth_url, branch], cwd=workspace_path)
        else:
            r = await _run_git([action, "origin", branch], cwd=workspace_path)
            
        if r["returncode"] != 0:
            return _fail(action, r)
        return _ok(action, {"branch": branch, **r})

    elif action == "fetch":
        # Fetch supports token injection too
        remote_url = await _get_remote_url("origin", cwd=workspace_path)
        token = getattr(user_context, "github_token", None) or getattr(user_context, "git_token", None)
        
        if token and remote_url.startswith("https://"):
            from urllib.parse import urlparse
            parsed = urlparse(remote_url)
            auth_url = f"https://{token}@{parsed.hostname}{parsed.path}"
            r = await _run_git(["fetch", auth_url, branch], cwd=workspace_path)
        else:
            r = await _run_git(["fetch", "origin", branch], cwd=workspace_path)
            
        if r["returncode"] != 0:
            return _fail("fetch", r)
        return _ok("fetch", r)

    elif action == "log":
        r = await _run_git(["log", "--oneline", f"-{log_count}"], cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("log", r)
        return _ok("log", {"commits": r["stdout"].splitlines(), **r})

    else:
        return GitExecutionResult(status="FAILURE", message=f"Unknown git action '{action}'. Valid: status, diff, add, commit, pull, push, log.", service="git", detail={})
