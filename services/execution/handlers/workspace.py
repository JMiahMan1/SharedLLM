# services/execution/handlers/workspace.py
import os
import logging
import difflib
from typing import Dict, Any, Optional
from schemas import WorkspaceFileReadRequest, WorkspaceFileWriteRequest, WorkspaceFilePatchRequest, ExecutionResult

log = logging.getLogger("execution.workspace")

WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", "/workspace/SharedLLM")

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
            return _fail(f"File not found: {req.path}")
        if not os.path.isfile(abs_path):
            return _fail(f"Path is not a file: {req.path}")
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            
        return _ok(f"Read {len(content)} bytes from {req.path}", {"content": content, "path": req.path})
    except Exception as e:
        log.error(f"Workspace read failed: {e}")
        return _fail(str(e))

async def handle_workspace_write(req: WorkspaceFileWriteRequest) -> ExecutionResult:
    try:
        abs_path = resolve_safe_path(req.path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        
        original_content = ""
        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                original_content = f.read()
        
        new_content = req.content
        if getattr(req, "is_patch", False):
            # Simple unified diff patch support could go here, 
            # but for now we'll assume content IS the new content or handle patch logic.
            # In SharedLLM, 'is_patch' usually means the LLM provided a diff.
            # However, for simplicity and reliability, we prefer full writes.
            pass

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        message = f"Successfully wrote to {req.path}."
        if req.commit_after:
            # We would normally trigger git commit here, but for now we'll just log it
            message += " (Commit pending)"
            
        return _ok(message, {"path": req.path, "bytes": len(new_content)})
    except Exception as e:
        log.error(f"Workspace write failed: {e}")
        return _fail(str(e))
async def handle_workspace_patch(req: WorkspaceFilePatchRequest) -> ExecutionResult:
    try:
        abs_path = resolve_safe_path(req.path)
        if not os.path.exists(abs_path):
            return _fail(f"File not found for patching: {req.path}")
        
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        
        applied_count = 0
        failed_chunks = []
        
        for chunk in req.chunks:
            if chunk.old_text in content:
                content = content.replace(chunk.old_text, chunk.new_text, 1) # Only replace first occurrence
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
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()

        # ── Python ───────────────────────────────────────────────────────────
        if ext == ".py" or forced in ("black", "flake8", "python"):
            if forced != "flake8":
                black_args = [str(abs_path)] if req.fix else ["--check", "--diff", str(abs_path)]
                rc, out, err = _run(["black"] + black_args)
                results.append({"tool": "black", "returncode": rc, "output": out or err})
                if rc != 0:
                    passed = False
            if forced != "black":
                rc, out, err = _run(["flake8", "--max-line-length=120", str(abs_path)])
                results.append({"tool": "flake8", "returncode": rc, "output": out or err})
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

        summary = "PASSED" if passed else "FAILED"
        detail = "\n".join(f"[{r['tool']}] rc={r['returncode']}\n{r['output']}" for r in results)
        msg = f"Lint {summary} for {req.path}:\n{detail}"
        return _ok(msg, {"path": req.path, "passed": passed, "results": results}) if passed \
            else _fail(msg)

    except Exception as e:
        log.error(f"Workspace lint failed: {e}")
        return _fail(str(e))
