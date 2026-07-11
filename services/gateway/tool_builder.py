"""
Raven tool-builder.

Lets Raven discover an existing tool, chain existing tools together, or
scaffold + run a brand-new tool when nothing fits.

The decision logic backs the ``RavenBuildToolRequest`` action handled in
``agent_loop.py``. Three outcomes, tried in order:

1. ``use_existing`` — a single known tool already covers the capability, so
   Raven should call that tool directly instead of reinventing it.
2. ``chain`` — no single tool fits, but a small ordered set (<=3) of known
   tools together covers the capability; Raven executes them in sequence.
3. ``build`` — nothing fits, so we scaffold a runnable Python tool in the
   mission workspace and tell Raven to implement ``run()`` and then execute it.

This keeps Raven from duplicating capabilities that already exist and gives it
a safe, sandboxed path to extend itself when it hits a genuine gap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _Tool:
    name: str
    description: str
    # Lower-cased substrings; a tool matches when ANY trigger appears in the
    # capability description. Keep these SPECIFIC so generic verbs ("build",
    # "run", "test") don't falsely claim an existing tool covers the task.
    triggers: tuple[str, ...]
    # Ordering hint for chaining (lower runs first).
    order: int = 0


# Catalog of capabilities Raven can already perform. This is the single source
# of truth the router checks before suggesting a brand-new tool.
_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "WorkspaceShellRequest",
        "Run a shell command inside the workspace (gh, git, lint, tests, builds).",
        ("run command", "execute shell", "run tests", "run lint", "gh ", "git ",
         "npm ", "pip install", "python -m", "compile", "build the"),
        5,
    ),
    _Tool(
        "WorkspaceFileWriteRequest",
        "Write/create a file in the workspace.",
        ("write file", "create file", "save file", "write code", "create a file", "new file"),
        2,
    ),
    _Tool(
        "WorkspaceFileReadRequest",
        "Read a file from the workspace.",
        ("read file", "show file", "view file", "cat file", "open file"),
    ),
    _Tool(
        "WorkspaceFilePatchRequest",
        "Patch/modify an existing file in the workspace.",
        ("patch file", "edit file", "modify file", "update file"),
    ),
    _Tool(
        "WorkspaceSearchRequest",
        "Search the workspace for code/text.",
        ("search code", "grep", "find in files", "search the workspace", "find code"),
    ),
    _Tool(
        "WorkspaceLintRequest",
        "Lint a file in the workspace.",
        ("lint file", "format file", "flake8", "ruff check"),
    ),
    _Tool(
        "GitOperationRequest",
        "Run a git operation (clone, add, commit, push, pull, log).",
        ("git ", "commit", "push", "clone", "git diff", "git log",
         "git add", "git status"),
    ),
    _Tool(
        "WorkspaceBootstrapRequest",
        "Bootstrap/clone an existing repo into a workspace.",
        ("bootstrap", "clone repo", "checkout repo", "clone the"),
    ),
    _Tool(
        "WorkspaceSettingsUpdateRequest",
        "Update workspace settings (repo_url, branch, display name).",
        ("workspace settings", "set repo url", "wire remote", "set remote"),
    ),
    _Tool(
        "ImageGenerationRequest",
        "Generate an image from a text prompt via Stable Diffusion.",
        ("generate image", "image from prompt", "stable diffusion", "text to image",
         "make a picture", "create an image"),
    ),
    _Tool(
        "DockerLogsRequest",
        "Fetch Docker container logs for diagnostics.",
        ("docker log", "container log", "service log", "pod log"),
    ),
    _Tool(
        "StorageListRequest",
        "List files in Nextcloud/Storage.",
        ("list storage", "list files in storage", "nextcloud files", "storage list"),
    ),
    _Tool(
        "StorageIndexRequest",
        "Index Storage into RAG.",
        ("index storage", "index files", "rag index", "index the storage"),
    ),
    _Tool(
        "sharedllm_gh",
        "Run a GitHub CLI command (repo create, PRs, issues).",
        ("github", "gh repo", "create repository", "open pull request", "open issue",
         "github cli", "create a repo"),
    ),
    _Tool(
        "sharedllm_raven_mission",
        "Dispatch a background Raven mission.",
        ("raven mission", "dispatch mission", "delegate to raven", "sub mission",
         "another raven"),
    ),
)


def slugify(text: str) -> str:
    """Turn an arbitrary capability string into a safe filesystem slug."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "tool").lower()).strip("-")
    return (s[:40] or "tool").strip("-")


def _matched_tools(cap: str) -> list[_Tool]:
    return [t for t in _TOOLS if any(trig in cap for trig in t.triggers)]


def decide(capability: str) -> dict[str, Any]:
    """Route a capability to one of: use_existing, chain, or build.

    Returns a structured decision dict Raven can act on directly.
    """
    cap = (capability or "").lower().strip()
    matched = _matched_tools(cap)

    # Step 1 — a single existing tool already covers it.
    if len(matched) == 1:
        t = matched[0]
        return {
            "decision": "use_existing",
            "tool": t.name,
            "description": t.description,
            "triggers_matched": [trig for trig in t.triggers if trig in cap],
        }

    # Step 2 — multiple existing tools together cover it: return an ordered chain.
    if len(matched) >= 2:
        chain = sorted(matched, key=lambda t: t.order)[:3]
        return {
            "decision": "chain",
            "steps": [
                {"tool": t.name, "description": t.description}
                for t in chain
            ],
        }

    # Step 3 — nothing fits: scaffold a new runnable tool.
    slug = slugify(capability)
    return {
        "decision": "build",
        "slug": slug,
        "tool_path": f"tools/{slug}.py",
        "instruction": (
            f"Implement run() in tools/{slug}.py to: {capability.strip()}. "
            f"Then execute it with: python tools/{slug}.py <args>"
        ),
    }


def scaffold_source(capability: str, slug: str | None = None) -> str:
    """Generate a runnable Python tool scaffold (``run()`` left for Raven)."""
    safe = slug or slugify(capability)
    cap = (capability or safe).strip()
    return (
        f'#!/usr/bin/env python3\n'
        f'"""\n'
        f'Auto-scaffolded Raven tool: {safe}\n\n'
        f'Capability requested:\n'
        f'    {cap}\n\n'
        f'Implement run() below to perform the capability. Keep it self-contained\n'
        f'(stdlib only unless the workspace already provides the dependency). Then run:\n\n'
        f'    python tools/{safe}.py <args>\n\n'
        f'Return a process exit code (0 == success).\n'
        f'"""\n'
        f'import argparse\n'
        f'import sys\n'
        f'\n'
        f'\n'
        f'def run(argv: list[str]) -> int:\n'
        f'    # TODO(Raven): implement the capability here.\n'
        f'    # Use argv; read any needed inputs; produce output / side effects.\n'
        f'    raise NotImplementedError("Implement run() for: {cap}")\n'
        f'\n'
        f'\n'
        f'def main() -> int:\n'
        f'    parser = argparse.ArgumentParser(description="{safe}")\n'
        f'    # TODO(Raven): add the arguments this capability requires.\n'
        f'    parser.add_argument("args", nargs="*", help="capability arguments")\n'
        f'    ns = parser.parse_args()\n'
        f'    try:\n'
        f'        return run(ns.args)\n'
        f'    except NotImplementedError as e:\n'
        f'        print(f"[tool:{safe}] NOT IMPLEMENTED: {{e}}", file=sys.stderr)\n'
        f'        return 2\n'
        f'\n'
        f'\n'
        f'if __name__ == "__main__":\n'
        f'    sys.exit(main())\n'
    )
