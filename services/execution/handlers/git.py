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
    init     — git init
    remote_add — git remote add <name> <url>  (token-aware for https)
    repo_create — create a GitHub repo via API (token-aware) + wire origin remote
    repo_clone  — no-op: workspace is already the repo (sandbox has no `gh` CLI)
    gh_noop     — no-op: intercepted `gh` command with no git-tool equivalent

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
import traceback

from fastapi import HTTPException

from services.config import INTERNAL_SECRET, WORKSPACE_ROOT, WORKSPACE_RUNTIME_SVC_URL

try:
    from schemas import GitExecutionResult, GitOperationRequest
except ImportError:
    from ..schemas import GitExecutionResult, GitOperationRequest

log = logging.getLogger("execution.git")

# Fix: Mark workspace root as safe to avoid 'dubious ownership' errors in Docker
os.system(f"git config --global --add safe.directory {WORKSPACE_ROOT}")
os.system(f"git config --global --add safe.directory '{WORKSPACE_ROOT}/*'")
log.info(f"Marked {WORKSPACE_ROOT} and subdirectories as safe.directory")

# Default .gitignore seeded into every workspace on repo creation. Keeps
# training-repo noise (local journals, venvs, caches, model artifacts) out of
# GitHub pushes. Only written when the workspace has no .gitignore yet, so a
# model-authored one is never clobbered.
DEFAULT_GITIGNORE = """\
# Raven local training journal (per-workspace learning, not repo content)
raven_memory.md

# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Node / JS / TS
node_modules/
dist/
build/
*.tsbuildinfo
.next/

# Env / secrets
.env
.env.*
!.env.example

# OS / editor
.DS_Store
Thumbs.db
*.swp

# Model artifacts / caches
*.bin
*.gguf
models/
checkpoints/
"""


# ---------------------------------------------------------------------------
# Repo-write guardrail: a workspace may ONLY push to its OWN designated repository.
# There is intentionally NO hardcoded allow/deny list of specific repos — the
# policy is purely per-workspace. SharedLLM stays safe simply because no ordinary
# workspace is bound to it: a workspace may only push to the repo it is bound to.
# ---------------------------------------------------------------------------
def normalize_repo_url(url: str | None) -> str:
    """Canonicalize a repo URL for comparison (drops scheme, auth, .git, slashes)."""
    if not url:
        return ""
    u = str(url).strip().lower()
    if u.startswith("git@"):
        u = u[4:]
    u = u.replace("git+", "")
    u = re.sub(r"\.git$", "", u)
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^[^/@]+@", "", u)  # drop embedded credentials (user@host)
    u = u.replace(":", "/")  # ssh colon -> path slash
    return u.strip("/")


def push_allowed(workspace_repo_url: str | None, target_url: str | None) -> tuple[bool, str]:
    """Return (allowed, reason).

    Allowed iff the target remote URL matches the workspace's designated repo.
    If the workspace has no designated repo (e.g. a freshly created "netnew"
    workspace that just ran `gh repo create` and has not yet bound a repo_url),
    the push is ALLOWED — the caller binds repo_url on success so the per-workspace
    scope guardrail becomes effective for subsequent pushes. This supports the
    create-repo-then-push flow without a hardcoded repo allow-list. The push is
    still gated upstream by token/identity checks (shell path requires a GitHub
    token in the user context; the git API requires is_admin), so an unbound
    workspace can only push to repositories its credentials can write to.
    """
    target = normalize_repo_url(target_url)
    if not target:
        return False, "Cannot determine the push target repository; refusing to push."
    allowed = normalize_repo_url(workspace_repo_url)
    if allowed and target == allowed:
        return True, ""
    if not allowed:
        # Unbound workspace: allow the push; the caller binds repo_url on success.
        return True, ""
    return False, (
        f"Refusing to push to '{target_url}': this workspace is only permitted to "
        f"push to its designated repository ({workspace_repo_url})."
        + " Create or bind the intended repository first."
    )


async def _bind_workspace_repo(workspace_id: str | None, repo_url: str | None) -> None:
    """Best-effort: bind a repo_url to an (initially unbound) workspace after a
    successful first push, so the per-workspace push-scope guardrail becomes
    effective for subsequent pushes (create-repo-then-push flow).
    """
    if not workspace_id or not repo_url:
        return
    try:
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as client:
            await client.patch(
                f"{WORKSPACE_RUNTIME_SVC_URL}/workspaces/{workspace_id}",
                json={"repo_url": repo_url},
                headers={"X-Internal-Secret": INTERNAL_SECRET},
            )
    except Exception as e:  # pragma: no cover - best effort
        log.warning(f"Failed to bind repo_url for workspace {workspace_id}: {e}")


async def _get_workspace_repo_url(workspace_id: str | None) -> str | None:
    """Resolve the workspace's designated repo_url from the workspace_runtime service."""
    import aiohttp

    if not workspace_id:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as client, client.post(
            f"{WORKSPACE_RUNTIME_SVC_URL}/workspace/resolve",
            json={"workspace_id": workspace_id, "user_context": {"user": "system", "is_admin": True}},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "SUCCESS":
                    return (data.get("workspace") or {}).get("repo_url")
    except Exception as e:
        log.warning(f"Failed to resolve workspace repo_url for {workspace_id}: {e}")
    return None


async def _resolve_workspace_path(
    workspace_id: str | None = None,
    user_context: dict | None = None,
    required_capability: str | None = None
) -> str:
    """Resolve workspace path from workspace_runtime service and check capability.
    
    Priority:
    1. Explicit workspace_id
    2. Workspace marked as is_default=True
    3. First available workspace with git capabilities
    4. Fallback to WORKSPACE_ROOT
    """
    import aiohttp

    # Try to resolve specific workspace
    if workspace_id:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as client:
                user_ctx = user_context or {"user": "system", "is_admin": True}
                if hasattr(user_ctx, "model_dump"):
                    user_ctx = user_ctx.model_dump()
                elif hasattr(user_ctx, "dict"):
                    user_ctx = user_ctx.dict()

                async with client.post(
                    f"{WORKSPACE_RUNTIME_SVC_URL}/workspace/resolve",
                    json={"workspace_id": workspace_id, "user_context": user_ctx},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
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
                            err_detail = (await resp.json()).get("detail", await resp.text())
                        except Exception:
                            err_detail = await resp.text()
                        raise HTTPException(status_code=resp.status, detail=err_detail)
        except HTTPException:
            raise
        except Exception as e:
            log.warning(f"Failed to resolve workspace {workspace_id}: {e}")

    # Fallback: list workspaces and find default or first available
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as client, client.get(
            f"{WORKSPACE_RUNTIME_SVC_URL}/workspaces",
            params={"rag_user": "system"},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                workspaces = data.get("workspaces", []) if isinstance(data, dict) else data

                # First try to find the default workspace
                for ws in workspaces:
                    if ws.get("is_default") and ws.get("resolved_path"):
                        log.info(f"Using default workspace: {ws.get('id')} -> {ws['resolved_path']}")
                        return ws["resolved_path"]

                # Handle workspaces that require user context (resolved_path is null in list response)
                system_user_ctx = {"user": "system", "is_admin": True}
                for ws in workspaces:
                    ws_id = ws.get("id")
                    if ws_id and not ws.get("resolved_path") and ws.get("requires_user_context"):
                        try:
                            async with client.post(
                                f"{WORKSPACE_RUNTIME_SVC_URL}/workspace/resolve",
                                json={"workspace_id": ws_id, "user_context": system_user_ctx},
                                headers={"X-Internal-Secret": INTERNAL_SECRET}
                            ) as resolve_resp:
                                if resolve_resp.status == 200:
                                    resolve_data = await resolve_resp.json()
                                    if resolve_data.get("status") == "SUCCESS":
                                        resolved_path = resolve_data["workspace"].get("resolved_path")
                                        if resolved_path:
                                            log.info(f"Resolved workspace '{ws_id}' -> {resolved_path}")
                                            return resolved_path
                        except Exception as resolve_err:
                            log.warning(f"Failed to resolve workspace {ws_id}: {resolve_err}")

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
    except Exception:
        tb_str = traceback.format_exc()
        log.error(f"[GIT RESOLVE WORKSPACE FAILED] workspace_id={workspace_id}\n{tb_str}")

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
    except TimeoutError:
        tb_str = traceback.format_exc()
        log.error(f"[GIT TIMEOUT] cmd={' '.join(cmd)} cwd={cwd}\n{tb_str}")
        return {"returncode": -1, "stdout": "", "stderr": "Git command timed out after 60s."}
    except Exception as e:
        tb_str = traceback.format_exc()
        log.error(f"[GIT ERROR] cmd={' '.join(cmd)} cwd={cwd}\n{tb_str}")
        return {"returncode": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


async def _get_remote_url(remote_name: str = "origin", cwd: str = WORKSPACE_ROOT) -> str:
    r = await _run_git(["remote", "get-url", remote_name], cwd=cwd)
    return r["stdout"].strip()


async def _create_github_repo(
    token: str, repo_name: str, private: bool = False, description: str | None = None
) -> str | None:
    """Create a GitHub repository via the REST API using a personal access
    token. Returns the HTTPS clone URL on success, or None on failure.
    """
    import aiohttp

    api_url = "https://api.github.com/user/repos"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"name": repo_name, "private": bool(private)}
    if description:
        payload["description"] = description
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30.0)) as client:
            async with client.post(api_url, json=payload, headers=headers) as resp:
                if resp.status in (201, 200):
                    data = await resp.json()
                    return (data.get("clone_url") or data.get("ssh_url") or "").replace(".git", "")
                body = await resp.text()
                # 422 often means the repo already exists; try to resolve it.
                if resp.status == 422:
                    log.warning(f"[Git] repo_create 422 for {repo_name}: {body[:200]}")
                    existing = await _resolve_github_repo_url(token, repo_name)
                    if existing:
                        return existing
                log.warning(f"[Git] repo_create failed ({resp.status}): {body[:200]}")
                return None
    except Exception as e:
        log.error(f"[Git] repo_create exception: {e}")
        return None


async def _resolve_github_repo_url(token: str, repo_name: str) -> str | None:
    """Best-effort: resolve the clone URL of an existing repo owned by the token."""
    import aiohttp

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15.0)) as client:
            async with client.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            ) as resp:
                if resp.status != 200:
                    return None
                login = (await resp.json()).get("login")
            if not login:
                return None
            async with client.get(
                f"https://api.github.com/repos/{login}/{repo_name}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            ) as resp2:
                if resp2.status != 200:
                    return None
                data = await resp2.json()
                return (data.get("clone_url") or "").replace(".git", "")
    except Exception as e:
        log.warning(f"[Git] _resolve_github_repo_url failed: {e}")
        return None


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
    commit_message: str | None = getattr(req, "message", None) or getattr(req, "commit_message", None)

    # Dynamic Branch Detection: Default to current branch if not explicitly set or if 'main' hallucination on 'microservices' repo
    current_branch = await _get_current_branch(cwd=workspace_path)
    branch: str = getattr(req, "branch", None) or current_branch

    # Hardened Pivot: If we are on microservices but agent says main, it is likely a hallucination
    if current_branch == "microservices" and branch == "main":
        log.info("[Git] Pivoting hallucinated branch 'main' to active branch 'microservices'")
        branch = "microservices"

    log_count: int = int(getattr(req, "log_count", 10) or 10)
    user_context = getattr(req, "user_context", None)

    # The workspace is created EMPTY and is NOT necessarily a git repository
    # until the `gh repo create` step initializes it. The read-only git
    # inspection (e.g. the IDE's `git status`) must NOT hard-error in
    # that state. Write ops (commit/push/add/...) are intentionally
    # NOT guarded here: they fall through to their normal handling
    # (reset/clean are blocked; commit/push fail naturally with a clear
    # "not a git repository" so the model is steered to `gh repo
    # create` first, which inits + wires git). This keeps the
    # existing unit-test expectations intact.
    _is_repo = os.path.isdir(os.path.join(workspace_path, ".git"))
    if not _is_repo and action in ("status", "diff", "log", "branch", "remote", "show", "fetch"):
        return GitExecutionResult(
            status="SUCCESS",
            message=(
                "Workspace is not yet a git repository. Initialize it with "
                "`gh repo create <name> --private` (intercepted and wired for "
                "you) before committing/pushing."
            ),
            service="git",
            detail={"note": "not_a_git_repo", "action": action},
        )
    is_admin: bool = getattr(user_context, "is_admin", False) if user_context else False

    if action in {"reset", "clean"}:
        return GitExecutionResult(status="FAILURE", message=f"git {action} is blocked for safety.", service="git", detail={"error": "unsafe_git_action_blocked"})

    if action == "remote":
        # Show configured remotes (mirrors `git remote -v`).
        r = await _run_git(["remote", "-v"], cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("remote", r)
        return _ok("remote", {"remotes": r["stdout"].splitlines(), **r})

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

        if action == "push":
            # Guardrail: a workspace may ONLY push to its OWN designated repository.
            # No hardcoded repo lists — the policy is purely per-workspace. SharedLLM
            # stays safe because no ordinary workspace is bound to it. This is the
            # server-side backstop so a flaky model can never push to the wrong repo
            # (covers both the git API and raw shell pushes via workspace.py).
            ws_repo_url = await _get_workspace_repo_url(workspace_id)
            remote_url = await _get_remote_url("origin", cwd=workspace_path)
            _allowed, _reason = push_allowed(ws_repo_url, remote_url)
            if not _allowed:
                return GitExecutionResult(
                    status="FAILURE",
                    message=_reason,
                    service="git",
                    detail={"error": "wrong_repo_push_blocked"},
                )
        else:
            # pull is read-only; resolve remote URL for token injection only
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
        # Bind repo_url on the first successful push from an unbound workspace so
        # the per-workspace push-scope guardrail becomes effective going forward.
        if action == "push" and not ws_repo_url and remote_url:
            await _bind_workspace_repo(workspace_id, remote_url)
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

    elif action == "init":
        # Initialize a git repository in the workspace (idempotent for existing repos).
        r = await _run_git(["init"], cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("init", r)
        return _ok("init", r)

    elif action == "remote_add":
        remote_name = (getattr(req, "remote_name", None) or "origin").strip()
        remote_url_raw = getattr(req, "repo_url", None) or getattr(req, "remote_url", None)
        if not remote_url_raw:
            return _fail("remote_add", {"error": "repo_url is required for 'remote_add' action."})
        # Token-inject HTTPS remotes so the first push authenticates.
        remote_url = remote_url_raw
        token = getattr(user_context, "github_token", None) or getattr(user_context, "git_token", None)
        from urllib.parse import urlparse
        if token and remote_url.startswith("https://"):
            parsed = urlparse(remote_url)
            remote_url = f"https://{token}@{parsed.hostname}{parsed.path}"
        # Replace existing remote if present (idempotent).
        await _run_git(["remote", "remove", remote_name], cwd=workspace_path)
        r = await _run_git(["remote", "add", remote_name, remote_url], cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("remote_add", r)
        return _ok("remote_add", {"remote_name": remote_name, "repo_url": remote_url_raw, **r})

    elif action == "repo_create":
        # Create a GitHub repository via the REST API using the user's token
        # (no gh CLI / shell credentials needed), then wire it as the origin
        # remote so the subsequent git push (which injects the token) succeeds.
        token = getattr(user_context, "github_token", None) or getattr(user_context, "git_token", None)
        if not token:
            return GitExecutionResult(
                status="FAILURE",
                message="repo_create requires a GitHub token in the user context.",
                service="git",
                detail={"error": "auth_required"},
            )
        repo_name = (getattr(req, "repo_name", None) or "").strip()
        if not repo_name:
            return _fail("repo_create", {"error": "repo_name is required for 'repo_create' action."})
        created_url = await _create_github_repo(
            token,
            repo_name,
            private=bool(getattr(req, "private", False)),
            description=getattr(req, "description", None),
        )
        if not created_url:
            return GitExecutionResult(
                status="FAILURE",
                message=f"Failed to create GitHub repository '{repo_name}'.",
                service="git",
                detail={"error": "repo_create_failed"},
            )
        # The workspace directory may not yet be a git repository (it is created
        # empty). Initialize it so the origin remote can be wired and later
        # `git add/commit/push` succeed. `git init` is idempotent on an
        # existing repo.
        init_r = await _run_git(["init"], cwd=workspace_path)
        if init_r["returncode"] != 0:
            return _fail("repo_create", init_r)
        # Token-inject the clone URL for the origin remote.
        from urllib.parse import urlparse
        parsed = urlparse(created_url)
        auth_url = f"https://{token}@{parsed.hostname}{parsed.path}"
        await _run_git(["remote", "remove", "origin"], cwd=workspace_path)
        r = await _run_git(["remote", "add", "origin", auth_url], cwd=workspace_path)
        if r["returncode"] != 0:
            return _fail("repo_create", r)
        # Seed a default .gitignore (idempotent: never overwrites a model's own).
        _gi_path = os.path.join(workspace_path, ".gitignore")
        if not os.path.exists(_gi_path):
            try:
                with open(_gi_path, "w", encoding="utf-8") as _gf:
                    _gf.write(DEFAULT_GITIGNORE)
                log.info(f"[Git] Seeded default .gitignore at {_gi_path}")
            except Exception as _gie:
                log.warning(f"[Git] .gitignore seed skipped: {_gie}")
        # Bind repo_url to the workspace so the per-workspace push-scope
        # guardrail permits the follow-up push.
        if workspace_id:
            await _bind_workspace_repo(workspace_id, created_url)
        return _ok("repo_create", {"repo_name": repo_name, "repo_url": created_url, **r})

    elif action == "repo_clone":
        # The sandbox has no `gh` CLI and no need for one. The model's
        # `gh repo clone` almost always means "set up my repository" — which
        # is done by the `gh repo create` step (wires git + GitHub for the
        # workspace). If that step hasn't run yet the workspace isn't a git
        # repo, so steer the model there instead of lying that it's present.
        if not _is_repo:
            return GitExecutionResult(
                status="SUCCESS",
                message=(
                    "Workspace is not yet a git repository. Run "
                    "`gh repo create <name> --private` first (it is intercepted and "
                    "initializes + wires git for you); the workspace then BECOMES "
                    "the repository, so no separate clone is needed."
                ),
                service="git",
                detail={"note": "not_a_git_repo", "action": "repo_clone"},
            )
        remote_url = await _get_remote_url("origin", cwd=workspace_path)
        if remote_url:
            return GitExecutionResult(
                status="SUCCESS",
                message=(
                    "Repository already present: the workspace is already a git "
                    f"repository with origin = {remote_url}. No clone needed — "
                    "write files with WorkspaceFileWriteRequest and commit/push via "
                    "GitOperationRequest."
                ),
                service="git",
                detail={"remote_url": remote_url, "note": "clone-noop"},
            )
        return GitExecutionResult(
            status="SUCCESS",
            message=(
                "Workspace is ready. No `gh repo clone` needed — the workspace is "
                "already a git repository bound to its GitHub remote. Write files "
                "and commit/push via GitOperationRequest."
            ),
            service="git",
            detail={"note": "clone-noop"},
        )

    elif action == "gh_noop":
        # Intercepted `gh` command that has no safe git-tool equivalent
        # (e.g. `gh repo view`, `gh auth status`, `gh pr`, `gh api`). The
        # sandbox has no `gh` CLI, so this keeps the loop from stalling on a
        # missing binary while steering the model back to the proper tools.
        return GitExecutionResult(
            status="SUCCESS",
            message=(
                "The `gh` CLI is not available in the sandbox; this command was "
                "intercepted. The workspace is already a git repository bound to "
                "its GitHub remote. Use GitOperationRequest for git operations "
                "(status, add, commit, push, repo_create) and "
                "WorkspaceFileWriteRequest to write files."
            ),
            service="git",
            detail={"intercepted_gh": getattr(req, "gh_command", None)},
        )

    else:
        return GitExecutionResult(status="FAILURE", message=f"Unknown git action '{action}'. Valid: status, diff, add, commit, pull, push, log, repo_create, repo_clone, gh_noop.", service="git", detail={})
