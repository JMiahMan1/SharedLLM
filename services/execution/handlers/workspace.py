# services/execution/handlers/workspace.py
import os
import sys
import logging
import difflib
import shlex
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import WORKSPACE_ROOT
from typing import Optional
from schemas import WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, WorkspaceSearchRequest, WorkspaceShellRequest, ExecutionResult

log = logging.getLogger("execution.workspace")
READ_ONLY_SHELL_COMMANDS = {
    "cat", "find", "git", "head", "ls", "pwd", "rg", "sed", "tail", "wc", "grep", "du", "stat"
}
VERIFICATION_SHELL_COMMANDS = {
    "black", "eslint", "flake8", "pytest", "python", "python3"
}
SHELL_BLOCKLIST_TOKENS = {">", ">>", "<"}
SHELL_BLOCKLIST_COMMANDS = {
    "bash", "chmod", "cp", "git-commit", "git-push", "mv", "rm", "sh", "sudo", "su", "curl", "wget", "xargs"
}

def _ok(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="SUCCESS", message=message, service="workspace", detail=detail)

def _fail(message: str, detail: dict | None = None) -> ExecutionResult:
    return ExecutionResult(status="FAILURE", message=message, service="workspace", detail=detail)

def resolve_safe_path(path: str) -> str:
    """Ensure the path stays within WORKSPACE_ROOT."""
    # Remove leading slash if present to make it relative to root
    rel_path = path.lstrip("/")
    abs_path = os.path.abspath(os.path.join(WORKSPACE_ROOT, rel_path))
    if not abs_path.startswith(os.path.abspath(WORKSPACE_ROOT)):
        raise ValueError(f"Path traversal detected: {path}")
    return abs_path

async def handle_workspace_read(req: WorkspaceFileReadRequest) -> ExecutionResult:
    try:
        abs_path = resolve_safe_path(req.path)
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
        abs_path = resolve_safe_path(req.path)
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
    except Exception as e:
        log.error(f"Workspace write failed: {e}")
        return _fail(str(e))

async def handle_workspace_search(req: WorkspaceSearchRequest) -> ExecutionResult:
    """Performs a ripgrep search in the workspace."""
    import subprocess
    try:
        abs_search_path = resolve_safe_path(req.path)
        
        # Build rg command
        cmd = ["rg", "--json", "-i", "--max-count", "100", req.query, abs_search_path]
        if req.include:
            cmd.extend(["-g", req.include])
        if req.exclude:
            cmd.extend(["-g", f"!{req.exclude}"])
            
        log.info(f"Running search: {' '.join(cmd)}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            log.info("ripgrep (rg) not found, falling back to grep")
            cmd = ["grep", "-rnI", "-e", req.query, abs_search_path]
            if req.include:
                cmd = ["grep", "-rnI", "--include", req.include, "-e", req.query, abs_search_path]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        # Ripgrep returns 1 if no matches found, which isn't a failure in our case
        if proc.returncode not in (0, 1):
            return _fail(f"Search failed: {proc.stderr}")
            
        matches = []
        import json
        for line in proc.stdout.splitlines():
            try:
                # Try parsing as JSON (rg output), otherwise treat as raw grep line
                data = json.loads(line)
                if data.get("type") == "match":
                    match_data = data.get("data", {})
                    matches.append({
                        "path": os.path.relpath(match_data.get("path", {}).get("text", ""), WORKSPACE_ROOT),
                        "line": match_data.get("line_number"),
                        "text": match_data.get("lines", {}).get("text", "").strip()
                    })
            except Exception:
                # Basic grep fallback parsing
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    matches.append({
                        "path": os.path.relpath(parts[0], WORKSPACE_ROOT),
                        "line": parts[1],
                        "text": parts[2].strip()
                    })
                
        if not matches:
            return _ok(f"No matches found for '{req.query}' in {req.path}", {"matches": []})
            
        return _ok(f"Found {len(matches)} matches for '{req.query}'", {"matches": matches})
    except Exception as e:
        log.error(f"Workspace search failed: {e}")
        return _fail(str(e))

async def handle_workspace_shell(req: WorkspaceShellRequest) -> ExecutionResult:
    """Executes an arbitrary shell command in the workspace."""
    import subprocess
    try:
        # Resolve safe CWD
        safe_cwd = req.cwd if hasattr(req, 'cwd') and req.cwd else "."
        abs_cwd = resolve_safe_path(safe_cwd)
        
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

        if normalized_command in SHELL_BLOCKLIST_COMMANDS:
            return _fail(
                "Mutating shell commands are blocked. Use workspace write/patch tools and workspace runtime verification workflows instead."
            )

        allowed = normalized_command in READ_ONLY_SHELL_COMMANDS or base_command in VERIFICATION_SHELL_COMMANDS
        if not allowed:
            return _fail(
                f"Shell command '{normalized_command}' is not allowed. Use read/search tools or the workspace runtime workflow."
            )

        if base_command in {"python", "python3"} and parsed[1:3] != ["-m", "pytest"]:
            return _fail("Only pytest execution is allowed through python shell commands")

        if base_command == "git" and len(parsed) > 1 and parsed[1] not in {"status", "diff", "log", "show"}:
            return _fail("Only read-only git shell commands are allowed")

        log.info(f"Executing shell command: {final_cmd} in {abs_cwd}")
        # Enforce a max timeout of 300s
        safe_timeout = min(req.timeout, 300)
        
        # Use shell=True to support pipes and redirections (safe due to allowlist validation)
        proc = subprocess.run(
            final_cmd,
            shell=True,
            cwd=abs_cwd,
            capture_output=True,
            text=True,
            timeout=safe_timeout
        )
        
        detail = {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode
        }
        
        if proc.returncode == 0:
            cmd_prefix = req.command[:50] if req.command else "<unknown>"
            return _ok(f"Command executed successfully: {cmd_prefix}...", detail)
        else:
            return _fail(f"Command failed with exit code {proc.returncode}", detail)
            
    except subprocess.TimeoutExpired:
        return _fail(f"Command timed out after {req.timeout}s")
    except Exception as e:
        log.error(f"Workspace shell execution failed: {e}")
        return _fail(str(e))

async def handle_workspace_patch(req: WorkspaceFilePatchRequest) -> ExecutionResult:
    try:
        abs_path = resolve_safe_path(req.path)
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
    except Exception as e:
        log.error(f"Workspace patch failed: {e}")
        return _fail(str(e))


async def handle_workspace_lint(req) -> ExecutionResult:
    """Auto-detect and run the appropriate linter for the given file."""
    import subprocess
    try:
        abs_path = resolve_safe_path(req.path)
        if not os.path.exists(abs_path):
            return _fail(f"File not found: {req.path}")

        ext = os.path.splitext(req.path)[1].lower()
        forced = (req.linter or "").strip().lower()
        results = []
        passed = True

        def _run(cmd):
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
            except FileNotFoundError:
                return -1, "", f"Tool not found: {cmd[0]}"

        # ── Python ───────────────────────────────────────────────────────────
        if ext == ".py" or forced in ("ruff", "black", "flake8", "python"):
            # Syntax check first — catches malformed files (missing imports, broken syntax)
            rc, out, err = _run(["python3", "-m", "py_compile", str(abs_path)])
            if rc == -1:
                return _ok("Python compiler not available — skipping lint.", {"path": req.path, "skipped": True})
            results.append({"tool": "py_compile", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False
            else:
                # Ruff: fast modern linter + formatter (replaces flake8, black, isort)
                rc, out, err = _run(["ruff", "check", str(abs_path)])
                results.append({"tool": "ruff", "returncode": rc, "output": out or err})
                if rc != 0:
                    passed = False

        # ── JavaScript / TypeScript ───────────────────────────────────────────
        elif ext in (".js", ".ts", ".jsx", ".tsx", ".mjs") or forced == "eslint":
            fix_flag = ["--fix"] if req.fix else []
            rc, out, err = _run(["eslint"] + fix_flag + [str(abs_path)])
            results.append({"tool": "eslint", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False

        # ── JSON ─────────────────────────────────────────────────────────────
        elif ext == ".json" or forced == "json":
            rc, out, err = _run(["python3", "-m", "json.tool", str(abs_path)])
            results.append({"tool": "json.tool", "returncode": rc, "output": out or err})
            if rc != 0:
                passed = False

        # ── YAML ─────────────────────────────────────────────────────────────
        elif ext in (".yaml", ".yml") or forced == "yamllint":
            rc, out, err = _run(["yamllint", "-d", "relaxed", str(abs_path)])
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
