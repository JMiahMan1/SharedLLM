"""Git operations for the Workspace IDE.

Every git command runs INSIDE the workspace's dedicated sandbox container
(see ``services.workspace_sandbox``), so the IDE's git/commit/push operations
are confined to that workspace's directory, a non-root user, a private network,
and resource limits — exactly like the agent loop's shell commands.

This module is imported by ``main.py`` at the bottom of the file (after all
shared helpers are defined) to avoid a circular import. It exposes ``git_router``
for the REST endpoints and the async ``*`` functions used internally by the
``/workflow/write-sync-commit`` orchestration endpoint.
"""
from __future__ import annotations

import datetime
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import JSONResponse

# Shared helpers / models live in main.py. Importing them here is safe because
# main.py imports this router only at the very bottom of the file, by which
# point every name below already exists on the (partially initialized) module.
from services.workspace_runtime.main import (
    DiffRequest,
    GitAddRequest,
    GitBranchCreateRequest,
    GitCheckoutRequest,
    GitCommitRequest,
    GitFetchRequest,
    GitLogRequest,
    GitPullRequest,
    GitPushRequest,
    GitRebaseRequest,
    GitRemoteRequest,
    GitRevertRequest,
    GitShowRequest,
    GitStashRequest,
    Session,
    Workspace,
    WorkspaceRef,
    _derive_git_author,
    _git_https_credentials,
    _is_protected_branch,
    _require_internal_secret,
    _require_workspace_capability,
    _resolve_workspace,
    _sanitize_targets,
    _slugify_branch_component,
    _trigger_nextcloud_sync,
    _validate_branch_name,
    engine,
)
from services.workspace_sandbox import run_workspace_cmd

log = logging.getLogger("workspace_runtime.git")

git_router = APIRouter()


# ── Sandbox-backed git command runner ────────────────────────────────────────
async def run_git(
    workspace_id: str,
    workspace_path: str | Path,
    args: list[str],
    timeout_seconds: int = 30,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a git (or git-adjacent) command inside the workspace sandbox."""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return await run_workspace_cmd(
        str(workspace_id),
        str(workspace_path),
        args,
        cwd=str(workspace_path),
        timeout=float(timeout_seconds),
        shell=False,
        env=env,
    )


async def run_git_with_optional_askpass(
    workspace_id: str,
    workspace_path: str | Path,
    args: list[str],
    identity: dict[str, Any],
    remote_url: str,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Run a git command with credentials injected via an inline credential
    helper (env-based, no temp file) so it works inside the sandbox container.
    """
    credentials = _git_https_credentials(identity, remote_url)
    if not credentials:
        return await run_git(workspace_id, workspace_path, args, timeout_seconds)
    username, password = credentials
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        # Inline helper avoids writing a temp askpass script that would not exist
        # inside the sandbox container.
        "GIT_CONFIG_VALUE_0": f"!f() {{ echo username={username}; echo password={password}; }}; f",
    }
    return await run_git(workspace_id, workspace_path, args, timeout_seconds, env_overrides=env)


async def current_branch_name(workspace_id: str, workspace_path: str | Path) -> str:
    res = await run_git(workspace_id, workspace_path, ["git", "branch", "--show-current"])
    return res["stdout"].strip()


async def git_remote_url(workspace_id: str, workspace_path: str | Path, remote_name: str) -> str:
    log.info(f"Resolving git remote URL for '{remote_name}' in {workspace_path}")
    result = await run_git(workspace_id, workspace_path, ["git", "config", "--get", f"remote.{remote_name}.url"])
    if result["returncode"] != 0:
        log.warning(f"Git remote lookup failed for '{remote_name}': {result['stderr']}")
        raise HTTPException(status_code=400, detail=f"Git remote '{remote_name}' is not configured")
    remote_url = result["stdout"].strip()
    if not remote_url:
        raise HTTPException(status_code=400, detail=f"Git remote '{remote_name}' is empty")
    return remote_url


async def create_review_branch(
    workspace_id: str,
    workspace_path: str | Path,
    identity: dict[str, Any],
    workspace: dict[str, Any],
    relative_path: str,
    prefix: str,
) -> str:
    base_branch = str(workspace.get("default_branch") or "main").strip() or "main"
    base_ref = _validate_branch_name(base_branch)
    prefix_clean = _slugify_branch_component(prefix)
    user_fragment = _slugify_branch_component(identity.get("user") or "raven")
    file_fragment = _slugify_branch_component(Path(relative_path).stem or "change")
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    branch_name = _validate_branch_name(f"{prefix_clean}/{user_fragment}/{file_fragment}-{timestamp}")

    checkout_base = await run_git(workspace_id, workspace_path, ["git", "checkout", base_ref])
    if checkout_base["returncode"] != 0:
        raise HTTPException(
            status_code=400,
            detail=checkout_base["stderr"].strip() or checkout_base["stdout"].strip() or f"Unable to checkout base branch {base_ref}",
        )

    create_branch = await run_git(workspace_id, workspace_path, ["git", "checkout", "-b", branch_name, base_ref])
    if create_branch["returncode"] != 0:
        raise HTTPException(
            status_code=400,
            detail=create_branch["stderr"].strip() or create_branch["stdout"].strip() or f"Unable to create review branch {branch_name}",
        )
    return branch_name


# ── Endpoints ────────────────────────────────────────────────────────────────
@git_router.post("/git/status")
async def git_status(req: WorkspaceRef, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_status")
    workspace_path = Path(workspace["resolved_path"])
    ws_id = workspace["id"]
    branch = await run_git(ws_id, workspace_path, ["git", "branch", "--show-current"])
    porcelain = await run_git(ws_id, workspace_path, ["git", "status", "--short"])
    upstream = await run_git(ws_id, workspace_path, ["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "branch": branch["stdout"].strip(),
        "upstream": upstream["stdout"].strip() if upstream["returncode"] == 0 else None,
        "porcelain": porcelain["stdout"].splitlines(),
        "dirty": bool(porcelain["stdout"].strip()),
    }


@git_router.post("/git/branches")
async def git_branches(req: WorkspaceRef, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_status")
    workspace_path = Path(workspace["resolved_path"])
    ws_id = workspace["id"]
    local_res = await run_git(ws_id, workspace_path, ["git", "branch", "--format=%(refname:short)"])
    current_res = await run_git(ws_id, workspace_path, ["git", "branch", "--show-current"])
    remote_res = await run_git(ws_id, workspace_path, ["git", "branch", "-r", "--format=%(refname:short)"])
    local = [b.strip() for b in local_res["stdout"].splitlines() if b.strip()]
    remote = [
        b.strip()
        for b in remote_res["stdout"].splitlines()
        if b.strip() and "HEAD" not in b and " -> " not in b
    ]
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "current": current_res["stdout"].strip(),
        "local": local,
        "remote": remote,
    }


@git_router.post("/git/diff")
async def git_diff(req: DiffRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_diff")
    workspace_path = Path(workspace["resolved_path"])
    pathspecs = _sanitize_targets(req.pathspecs)
    args = ["git", "diff", req.ref, "--", *pathspecs] if pathspecs else ["git", "diff", req.ref]
    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or "git diff failed")
    return {"status": "SUCCESS", "workspace": workspace, "diff": result["stdout"]}


@git_router.post("/git/add")
async def git_add(req: GitAddRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    pathspecs = _sanitize_targets(req.pathspecs)
    args = ["git", "add", "--", *pathspecs] if pathspecs else ["git", "add", "-A"]
    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or "git add failed")
    status_result = await run_git(workspace["id"], workspace_path, ["git", "status", "--short"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "porcelain": status_result["stdout"].splitlines(),
    }


@git_router.post("/git/commit")
async def git_commit(req: GitCommitRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    ws_id = workspace["id"]
    identity = workspace.get("resolved_identity") or {}
    if req.pathspecs:
        add_req = GitAddRequest(
            workspace_id=req.workspace_id,
            local_path=req.local_path,
            rag_user=req.rag_user,
            voice_id=req.voice_id,
            device_id=req.device_id,
            pathspecs=req.pathspecs,
        )
        await git_add(add_req, x_internal_secret)

    author_name, author_email = _derive_git_author(identity, req.author_name, req.author_email)
    args = ["git", "commit", "-m", req.message]
    if req.allow_empty:
        args.append("--allow-empty")
    result = await run_git(
        ws_id,
        workspace_path,
        args,
        env_overrides={
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        },
    )
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git commit failed")
    commit_ref = await run_git(ws_id, workspace_path, ["git", "rev-parse", "HEAD"])
    commit_sha = commit_ref["stdout"].strip() if commit_ref["returncode"] == 0 else None
    if not commit_sha:
        raise HTTPException(
            status_code=500,
            detail=f"Commit verification failed: 'git commit' reported success but no HEAD commit could be read back ({commit_ref.get('stderr', '').strip()}).",
        )
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "commit": commit_sha,
        "author_name": author_name,
        "author_email": author_email,
    }


@git_router.post("/git/branch/create")
async def git_branch_create(req: GitBranchCreateRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    branch_name = _validate_branch_name(req.branch_name)
    if req.checkout:
        args = ["git", "checkout", "-b", branch_name]
        if req.from_ref:
            args.append(req.from_ref)
    else:
        args = ["git", "branch", branch_name]
        if req.from_ref:
            args.append(req.from_ref)
    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git branch create failed")
    current_branch = await run_git(workspace["id"], workspace_path, ["git", "branch", "--show-current"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "branch": branch_name,
        "current_branch": current_branch["stdout"].strip(),
    }


@git_router.post("/git/push")
async def git_push(req: GitPushRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    ws_id = workspace["id"]
    identity = workspace.get("resolved_identity") or {}
    remote_name = (req.remote or workspace.get("git_remote") or "origin").strip()
    branch_name = (req.branch or await current_branch_name(ws_id, workspace_path)).strip()
    if not branch_name:
        raise HTTPException(status_code=400, detail="Unable to determine branch to push")

    if _is_protected_branch(branch_name, identity):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Autonomous push to protected branch '{branch_name}' is blocked by policy. "
                "Push to a review branch and open a Pull Request instead."
            ),
        )
    remote_url = await git_remote_url(ws_id, workspace_path, remote_name)
    args = ["git", "push"]
    if req.set_upstream:
        args.append("-u")
    args.extend([remote_name, branch_name])
    result = await run_git_with_optional_askpass(ws_id, workspace_path, args, identity=identity, remote_url=remote_url)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git push failed")
    # Push verification (Aider-style): confirm the ref actually advanced and no
    # commits remain unpushed. `git push` can exit 0 yet leave commits behind
    # (e.g. one ref rejected while another succeeded, or nothing was committed).
    unpushed = await _count_unpushed(ws_id, workspace_path, remote_name, branch_name)
    # Unknown (-1, e.g. remote-tracking ref not yet fetched) is treated as
    # verified by trusting the successful push exit code, to avoid false PARTIALs
    # on first pushes.
    verified = unpushed == 0 if unpushed >= 0 else True
    upstream = await run_git(ws_id, workspace_path, ["git", "rev-parse", "--abbrev-ref", "@{upstream}"])
    return {
        "status": "SUCCESS",
        "verified": verified,
        "unpushed_count": unpushed if unpushed >= 0 else None,
        "warning": None if verified else f"{unpushed} commit(s) still unpushed after push",
        "workspace": workspace,
        "command": args,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "remote": remote_name,
        "branch": branch_name,
        "upstream": upstream["stdout"].strip() if upstream["returncode"] == 0 else None,
    }


async def _count_unpushed(ws_id: str, workspace_path: Path, remote_name: str, branch_name: str) -> int:
    """Return local commits absent from the remote-tracking ref.

    Negative means the check could not run (remote-tracking ref missing / git
    error) — callers treat that as "unknown", not as a failed push.
    """
    res = await run_git(ws_id, workspace_path, ["git", "rev-list", "--count", f"{remote_name}/{branch_name}..{branch_name}"])
    if res["returncode"] != 0:
        return -1
    try:
        return int((res["stdout"] or "0").strip())
    except ValueError:
        return -1


@git_router.post("/git/fetch")
async def git_fetch(req: GitFetchRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    ws_id = workspace["id"]
    identity = workspace.get("resolved_identity") or {}
    remote_name = (req.remote or workspace.get("git_remote") or "origin").strip()
    remote_url = await git_remote_url(ws_id, workspace_path, remote_name)

    args = ["git", "fetch"]
    if req.prune:
        args.append("--prune")
    args.append(remote_name)

    result = await run_git_with_optional_askpass(ws_id, workspace_path, args, identity=identity, remote_url=remote_url)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git fetch failed")

    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": args,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


@git_router.post("/git/pull")
async def git_pull(req: GitPullRequest, background_tasks: BackgroundTasks, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])
    ws_id = workspace["id"]
    identity = workspace.get("resolved_identity") or {}
    remote_name = (req.remote or workspace.get("git_remote") or "origin").strip()

    cur = await run_git(ws_id, workspace_path, ["git", "branch", "--show-current"])
    current_branch = cur["stdout"].strip()
    branch_name = (
        req.branch
        or current_branch
        or workspace.get("default_branch")
        or "main"
    ).strip()

    if not branch_name:
        raise HTTPException(
            status_code=400,
            detail="Cannot determine which branch to pull (detached HEAD and no default_branch configured).",
        )

    remote_url = await git_remote_url(ws_id, workspace_path, remote_name)

    args = ["git", "pull"]
    if req.rebase:
        args.append("--rebase")
    args.extend([remote_name, branch_name])

    result = await run_git_with_optional_askpass(ws_id, workspace_path, args, identity=identity, remote_url=remote_url)

    recovered = False
    recovery_note = None

    if result["returncode"] != 0:
        stderr = (result["stderr"] or result["stdout"] or "").strip()
        dirty_indicators = (
            "would be overwritten",
            "your local changes would be overwritten",
            "local changes would be overwritten",
            "would be overwritten by merge",
            "commit your changes or stash them",
            "not have locally. this is usually caused by another repository pushing",
            "untracked working tree files",
        )
        if any(ind in stderr.lower() for ind in dirty_indicators):
            stash_result = await run_git_with_optional_askpass(
                ws_id, workspace_path, ["git", "stash", "push", "-u", "-m", "sharedllm-auto-pull"], identity=identity, remote_url=remote_url
            )
            if stash_result["returncode"] == 0:
                result = await run_git_with_optional_askpass(ws_id, workspace_path, args, identity=identity, remote_url=remote_url)
                if result["returncode"] == 0:
                    recovered = True
                    recovery_note = (
                        "Local uncommitted changes were stashed to allow the pull. "
                        "Reapply them with: git stash pop"
                    )
                else:
                    await run_git_with_optional_askpass(ws_id, workspace_path, ["git", "stash", "pop"], identity=identity, remote_url=remote_url)
                    raise HTTPException(
                        status_code=400,
                        detail=(result["stderr"] or result["stdout"] or "").strip() or "git pull failed after stashing local changes",
                    )
            else:
                raise HTTPException(status_code=400, detail=stderr or "git pull failed and automatic stash failed")
        else:
            raise HTTPException(status_code=400, detail=stderr or "git pull failed")

    if result["returncode"] == 0:
        with Session(engine) as session:
            match = session.get(Workspace, workspace["id"])
            if match and match.auto_backup_enabled and match.nextcloud_path:
                background_tasks.add_task(
                    _trigger_nextcloud_sync,
                    match.id,
                    match.owner_user or "default",
                    str(workspace_path),
                    match.nextcloud_path,
                )

        return {
            "status": "SUCCESS",
            "workspace": workspace,
            "command": args,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "branch": branch_name,
            "recovered": recovered,
            "recovery_note": recovery_note,
        }

    raise HTTPException(status_code=400, detail=(result["stderr"] or result["stdout"] or "").strip() or "git pull failed")


@git_router.post("/git/revert")
async def git_revert(req: GitRevertRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace_data = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace_data, "git_write")
    path = Path(workspace_data["resolved_path"])
    ws_id = workspace_data["id"]

    if req.hard:
        result = await run_git(ws_id, path, ["git", "reset", "--hard", "HEAD~1"])
        if result["returncode"] != 0:
            return JSONResponse(status_code=400, content={"status": "ERROR", "message": result["stderr"] or result["stdout"]})
    elif req.commit:
        result = await run_git(ws_id, path, ["git", "revert", "--no-edit", req.commit])
        if result["returncode"] != 0:
            return JSONResponse(status_code=400, content={"status": "ERROR", "message": result["stderr"] or result["stdout"]})
    else:
        result = await run_git(ws_id, path, ["git", "revert", "--no-edit", "HEAD"])
        if result["returncode"] != 0:
            return JSONResponse(status_code=400, content={"status": "ERROR", "message": result["stderr"] or result["stdout"]})

    with Session(engine) as session:
        ws = session.get(Workspace, ws_id)
        if ws:
            ws.quarantined = False
            session.add(ws)
            session.commit()

    return {"status": "SUCCESS", "message": "Git revert completed and quarantine lifted."}


@git_router.post("/git/log")
async def git_log(req: GitLogRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_status")
    workspace_path = Path(workspace["resolved_path"])

    args = ["git", "log", f"-{req.max_count}"]
    if req.oneline:
        args.append("--oneline")
    else:
        args.extend(["--pretty=format:%H %an <%ae> %ai %s"])
    if req.ref:
        args.append(req.ref)
    if req.file_path:
        args.extend(["--", req.file_path])

    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or "git log failed")

    entries = []
    for line in result["stdout"].strip().splitlines():
        if not line.strip():
            continue
        if req.oneline:
            parts = line.split(" ", 1)
            entries.append({"commit": parts[0], "message": parts[1] if len(parts) > 1 else ""})
        else:
            parts = line.split(" ", 3)
            if len(parts) >= 4:
                entries.append({"commit": parts[0], "author": parts[1], "date": parts[2], "message": parts[3]})

    return {"status": "SUCCESS", "workspace": workspace, "count": len(entries), "entries": entries}


@git_router.post("/git/checkout")
async def git_checkout(req: GitCheckoutRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])

    branch_name = _validate_branch_name(req.branch)

    if req.create:
        args = ["git", "checkout", "-b", branch_name]
        if req.from_ref:
            args.append(req.from_ref)
    else:
        args = ["git", "checkout", branch_name]

    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git checkout failed")

    current_branch = await run_git(workspace["id"], workspace_path, ["git", "branch", "--show-current"])
    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": result["args"],
        "branch": branch_name,
        "current_branch": current_branch["stdout"].strip(),
    }


@git_router.post("/git/stash")
async def git_stash(req: GitStashRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])

    if req.action == "save":
        args = ["git", "stash", "push"]
        if req.message:
            args.extend(["-m", req.message])
    elif req.action == "pop":
        args = ["git", "stash", "pop"]
        if req.stash_index > 0:
            args.append(f"stash@{{{req.stash_index}}}")
    elif req.action == "apply":
        args = ["git", "stash", "apply"]
        if req.stash_index > 0:
            args.append(f"stash@{{{req.stash_index}}}")
    elif req.action == "list":
        args = ["git", "stash", "list"]
    elif req.action == "drop":
        args = ["git", "stash", "drop"]
        if req.stash_index > 0:
            args.append(f"stash@{{{req.stash_index}}}")
    elif req.action == "clear":
        args = ["git", "stash", "clear"]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown stash action: {req.action}")

    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or f"git stash {req.action} failed")

    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "action": req.action,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


@git_router.post("/git/remote")
async def git_remote(req: GitRemoteRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_status")
    workspace_path = Path(workspace["resolved_path"])

    if req.action == "list":
        args = ["git", "remote", "-v"]
    elif req.action == "add":
        if not req.name or not req.url:
            raise HTTPException(status_code=400, detail="name and url are required for add action")
        args = ["git", "remote", "add", req.name, req.url]
    elif req.action == "remove":
        if not req.name:
            raise HTTPException(status_code=400, detail="name is required for remove action")
        args = ["git", "remote", "remove", req.name]
    elif req.action == "set_url":
        if not req.name or not req.url:
            raise HTTPException(status_code=400, detail="name and url are required for set_url action")
        args = ["git", "remote", "set-url", req.name, req.url]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown remote action: {req.action}")

    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or f"git remote {req.action} failed")

    remotes = []
    if req.action == "list":
        current_name = None
        for line in result["stdout"].strip().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                name, url, direction = parts[0], parts[1], parts[2]
                if name != current_name:
                    remotes.append({"name": name, "fetch": None, "push": None})
                    current_name = name
                if direction == "(fetch)":
                    remotes[-1]["fetch"] = url
                elif direction == "(push)":
                    remotes[-1]["push"] = url

    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "action": req.action,
        "remotes": remotes,
        "stdout": result["stdout"],
    }


@git_router.post("/git/show")
async def git_show(req: GitShowRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_status")
    workspace_path = Path(workspace["resolved_path"])

    args = ["git", "show", f"{req.ref}:{req.file_path}"] if req.file_path else ["git", "show", "--stat", req.ref]

    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git show failed")

    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "ref": req.ref,
        "file_path": req.file_path,
        "content": result["stdout"],
    }


@git_router.post("/git/rebase")
async def git_rebase(req: GitRebaseRequest, x_internal_secret: str | None = Header(default=None)):
    _require_internal_secret(x_internal_secret)
    workspace = _resolve_workspace(req, check_recovery=True)
    _require_workspace_capability(workspace, "git_write")
    workspace_path = Path(workspace["resolved_path"])

    args = ["git", "rebase", req.upstream]
    if req.branch:
        args.append(req.branch)

    result = await run_git(workspace["id"], workspace_path, args)
    if result["returncode"] != 0:
        raise HTTPException(status_code=400, detail=result["stderr"].strip() or result["stdout"].strip() or "git rebase failed")

    return {
        "status": "SUCCESS",
        "workspace": workspace,
        "command": args,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }
