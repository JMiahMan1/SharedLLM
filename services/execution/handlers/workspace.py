# services/execution/handlers/workspace.py
import asyncio
import difflib
import logging
import os
import re
import shlex
import traceback

from fastapi import HTTPException

from services.config import INTERNAL_SECRET, WORKSPACE_ROOT, WORKSPACE_RUNTIME_SVC_URL
from services.execution.schemas import (
    ExecutionResult,
    WorkspaceFilePatchRequest,
    WorkspaceFileReadRequest,
    WorkspaceFileWriteRequest,
    WorkspaceSearchRequest,
    WorkspaceShellRequest,
)

log = logging.getLogger("execution.workspace")
READ_ONLY_SHELL_COMMANDS = {
    # File reading / listing
    "cat", "find", "head", "ls", "pwd", "rg", "sed", "tail", "wc", "grep", "du", "stat", "file",
    # System info
    "uptime", "whoami", "id", "hostname", "date", "echo", "printenv",
    # Text processing
    "sort", "uniq", "tr", "cut", "awk", "xxd",
    # Shell builtins
    "env", "true", "false", "yes", "seq", "printf",
    # Location/lookup
    "which", "whereis", "type", "command",
    # Math / checksum
    "bc", "expr", "md5sum", "sha256sum",
    # Disk / process info
    "df", "free", "uname", "ps", "top", "dmesg",
}
CODE_EDITING_SHELL_COMMANDS = {
    # File creation / manipulation (agents need these for code editing)
    "touch", "mkdir", "rm", "mv", "cp", "tee", "cat",
    # Permission / ownership
    "chmod", "chown",
    # Script execution
    "bash", "sh", "zsh",
    # External content
    "curl", "wget", "pip", "npm", "apt", "apt-get", "yum", "dnf", "pacman",
    # Additional file utilities
    "ln", "xargs",
}
VERIFICATION_SHELL_COMMANDS = {
    "black", "eslint", "flake8", "pytest", "python", "python3", "git", "gh"
}
# Truly dangerous system-level commands — never allowed regardless of context
SYSTEM_BLOCKLIST_COMMANDS = {
    "sudo", "su", "dd", "mkfs", "mount", "umount",
    "reboot", "shutdown", "poweroff", "halt",
    "insmod", "modprobe", "rmmod", "modinfo",
    "iptables", "ip6tables", "ufw", "firewall-cmd",
    "fdisk", "parted", "losetup",
}


async def _bind_workspace_repo(workspace_id: str | None, repo_url: str | None) -> None:
    """Best-effort: bind a repo_url to an (initially unbound) workspace after a
    successful first push, so the per-workspace push-scope guardrail becomes
    effective for subsequent pushes. Supports the create-repo-then-push flow
    where the workspace has no designated repo until the first push succeeds.
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
        log.debug(f"[bind repo] failed for {workspace_id}: {e}")

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="workspace", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="workspace", detail=detail)

async def _resolve_workspace_info(workspace_id: str | None, user_context: dict | None = None) -> tuple[str, dict]:
    """Resolves workspace path and details from workspace_runtime service.

    We do NOT silently fall back to ``WORKSPACE_ROOT`` (the Default Workspace)
    when resolution fails or no ``workspace_id`` is supplied — that would route a
    project's file/shell operations into the shared maintenance workspace and
    confuse the agent. We fail loudly instead so a valid ``workspace_id`` is used.
    """
    import aiohttp
    if not workspace_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "No workspace_id provided. Workspace-scoped tools require a valid "
                "workspace_id from WorkspaceCreateRequest; operations are NOT routed "
                "to the Default Workspace."
            ),
        )

    workspace_details: dict = {}
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
                        workspace_details = data.get("workspace", {})
                        resolved_path = str(workspace_details.get("resolved_path") or WORKSPACE_ROOT)
                        return resolved_path, workspace_details
                try:
                    err_detail = (await resp.json()).get("detail", await resp.text())
                except Exception:
                    err_detail = await resp.text()
                raise HTTPException(status_code=resp.status, detail=err_detail)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to resolve workspace '{workspace_id}': {e}",
        )


def _strip_workspace_path_prefix(relative_path: str, workspace: dict | None) -> str:
    """Normalize a model-supplied path that duplicates the workspace path.

    Mirrors the helper in workspace_runtime: if the agent copies ``local_path``
    / ``resolved_path`` (e.g. 'users/default/<id>/main.py') into a ``cwd`` or
    ``relative_path``, strip the redundant prefix so it resolves correctly.
    """
    if not relative_path or not workspace:
        return relative_path
    norm = os.path.normpath(str(relative_path)).replace("\\", "/").strip()
    if norm in (".", "/"):
        return ""
    for key in ("resolved_path", "local_path"):
        base = workspace.get(key)
        if not base:
            continue
        base_norm = os.path.normpath(str(base)).replace("\\", "/").strip()
        if not base_norm or base_norm in (".", "/"):
            continue
        if norm == base_norm:
            return ""
        if norm.startswith(base_norm + "/"):
            return norm[len(base_norm) + 1:].strip("/")
    return relative_path

def _require_capability(workspace: dict, capability: str):
    identity = workspace.get("resolved_identity") or {}
    if identity.get("is_admin"):
        return
    capabilities = workspace.get("capabilities")
    if capabilities is None:
        return
    if capability not in capabilities:
        raise HTTPException(
            status_code=403,
            detail=f"Workspace '{workspace.get('id')}' does not allow capability '{capability}'"
        )

def resolve_safe_path(path: str, workspace_root: str | None = None) -> str:
    """Ensure the path stays within workspace_root."""
    actual_root = workspace_root or WORKSPACE_ROOT or "/workspace"
    workspace_root_abs = os.path.abspath(actual_root)

    # If the path is already an absolute path inside the workspace root (agents often
    # write to the exact resolved_path they were told), use it directly.
    cand = os.path.abspath(path)
    if cand == workspace_root_abs or cand.startswith(workspace_root_abs + os.sep):
        if not cand.startswith(workspace_root_abs):
            raise ValueError(f"Path traversal detected: {path}")
        return cand

    # Otherwise treat the path as relative to the workspace root.
    rel_path = path.lstrip("/")
    # Drop a redundant leading "workspace" (or workspace-root basename) segment that
    # agents sometimes prepend, e.g. "/workspace/game.py" when the real root is
    # "/workspaces/<repo>". This keeps relative paths landing at the workspace root.
    parts = rel_path.split("/")
    if len(parts) > 1 and parts[0] in ("workspace", os.path.basename(actual_root.rstrip("/"))):
        parts = parts[1:]
        rel_path = "/".join(parts)
    abs_path = os.path.join(workspace_root_abs, rel_path)
    if not abs_path.startswith(workspace_root_abs):
        raise ValueError(f"Path traversal detected: {path}")
    return abs_path

async def _run_command_async(
    cmd: list[str] | str,
    cwd: str | None = None,
    timeout: float = 30.0,
    shell: bool = False,
    env: dict | None = None
) -> tuple[int, str, str]:
    """Runs a subprocess asynchronously using asyncio to prevent blocking the event loop."""
    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                cmd if isinstance(cmd, str) else " ".join(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            ret_code = proc.returncode if proc.returncode is not None else 0
            return ret_code, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise TimeoutError(f"Command timed out after {timeout} seconds")
    except FileNotFoundError as e:
        return -1, "", f"Executable or directory not found: {e}"


async def _sandbox_run(
    workspace_id: str | None,
    host_path: str | None,
    cmd: list[str] | str,
    cwd: str | None = None,
    timeout: float = 30.0,
    shell: bool = False,
    env: dict | None = None,
) -> tuple[int, str, str]:
    """Run a workspace command inside its dedicated sandbox container.

    This is the containment boundary: instead of executing on the host (or in
    the shared execution container), the command runs in the per-workspace
    Docker container built by ``services.workspace_sandbox`` — only the
    workspace directory is mounted, it runs as a non-root user on a private
    network with resource limits. Falls back to a plain host subprocess only
    when no resolvable workspace is available (legacy/Default-workspace paths).
    """
    if not workspace_id or not host_path:
        return await _run_command_async(cmd, cwd=cwd, timeout=timeout, shell=shell, env=env)
    from services.workspace_sandbox import run_workspace_cmd

    res = await run_workspace_cmd(workspace_id, host_path, cmd, cwd=cwd, timeout=timeout, shell=shell, env=env)
    return res["returncode"], res["stdout"], res["stderr"]


async def handle_workspace_read(req: WorkspaceFileReadRequest) -> ExecutionResult:
    try:
        workspace_id = getattr(req, "workspace_id", None)
        user_ctx = getattr(req, "user_context", None)
        ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_ctx)
        if ws_details:
            _require_capability(ws_details, "read")

        abs_path = resolve_safe_path(req.path, ws_root)
        if not os.path.exists(abs_path):
            return _fail_with_discovery(req.path, f"File not found: {req.path}")
        if not os.path.isfile(abs_path):
            return _fail(f"Path is not a file: {req.path}")

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # Strategy 3: Semantic Extraction (Signatures Only)
        if req.summary_only:
            ext = os.path.splitext(req.path)[1].lower()
            if ext in (".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"):
                summary = []
                content_str = "".join(lines)

                if ext == ".py":
                    import ast
                    try:
                        tree = ast.parse(content_str)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ClassDef):
                                summary.append(f"Class: {node.name} (line {node.lineno})")
                            elif isinstance(node, ast.FunctionDef):
                                summary.append(f"Function: {node.name}({[a.arg for a in node.args.args]}) (line {node.lineno})")
                    except Exception as e:
                        log.warning(f"AST parse failed for {req.path}: {e}")

                if not summary:
                    lines_with_numbers = list(enumerate(lines, 1))

                    if ext in (".js", ".ts", ".jsx", ".tsx"):
                        func_pat = re.compile(r'(?:export\s+)?(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(([^)]*)\)')
                        arrow_pat = re.compile(r'(?:export\s+)?const\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>')
                        class_pat = re.compile(r'(?:export\s+)?class\s+([a-zA-Z0-9_$]+)')
                        interface_pat = re.compile(r'(?:export\s+)?(?:interface|type)\s+([a-zA-Z0-9_$]+)')

                        for line_num, line in lines_with_numbers:
                            line_strip = line.strip()
                            m = func_pat.match(line_strip)
                            if m:
                                summary.append(f"Function: {m.group(1)}({m.group(2).strip()}) (line {line_num})")
                                continue
                            m = arrow_pat.match(line_strip)
                            if m:
                                summary.append(f"ArrowFunction: {m.group(1)}({m.group(2).strip()}) (line {line_num})")
                                continue
                            m = class_pat.match(line_strip)
                            if m:
                                summary.append(f"Class: {m.group(1)} (line {line_num})")
                                continue
                            m = interface_pat.match(line_strip)
                            if m:
                                summary.append(f"Interface/Type: {m.group(1)} (line {line_num})")

                    elif ext == ".go":
                        func_pat = re.compile(r'^func\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)')
                        method_pat = re.compile(r'^func\s*\(\s*[^)]+\s*\)\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)')
                        type_pat = re.compile(r'^type\s+([a-zA-Z0-9_]+)\s+(struct|interface)')

                        for line_num, line in lines_with_numbers:
                            line_strip = line.strip()
                            m = func_pat.match(line_strip)
                            if m:
                                summary.append(f"Function: {m.group(1)}({m.group(2).strip()}) (line {line_num})")
                                continue
                            m = method_pat.match(line_strip)
                            if m:
                                summary.append(f"Method: {m.group(1)}({m.group(2).strip()}) (line {line_num})")
                                continue
                            m = type_pat.match(line_strip)
                            if m:
                                summary.append(f"Type ({m.group(2)}): {m.group(1)} (line {line_num})")

                    elif ext == ".rs":
                        fn_pat = re.compile(r'^(?:pub\s+)?(?:async\s+)?fn\s+([a-zA-Z0-9_]+)\s*(?:<[^>]*>)?\s*\(([^)]*)\)')
                        type_pat = re.compile(r'^(?:pub\s+)?(?:struct|enum|trait)\s+([a-zA-Z0-9_]+)')

                        for line_num, line in lines_with_numbers:
                            line_strip = line.strip()
                            m = fn_pat.match(line_strip)
                            if m:
                                summary.append(f"Function: {m.group(1)}({m.group(2).strip()}) (line {line_num})")
                                continue
                            m = type_pat.match(line_strip)
                            if m:
                                summary.append(f"Type/Trait: {m.group(1)} (line {line_num})")

                    elif ext == ".py":
                        func_pat = re.compile(r'^def\s+([a-zA-Z0-9_]+)\s*\(([^)]*)\)')
                        class_pat = re.compile(r'^class\s+([a-zA-Z0-9_]+)')
                        for line_num, line in lines_with_numbers:
                            line_strip = line.strip()
                            m = func_pat.match(line_strip)
                            if m:
                                summary.append(f"Function: {m.group(1)}({m.group(2).strip()}) (line {line_num})")
                                continue
                            m = class_pat.match(line_strip)
                            if m:
                                summary.append(f"Class: {m.group(1)} (line {line_num})")

                if summary:
                    content = "\n".join(summary)
                    return _ok(f"Semantic map for {req.path} ({len(summary)} symbols found)", {"content": content, "path": req.path})

        # Chunked Reading (Windowing)
        start = max(0, req.offset_lines - 1) if req.offset_lines > 0 else 0

        # Hardware Protection for 8GB VRAM constraints
        safe_limit = min(req.limit_lines if req.limit_lines and req.limit_lines > 0 else 300, 300)
        end = start + safe_limit

        chunk = lines[start:end]
        content = "".join(chunk)

        msg = f"Read {len(chunk)} lines from {req.path} (offset={req.offset_lines})"
        if end < len(lines):
            msg += f" | TRUNCATED for context safety: file has {len(lines)} lines total."

        return _ok(msg, {"content": content, "path": req.path, "total_lines": len(lines), "start_line": start + 1})
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Workspace read failed: {e}")
        return _fail(str(e))

def _get_discovery_suggestion(path: str) -> str | None:
    """Dynamically discovers similar files in the workspace to help agents self-correct."""
    try:
        filename = os.path.basename(path)
        if not filename:
            return None

        root_dir = WORKSPACE_ROOT or "/workspace"
        matches = []
        for root, _, files in os.walk(root_dir):
            # Skip hidden directories like .git
            if "/.git" in root:
                continue
            for f in files:
                if f.lower() == filename.lower():
                    rel_path = os.path.relpath(os.path.join(root, f), root_dir)
                    matches.append(rel_path)

        if matches:
            # Filter out the exact path if it somehow matched
            matches = [m for m in matches if m != path]
            if matches:
                return f"Did you mean: {', '.join(matches[:3])}?"
    except Exception as e:
        log.warning(f"Discovery suggestion failed: {e}")
    return None

def _fail_with_discovery(path: str, message: str) -> ExecutionResult:
    suggestion = _get_discovery_suggestion(path)
    if suggestion:
        message += f" | {suggestion}"
    return _fail(message)

async def handle_workspace_write(req: WorkspaceFileWriteRequest) -> ExecutionResult:
    try:
        workspace_id = getattr(req, "workspace_id", None)
        user_ctx = getattr(req, "user_context", None)
        ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_ctx)
        if ws_details:
            _require_capability(ws_details, "write")

        abs_path = resolve_safe_path(req.path, ws_root)
        exists = os.path.exists(abs_path)

        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)

        message = f"Successfully wrote to {req.path}."

        # Collision Detection: If creating a NEW file, check if it exists elsewhere
        if not exists:
            suggestion = _get_discovery_suggestion(req.path)
            if suggestion:
                message += f" | WARNING: {suggestion} You may have created a duplicate file in the wrong location."

        return _ok(message, {"path": req.path, "bytes": len(req.content), "created_new": not exists})
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Workspace write failed: {e}")
        return _fail(str(e))

async def handle_workspace_search(req: WorkspaceSearchRequest) -> ExecutionResult:
    """Performs a ripgrep search in the workspace."""
    try:
        workspace_id = getattr(req, "workspace_id", None)
        user_ctx = getattr(req, "user_context", None)
        ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_ctx)
        if ws_details:
            _require_capability(ws_details, "read")

        abs_search_path = resolve_safe_path(req.path, ws_root)

        # Build rg command. Search relative to the resolved path (cwd), not an
        # absolute arg, so it executes identically inside the sandbox container.
        cmd = ["rg", "--json", "-i", "--max-count", "100", req.query, "."]
        if req.include:
            cmd.extend(["-g", req.include])
        if req.exclude:
            cmd.extend(["-g", f"!{req.exclude}"])

        log.info(f"Running search in {abs_search_path}: {' '.join(cmd)}")
        try:
            rc, stdout, stderr = await _sandbox_run(workspace_id, ws_root, cmd, cwd=abs_search_path, timeout=30.0)
            if rc == -1 and "Executable or directory not found" in stderr:
                raise FileNotFoundError()
        except FileNotFoundError:
            log.info("ripgrep (rg) not found, falling back to grep")
            cmd = ["grep", "-rnI", "-e", req.query, "."]
            if req.include:
                cmd = ["grep", "-rnI", "--include", req.include, "-e", req.query, "."]
            rc, stdout, stderr = await _sandbox_run(workspace_id, ws_root, cmd, cwd=abs_search_path, timeout=30.0)
        except TimeoutError:
            return _fail("Search timed out after 30s")

        # Ripgrep returns 1 if no matches found, which isn't a failure in our case
        if rc not in (0, 1):
            return _fail(f"Search failed: {stderr}")

        matches = []
        import json
        for line in stdout.splitlines():
            try:
                # Try parsing as JSON (rg output), otherwise treat as raw grep line
                data = json.loads(line)
                if data.get("type") == "match":
                    match_data = data.get("data", {})
                    matches.append({
                        "path": os.path.relpath(match_data.get("path", {}).get("text", ""), ws_root),
                        "line": match_data.get("line_number"),
                        "text": match_data.get("lines", {}).get("text", "").strip()
                    })
            except Exception:
                # Basic grep fallback parsing
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({
                        "path": os.path.relpath(parts[0], ws_root),
                        "line": parts[1],
                        "text": parts[2].strip()
                    })

        if not matches:
            return _ok(f"No matches found for '{req.query}' in {req.path}", {"matches": []})

        return _ok(f"Found {len(matches)} matches for '{req.query}'", {"matches": matches})
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Workspace search failed: {e}")
        return _fail(str(e))

async def handle_workspace_shell(req: WorkspaceShellRequest) -> ExecutionResult:
    """Executes an arbitrary shell command in the workspace."""
    try:
        workspace_id = getattr(req, "workspace_id", None)
        user_ctx = getattr(req, "user_context", None)
        ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_ctx)

        # Resolve safe CWD
        safe_cwd = req.cwd if hasattr(req, 'cwd') and req.cwd else "."
        # Defensive: if the model copied local_path/resolved_path into cwd, strip it.
        safe_cwd = _strip_workspace_path_prefix(safe_cwd, ws_details) or "."
        abs_cwd = resolve_safe_path(safe_cwd, ws_root)

        # Determine the final command string
        final_cmd = ""
        if req.commands:
            final_cmd = " && ".join(req.commands)
        elif req.command:
            final_cmd = req.command
        else:
            return _fail("Neither 'command' nor 'commands' provided")

        # The command now executes inside the workspace's dedicated sandbox
        # container (see services.workspace_sandbox): only this workspace's
        # directory is mounted, it runs as a non-root user on a private network
        # with CPU/memory/PID limits, so runaway or destructive commands are
        # contained. The SYSTEM_BLOCKLIST below still hard-blocks commands that
        # could escape the sandbox (sudo, reboot, mkfs, iptables, ...).
        parsed = shlex.split(final_cmd)
        if not parsed:
            return _fail("Shell command is empty")

        base_command = parsed[0]
        normalized_command = f"{base_command}-{parsed[1]}" if base_command == "git" and len(parsed) > 1 else base_command

        # Guardrail: hard-block truly dangerous system-level commands regardless
        # of context (e.g. sudo, reboot, mkfs). These can compromise the host and
        # must never be executed from a workspace shell.
        _blocked_token = next((t for t in parsed if t in SYSTEM_BLOCKLIST_COMMANDS), None)
        if _blocked_token is not None:
            return _fail(
                f"Command blocked: '{_blocked_token}' is not permitted in a workspace shell.",
                {"error": "command_blocked", "command": final_cmd, "blocked_token": _blocked_token},
            )

        # Strip a redundant leading "cd <dir> &&" — the command already executes with
        # its cwd at the workspace root, and agents often prepend "cd /workspace".
        _cd = re.match(r"^\s*cd\s+(\S+)(?:\s*(?:&&|;)\s*)?", final_cmd)
        if _cd and _cd.group(1) in ("/workspace", ws_root, os.path.dirname(ws_root)):
            final_cmd = final_cmd[_cd.end():].lstrip()
            parsed = shlex.split(final_cmd)
            if parsed:
                base_command = parsed[0]

        # Enforce capability constraints on the workspace
        if ws_details:
            # Determine required capability based on command type
            if base_command in {"python", "python3"} and parsed[1:3] == ["-m", "pytest"]:
                required_cap = "pytest"
            elif base_command == "git":
                required_cap = "git_write" if len(parsed) > 1 and parsed[1] not in {"status", "diff", "log", "show"} else "git_status"
            elif normalized_command in CODE_EDITING_SHELL_COMMANDS and normalized_command not in READ_ONLY_SHELL_COMMANDS:
                # Only require write for commands that are exclusively in CODE_EDITING list
                required_cap = "write"
            else:
                # All other allowed commands (including read-only and overlapping commands like cat)
                required_cap = "read"
            _require_capability(ws_details, required_cap)

        # Resolve GitHub auth for this command. Prefer an explicit per-user token
        # from the user context; fall back to the runtime-injected container token
        # (GITHUB_TOKEN/GH_TOKEN sourced from .env) so `gh`/`git` are authenticated
        # even when the agent's user_context does not carry a github_token (the
        # common case). Without this fallback, raw `gh`/`git push` shells are
        # unauthenticated and every push is hard-blocked with auth_required.
        def _resolve_gh_tok() -> str | None:
            tok = None
            if isinstance(user_ctx, dict):
                tok = user_ctx.get("github_token")
            elif user_ctx is not None:
                tok = getattr(user_ctx, "github_token", None)
            if not tok:
                tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            return tok

        # Proactive auth check: git push requires an authenticated remote.
        if base_command == "git" and len(parsed) > 1 and parsed[1] == "push":
            gh_tok_check = _resolve_gh_tok()
            if not gh_tok_check:
                return _fail(
                    "Git push requires authentication, but no GitHub/Git token is present. "
                    "Connect a GitHub account (Settings) or provide GITHUB_TOKEN so Raven can push.",
                    {"error": "auth_required", "command": final_cmd},
                )

        # Guardrail: a workspace may ONLY push to its OWN designated repository
        # (its repo_url, or a repo Raven created via `gh repo create`). There is
        # no hardcoded allow/deny list; any push to a different repo (e.g.
        # SharedLLM from a throwaway test workspace) is refused. We gate `git
        # push` only — configuring a remote is allowed, and the push itself is
        # what must be protected (this also avoids order-dependence on
        # create-then-push vs push-then-create).
        if base_command == "git" and len(parsed) > 1 and parsed[1] == "push":
            from .git import push_allowed
            _target_url = None
            _url_m = re.search(r"git\s+push\b.*?\b(https?://\S+|\S+@\S+:\S+|\S+\.git)\b", final_cmd)
            if _url_m:
                _target_url = _url_m.group(1)
            if _target_url is None:
                try:
                    _rc, _out, _err = await _sandbox_run(
                        workspace_id, ws_root, ["git", "remote", "get-url", "origin"], cwd=abs_cwd, timeout=10.0
                    )
                    if _rc == 0 and _out.strip():
                        _target_url = _out.strip()
                except Exception:
                    _target_url = None
            if _target_url is not None:
                _ws_repo_url = (ws_details or {}).get("repo_url")
                _allowed, _reason = push_allowed(_ws_repo_url, _target_url)
                if not _allowed:
                    return _fail(
                        _reason,
                        {"error": "repo_push_scope_blocked", "command": final_cmd},
                    )

        log.info(f"Executing shell command: {final_cmd} in {abs_cwd}")
        # Enforce a max timeout. Builds (cargo build, npm install, go build),
        # CI-equivalent runs, and dependency fetches routinely exceed 300s, so the
        # ceiling is raised to 1800s. The schema also permits requesting up to
        # 1800s; anything higher is clamped here for safety.
        safe_timeout = min(req.timeout or 600, 1800)

        # Inject GitHub auth into the shell environment so Raven can manage repos
        # and use token-aware tooling without a pre-seeded credential store. This
        # mirrors services/execution/handlers/gh.py, but ALSO sets GITHUB_TOKEN
        # (which PyGithub reads via os.environ['GITHUB_TOKEN']) and GIT_TOKEN, and
        # applies to python scripts — models frequently shell out to
        # `python3 <<'PYEOF' ... PyGithub ...` to drive git, and that path dies
        # with KeyError('GITHUB_TOKEN') unless the env var is present.
        cmd_env = os.environ.copy()
        if base_command in ("gh", "git", "python", "python3"):
            gh_tok = _resolve_gh_tok()
            if gh_tok:
                cmd_env["GITHUB_TOKEN"] = gh_tok
                cmd_env["GH_TOKEN"] = gh_tok
                cmd_env["GH_ENTERPRISE_TOKEN"] = gh_tok
                cmd_env["GIT_TOKEN"] = gh_tok
                cmd_env["GH_PROMPT_DISABLED"] = "1"
                if base_command == "git":
                    cmd_env["GIT_TERMINAL_PROMPT"] = "0"
                    cmd_env["GIT_CONFIG_COUNT"] = "1"
                    cmd_env["GIT_CONFIG_KEY_0"] = "credential.helper"
                    cmd_env["GIT_CONFIG_VALUE_0"] = (
                        f"!f() {{ echo username=x-access-token; echo password={gh_tok}; }}; f"
                    )

        try:
            rc, stdout, stderr = await _sandbox_run(
                workspace_id, ws_root, final_cmd, cwd=abs_cwd, timeout=safe_timeout, shell=True, env=cmd_env
            )
        except TimeoutError:
            tb_str = traceback.format_exc()
            log.error(f"[WORKSPACE SHELL TIMEOUT] Command: {final_cmd}\n{tb_str}")
            detail = {
                "command": final_cmd,
                "cwd": abs_cwd,
                "timeout": safe_timeout,
                "error_type": "TimeoutError",
                "traceback": tb_str,
                "stdout": "",
                "stderr": f"Command timed out after {safe_timeout}s",
            }
            return _fail(f"Command timed out after {safe_timeout}s", detail)

        detail = {
            "command": final_cmd,
            "cwd": abs_cwd,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": rc
        }

        if rc == 0:
            # After a successful push from a workspace that had no designated
            # repo, bind the pushed URL so the per-workspace push-scope guardrail
            # becomes effective for future pushes (create-then-push flow).
            if base_command == "git" and len(parsed) > 1 and parsed[1] == "push" and _target_url:
                try:
                    await _bind_workspace_repo(workspace_id, _target_url)
                except Exception:
                    log.debug("best-effort repo_url bind after push failed", exc_info=True)
            cmd_prefix = req.command[:50] if req.command else "<unknown>"
            return _ok(f"Command executed successfully: {cmd_prefix}...", detail)
        else:
            log.error(f"[WORKSPACE SHELL FAIL] command='{final_cmd}' rc={rc} cwd={abs_cwd}\nstdout: {stdout}\nstderr: {stderr}")
            return _fail(f"Command failed with exit code {rc}", detail)

    except HTTPException:
        raise
    except Exception as e:
        tb_str = traceback.format_exc()
        log.error(f"[WORKSPACE SHELL ERROR] command='{final_cmd if 'final_cmd' in dir() else '<unknown>'}'\n{tb_str}")
        detail = {
            "command": final_cmd if 'final_cmd' in dir() else "<unknown>",
            "cwd": abs_cwd if 'abs_cwd' in dir() else "<unknown>",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": tb_str,
        }
        return _fail(f"Workspace shell execution failed: {e}", detail)

async def handle_workspace_patch(req: WorkspaceFilePatchRequest) -> ExecutionResult:
    try:
        workspace_id = getattr(req, "workspace_id", None)
        user_ctx = getattr(req, "user_context", None)
        ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_ctx)
        if ws_details:
            _require_capability(ws_details, "write")

        abs_path = resolve_safe_path(req.path, ws_root)
        if not os.path.exists(abs_path):
            return _fail_with_discovery(req.path, f"File not found for patching: {req.path}")

        with open(abs_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        applied_count = 0
        failed_chunks = []

        for chunk in req.chunks:
            lines = content.splitlines(keepends=True)
            old_lines = chunk.old_text.splitlines(keepends=True)

            # Normalize for matching
            old_norm = [line.strip() for line in old_lines if line.strip()]

            best_match = None
            highest_ratio = 0.0

            # Scan file for best matching block of code
            for i in range(len(lines) - len(old_norm) + 1):
                candidate = [line.strip() for line in lines[i:i+len(old_lines)] if line.strip()]
                ratio = difflib.SequenceMatcher(None, old_norm, candidate[:len(old_norm)]).ratio()

                if ratio > highest_ratio:
                    highest_ratio = ratio
                    best_match = (i, i + len(old_lines))

            if highest_ratio > 0.85 and best_match:
                start, end = best_match
                log.info(f"Fuzzy patch match found with ratio {highest_ratio:.2f} at lines {start}-{end}")
                # Reconstruct file with new chunk swapped in
                new_lines = lines[:start] + chunk.new_text.splitlines(keepends=True)
                if not new_lines[-1].endswith("\n"):
                    new_lines[-1] += "\n"
                new_lines += lines[end:]

                content = "".join(new_lines)
                applied_count += 1
            else:
                failed_chunks.append(chunk.old_text)

        if applied_count == 0:
            return _fail(f"Patch failed: No chunks matched the target file {req.path}")

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        message = f"Applied {applied_count}/{len(req.chunks)} patches to {req.path}."

        if failed_chunks:
            message += f" Failed to match {len(failed_chunks)} chunks."

        if req.commit_after:
            message += " (Commit pending)"

        return _ok(message, {"path": req.path, "applied": applied_count, "failed": len(failed_chunks)})
    except HTTPException:
        raise
    except Exception as e:
        tb_str = traceback.format_exc()
        log.error(f"[WORKSPACE PATCH ERROR] path='{getattr(req, 'path', '<unknown>')}'\n{tb_str}")
        detail = {
            "path": getattr(req, "path", "<unknown>"),
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": tb_str,
        }
        return _fail(f"Workspace patch failed: {e}", detail)


async def handle_workspace_lint(req) -> ExecutionResult:
    """Auto-detect and run the appropriate linter for the given file."""
    try:
        workspace_id = getattr(req, "workspace_id", None)
        user_ctx = getattr(req, "user_context", None)
        ws_root = None
        ws_details = None
        if workspace_id:
            try:
                ws_root, ws_details = await _resolve_workspace_info(workspace_id, user_ctx)
            except HTTPException:
                ws_root = None
        if not ws_root:
            ws_root = WORKSPACE_ROOT
        else:
            if ws_details:
                _require_capability(ws_details, "read")

        abs_path = resolve_safe_path(req.path, ws_root)
        if not os.path.exists(abs_path):
            return _fail(f"File not found: {req.path}")

        ext = os.path.splitext(req.path)[1].lower()
        forced = (req.linter or "").strip().lower()
        results = []
        passed = True
        verified = False  # becomes True once any real checker actually runs

        async def _run(cmd):
            try:
                rc, stdout, stderr = await _sandbox_run(workspace_id, ws_root, cmd, cwd=ws_root, timeout=30.0)
                if rc == -1 and "Executable or directory not found" in stderr:
                    raise FileNotFoundError()
                return rc, stdout.strip(), stderr.strip()
            except TimeoutError:
                return -2, "", "Timeout expired after 30s"
            except FileNotFoundError:
                return -1, "", f"Tool not found: {cmd[0]}"

        async def _lint_step(tool, args, label=None):
            """Run one linter/compiler. Returns True if the tool actually ran.

            A MISSING tool is recorded as skipped (not a code failure) so the
            caller can see verification never actually happened. Previously a
            missing binary was silently dropped, which let the gate report
            "clean" for code that was never checked at all — wrong information
            that let error-ridden code pass."""
            rc, out, err = await _run([tool, *args, str(abs_path)])
            if rc == -1 and "Tool not found" in err:
                results.append({"tool": label or tool, "returncode": None, "skipped": True, "output": f"{tool} not installed in sandbox"})
                return False
            nonlocal verified
            verified = True
            results.append({"tool": label or tool, "returncode": rc, "output": out or err})
            if rc != 0:
                nonlocal passed
                passed = False
            return True

        # ── Python ───────────────────────────────────────────────────────────
        if ext == ".py" or forced in ("ruff", "black", "flake8", "python"):
            # Syntax check first — catches malformed files (missing imports, broken syntax)
            rc, out, err = await _run(["python3", "-m", "py_compile", str(abs_path)])
            if rc == -1:
                return _ok("Python compiler not available — skipping lint.", {"path": req.path, "skipped": True})
            verified = True
            results.append({"tool": "py_compile", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False
            else:
                # Ruff: fast modern linter + formatter (replaces flake8, black, isort)
                await _lint_step("ruff", ["check"])
                # Pyflakes as a second opinion for undefined names (F821/F405)
                await _lint_step("python3", ["-m", "pyflakes"])

        # ── JavaScript / TypeScript ───────────────────────────────────────────
        elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs") or forced == "eslint":
            fix_flag = ["--fix"] if req.fix else []
            had_eslint = await _lint_step("eslint", fix_flag)
            # eslint may be absent in older sandboxes — at minimum syntax-check
            # JS with node so parse errors are still caught (no silent skip).
            if not had_eslint and ext in (".js", ".jsx", ".mjs"):
                await _lint_step("node", ["--check", str(abs_path)], label="node --check")
            if ext in (".ts", ".tsx") and not req.fix:
                # tsc type-check (no emit) catches undefined names in TS too
                await _lint_step("tsc", ["--noEmit", "--skipLibCheck"])

        # ── Shell ─────────────────────────────────────────────────────────────
        elif ext in (".sh", ".bash") or forced == "shellcheck":
            await _lint_step("shellcheck", ["-S", "error"])

        # ── Go ────────────────────────────────────────────────────────────────
        elif ext == ".go" or forced == "go":
            # gofmt -l prints the file path when it is NOT gofmt-clean (exits 0
            # either way) => treat non-empty output as a finding, not a tool miss.
            rc, out, err = await _run(["gofmt", "-l", str(abs_path)])
            if rc == -1 and "Tool not found" in err:
                results.append({"tool": "gofmt", "returncode": None, "skipped": True, "output": "gofmt not installed in sandbox"})
            else:
                verified = True
                results.append({"tool": "gofmt", "returncode": rc, "output": out or err or "(formatted)"})
                if out.strip():
                    passed = False
            # go vet: undefined symbols / wrong arg counts (needs module context)
            await _lint_step("go", ["vet", "./..."], label="go vet")
            # go build: REAL compile check (syntax + type errors). For a single
            # file this only works when the package is self-contained, but it
            # catches the common errors and never silently lies about passing.
            await _lint_step("go", ["build", "-o", "/dev/null", str(abs_path)], label="go build")

        # ── Rust ──────────────────────────────────────────────────────────────
        elif ext == ".rs" or forced == "rust":
            # rustfmt --check: formatting + parse errors (exits non-zero if unformatted)
            await _lint_step("rustfmt", ["--check", "--edition", "2021"])
            # Real compile/type check. `rustc` on a lone .rs file false-fails for
            # normal bin/lib files (no main / split modules), so only run cargo
            # when a project is actually present — otherwise report the gap.
            cargo_toml = os.path.join(os.path.dirname(abs_path), "Cargo.toml")
            if os.path.exists(cargo_toml):
                await _lint_step("cargo", ["check", "--offline"], label="cargo check")
            else:
                results.append({"tool": "cargo check", "returncode": None, "skipped": True,
                                "output": "no Cargo.toml here — compile check requires a Cargo project"})

        # ── C / C++ ───────────────────────────────────────────────────────────
        elif ext in (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp") or forced in ("gcc", "clang"):
            cc = "g++" if ext in (".cpp", ".cc", ".cxx", ".hpp") else "gcc"
            await _lint_step(cc, ["-fsyntax-only"])
            await _lint_step("clang", ["-fsyntax-only"])

        # ── Java ──────────────────────────────────────────────────────────────
        elif ext == ".java" or forced == "java":
            await _lint_step("javac", ["-d", "/dev/null"])

        # ── Ruby ──────────────────────────────────────────────────────────────
        elif ext == ".rb" or forced == "ruby":
            await _lint_step("ruby", ["-c"])

        # ── Lua ───────────────────────────────────────────────────────────────
        elif ext == ".lua" or forced == "lua":
            await _lint_step("luac", ["-p"])

        # ── PHP ───────────────────────────────────────────────────────────────
        elif ext == ".php" or forced == "php":
            await _lint_step("php", ["-l"])

        # ── JSON ─────────────────────────────────────────────────────────────
        elif ext == ".json" or forced == "json":
            await _lint_step("python3", ["-m", "json.tool"])

        # ── YAML ─────────────────────────────────────────────────────────────
        elif ext in (".yaml", ".yml") or forced == "yamllint":
            await _lint_step("yamllint", ["-d", "relaxed"])

        # ── Dockerfile ────────────────────────────────────────────────────────
        elif os.path.basename(req.path) == "Dockerfile" or forced == "hadolint":
            await _lint_step("hadolint", [])

        else:
            return _ok(f"No linter configured for {ext} files — skipping.", {"path": req.path, "skipped": True})

        summary = "PASSED" if passed else "ISSUES_FOUND"
        if not verified:
            # Every configured checker was missing in the sandbox — the code was
            # NEVER actually checked. Surface this loudly so the model can't read
            # "clean" and trust it. This is the root-cause fix for error-ridden
            # code passing review.
            summary = "UNVERIFIED"
            prefix = "Lint ran but NO checker executed (tools missing in sandbox). "
        else:
            prefix = ""
        detail = "\n".join(f"[{r['tool']}] rc={r.get('returncode')} {'[SKIPPED]' if r.get('skipped') else ''}\n{r['output']}" for r in results)
        msg = f"{prefix}Lint {summary} for {req.path}:\n{detail}"
        # Return SUCCESS regardless of lint outcome — lint findings are diagnostic results,
        # not tool failures. The LLM inspects `passed`/`verified` in the detail to decide
        # next steps. Using "issues found" instead of "failed" avoids triggering the
        # mission failure detector. `verified` lets run_post_write_lint flag untrusted passes.
        return _ok(msg, {"path": req.path, "passed": passed, "verified": verified, "results": results})

    except Exception as e:
        log.error(f"Workspace lint failed: {e}")
        return _fail(str(e))
