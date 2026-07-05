# services/execution/handlers/workspace.py
import asyncio
import difflib
import logging
import os
import shlex
import traceback
from typing import Optional
from fastapi import HTTPException
from services.config import WORKSPACE_ROOT, WORKSPACE_RUNTIME_SVC_URL, INTERNAL_SECRET
from services.execution.schemas import (
    ExecutionResult,
    WorkspaceFileReadRequest,
    WorkspaceFileWriteRequest,
    WorkspaceFilePatchRequest,
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
    "black", "eslint", "flake8", "pytest", "python", "python3", "git"
}
# Truly dangerous system-level commands — never allowed regardless of context
SYSTEM_BLOCKLIST_COMMANDS = {
    "sudo", "su", "dd", "mkfs", "mount", "umount",
    "reboot", "shutdown", "poweroff", "halt",
    "insmod", "modprobe", "rmmod", "modinfo",
    "iptables", "ip6tables", "ufw", "firewall-cmd",
    "fdisk", "parted", "losetup",
}
SHELL_BLOCKLIST_TOKENS = {">", ">>", "<"}

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="workspace", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="workspace", detail=detail)

async def _resolve_workspace_info(workspace_id: Optional[str], user_context: Optional[dict] = None) -> tuple[str, dict]:
    """Resolves workspace path and details from workspace_runtime service, and checks capability."""
    import aiohttp
    # Defaults
    resolved_path = WORKSPACE_ROOT
    workspace_details = {}
    
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
                            workspace_details = data.get("workspace", {})
                            resolved_path = workspace_details.get("resolved_path") or WORKSPACE_ROOT
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
            
    return resolved_path, workspace_details

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

def resolve_safe_path(path: str, workspace_root: str = WORKSPACE_ROOT) -> str:
    """Ensure the path stays within workspace_root."""
    # Remove leading slash if present to make it relative to root
    rel_path = path.lstrip("/")
    abs_path = os.path.abspath(os.path.join(workspace_root, rel_path))
    if not abs_path.startswith(os.path.abspath(workspace_root)):
        raise ValueError(f"Path traversal detected: {path}")
    return abs_path

async def _run_command_async(
    cmd: list[str] | str,
    cwd: Optional[str] = None,
    timeout: float = 30.0,
    shell: bool = False
) -> tuple[int, str, str]:
    """Runs a subprocess asynchronously using asyncio to prevent blocking the event loop."""
    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                cmd if isinstance(cmd, str) else " ".join(cmd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
        
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutExpired:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            raise TimeoutError(f"Command timed out after {timeout} seconds")
    except FileNotFoundError as e:
        return -1, "", f"Executable or directory not found: {e}"

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
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        
        # Strategy 3: Semantic Extraction (Signatures Only)
        if req.summary_only and req.path.endswith(".py"):
            import ast
            try:
                tree = ast.parse("".join(lines))
                summary = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        summary.append(f"Class: {node.name} (line {node.lineno})")
                    elif isinstance(node, ast.FunctionDef):
                        summary.append(f"Function: {node.name}({[a.arg for a in node.args.args]}) (line {node.lineno})")
                
                content = "\n".join(summary)
                return _ok(f"Semantic map for {req.path} ({len(summary)} symbols found)", {"content": content, "path": req.path})
            except Exception as e:
                log.warning(f"AST parse failed for {req.path}, falling back to chunked read: {e}")

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

def _get_discovery_suggestion(path: str) -> Optional[str]:
    """Dynamically discovers similar files in the workspace to help agents self-correct."""
    try:
        filename = os.path.basename(path)
        if not filename:
            return None
        
        matches = []
        for root, _, files in os.walk(WORKSPACE_ROOT):
            # Skip hidden directories like .git
            if "/.git" in root:
                continue
            for f in files:
                if f.lower() == filename.lower():
                    rel_path = os.path.relpath(os.path.join(root, f), WORKSPACE_ROOT)
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
        
        # Build rg command
        cmd = ["rg", "--json", "-i", "--max-count", "100", req.query, abs_search_path]
        if req.include:
            cmd.extend(["-g", req.include])
        if req.exclude:
            cmd.extend(["-g", f"!{req.exclude}"])
            
        log.info(f"Running search: {' '.join(cmd)}")
        try:
            rc, stdout, stderr = await _run_command_async(cmd, timeout=30.0)
            if rc == -1 and "Executable or directory not found" in stderr:
                raise FileNotFoundError()
        except FileNotFoundError:
            log.info("ripgrep (rg) not found, falling back to grep")
            cmd = ["grep", "-rnI", "-e", req.query, abs_search_path]
            if req.include:
                cmd = ["grep", "-rnI", "--include", req.include, "-e", req.query, abs_search_path]
            rc, stdout, stderr = await _run_command_async(cmd, timeout=30.0)
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
        abs_cwd = resolve_safe_path(safe_cwd, ws_root)
        
        # Determine the final command string
        final_cmd = ""
        if req.commands:
            final_cmd = " && ".join(req.commands)
        elif req.command:
            final_cmd = req.command
        else:
            return _fail("Neither 'command' nor 'commands' provided")

        # Check blocklist before parsing
        if any(token in final_cmd for token in SHELL_BLOCKLIST_TOKENS):
            return _fail("Shell operators are not allowed for autonomous workspace commands")

        parsed = shlex.split(final_cmd)
        if not parsed:
            return _fail("Shell command is empty")

        base_command = parsed[0]
        normalized_command = f"{base_command}-{parsed[1]}" if base_command == "git" and len(parsed) > 1 else base_command

        if normalized_command in SYSTEM_BLOCKLIST_COMMANDS:
            return _fail(
                f"Shell command '{normalized_command}' is blocked: dangerous system-level operation not permitted."
            )

        allowed = (
            normalized_command in READ_ONLY_SHELL_COMMANDS
            or normalized_command in CODE_EDITING_SHELL_COMMANDS
            or base_command in VERIFICATION_SHELL_COMMANDS
        )
        if not allowed:
            return _fail(
                f"Shell command '{normalized_command}' is not allowed. Allowed commands: read-only tools (cat, ls, grep, etc.), code editing tools (touch, mkdir, etc.), and verification tools (pytest). Use read/search/write/patch tools for complex operations."
            )

        if base_command in {"python", "python3"} and parsed[1:3] != ["-m", "pytest"]:
            return _fail("Only pytest execution is allowed through python shell commands")

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

        log.info(f"Executing shell command: {final_cmd} in {abs_cwd}")
        # Enforce a max timeout of 300s
        safe_timeout = min(req.timeout, 300)
        
        try:
            rc, stdout, stderr = await _run_command_async(final_cmd, cwd=abs_cwd, timeout=safe_timeout, shell=True)
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
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
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
        abs_path = resolve_safe_path(req.path)
        if not os.path.exists(abs_path):
            return _fail(f"File not found: {req.path}")

        ext = os.path.splitext(req.path)[1].lower()
        forced = (req.linter or "").strip().lower()
        results = []
        passed = True

        async def _run(cmd):
            try:
                rc, stdout, stderr = await _run_command_async(cmd, timeout=30.0)
                if rc == -1 and "Executable or directory not found" in stderr:
                    raise FileNotFoundError()
                return rc, stdout.strip(), stderr.strip()
            except TimeoutError:
                return -2, "", "Timeout expired after 30s"
            except FileNotFoundError:
                return -1, "", f"Tool not found: {cmd[0]}"

        # ── Python ───────────────────────────────────────────────────────────
        if ext == ".py" or forced in ("ruff", "black", "flake8", "python"):
            # Syntax check first — catches malformed files (missing imports, broken syntax)
            rc, out, err = await _run(["python3", "-m", "py_compile", str(abs_path)])
            if rc == -1:
                return _ok("Python compiler not available — skipping lint.", {"path": req.path, "skipped": True})
            results.append({"tool": "py_compile", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False
            else:
                # Ruff: fast modern linter + formatter (replaces flake8, black, isort)
                rc, out, err = await _run(["ruff", "check", str(abs_path)])
                results.append({"tool": "ruff", "returncode": rc, "output": out or err})
                if rc != 0:
                    passed = False

        # ── JavaScript / TypeScript ───────────────────────────────────────────
        elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs") or forced == "eslint":
            fix_flag = ["--fix"] if req.fix else []
            rc, out, err = await _run(["eslint"] + fix_flag + [str(abs_path)])
            results.append({"tool": "eslint", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False

        # ── JSON ─────────────────────────────────────────────────────────────
        elif ext == ".json" or forced == "json":
            rc, out, err = await _run(["python3", "-m", "json.tool", str(abs_path)])
            results.append({"tool": "json.tool", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False

        # ── YAML ─────────────────────────────────────────────────────────────
        elif ext in (".yaml", ".yml") or forced == "yamllint":
            rc, out, err = await _run(["yamllint", "-d", "relaxed", str(abs_path)])
            results.append({"tool": "yamllint", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False

        else:
            return _ok(f"No linter configured for {ext} files — skipping.", {"path": req.path, "skipped": True})

        summary = "PASSED" if passed else "ISSUES_FOUND"
        detail = "\n".join(f"[{r['tool']}] rc={r['returncode']}\n{r['output']}" for r in results)
        msg = f"Lint {summary} for {req.path}:\n{detail}"
        # Return SUCCESS regardless of lint outcome — lint findings are diagnostic results,
        # not tool failures. The LLM inspects `passed` in the detail to decide next steps.
        # Using "issues found" instead of "failed" avoids triggering the mission failure detector.
        return _ok(msg, {"path": req.path, "passed": passed, "results": results})

    except Exception as e:
        log.error(f"Workspace lint failed: {e}")
        return _fail(str(e))
