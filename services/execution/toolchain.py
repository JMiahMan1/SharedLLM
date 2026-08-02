# services/execution/toolchain.py
"""Workspace toolchain inventory.

Probes the execution container for the CLI tools Raven can actually run
inside its workspace shell and reports them to the RAG service as
``type="binary"`` capabilities (with version + scenario tags). The gateway
renders these as a truthful ``[WORKSPACE TOOLCHAIN]`` block in every mission
prompt, so Raven always knows its full toolset — writing, programming,
typesetting, publishing, image editing, media, and more — without hardcoding
anything in prompt text.

The probe is best-effort and never blocks startup: missing binaries are
skipped, and a failed sync is logged as a warning.
"""
import logging
import shutil
import subprocess
from typing import Callable

log = logging.getLogger("execution.toolchain")

# (binary, --version flag, scenario tags, short description)
# The version flag list covers common conventions; each probe falls back
# through the flags until one exits successfully.
TOOLCHAIN_PROBES: list[tuple[str, list[str], list[str], str]] = [
    ("python3", ["--version"], ["programming", "scripting", "writing"], "Python interpreter"),
    ("pip", ["--version"], ["programming"], "Python package installer"),
    ("git", ["--version"], ["programming", "publishing"], "Version control"),
    ("gh", ["--version"], ["programming", "publishing"], "GitHub CLI (repos, PRs, issues)"),
    ("pandoc", ["--version"], ["typesetting", "publishing", "writing", "document"], "Universal document converter (Markdown -> PDF/HTML/DOCX)"),
    ("pdflatex", ["--version"], ["typesetting", "publishing"], "LaTeX engine"),
    ("convert", ["--version"], ["image-editing", "publishing"], "ImageMagick image editing (resize, crop, annotate, format)"),
    ("magick", ["--version"], ["image-editing"], "ImageMagick 7 CLI"),
    ("identify", ["--version"], ["image-editing"], "ImageMagick image metadata"),
    ("gs", ["--version"], ["publishing", "pdf"], "Ghostscript PDF engine"),
    ("ffmpeg", ["-version"], ["media", "video", "publishing"], "Audio/video processing"),
    ("ffprobe", ["-version"], ["media", "video"], "Media file inspection"),
    ("tesseract", ["--version"], ["ocr", "document"], "OCR text extraction"),
    ("pdftotext", ["-v"], ["document", "publishing", "pdf"], "PDF text extraction"),
    ("pdftoppm", ["-v"], ["pdf", "publishing", "image-editing"], "PDF to image rendering"),
    ("rg", ["--version"], ["programming", "search"], "ripgrep fast search"),
    ("jq", ["--version"], ["programming", "data"], "JSON processor"),
    ("curl", ["--version"], ["web", "programming"], "HTTP client"),
    ("wget", ["--version"], ["web"], "HTTP downloader"),
    ("zip", ["--version"], ["archives"], "Zip archives"),
    ("unzip", ["-v"], ["archives"], "Unzip archives"),
    ("tree", ["--version"], ["files"], "Directory tree listing"),
    ("file", ["--version"], ["files"], "File type detection"),
    ("xxd", ["--version"], ["programming", "data"], "Hex dump"),
    ("tshark", ["--version"], ["network", "diagnostics"], "Packet capture"),
    ("nmap", ["--version"], ["network", "diagnostics"], "Network scanning"),
]


def _probe_version(binary: str, flags: list[str], run: Callable = subprocess.run) -> str:
    """Best-effort first line of `<binary> <flag>`, truncated to 60 chars."""
    for flag in flags:
        try:
            proc = run(
                [binary, flag],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = (proc.stdout or "").splitlines()
            if out:
                return out[0].strip()[:60]
        except Exception:
            continue
    return ""


def discover_toolchain(
    which: Callable = shutil.which,
    run: Callable = subprocess.run,
) -> list[dict]:
    """Return the installed binaries as capability dicts for the RAG sync."""
    capabilities = []
    for binary, flags, tags, description in TOOLCHAIN_PROBES:
        path = which(binary)
        if not path:
            continue
        version = _probe_version(binary, flags, run)
        capabilities.append({
            "name": binary,
            "description": f"{description}. Version: {version or 'unknown'}. "
                           f"Scenario: {', '.join(tags)}.",
            "version": version,
            "tags": tags,
            "type": "binary",
        })
    return capabilities


async def sync_toolchain_to_rag() -> dict:
    """POST the discovered toolchain to the RAG capabilities sync.

    Resolves the RAG URL from the ``RAG_SVC_URL`` environment variable
    (the network-mode-correct URL set by docker-compose), NOT from
    ``services.config.RAG_SVC_URL`` — that module-level value is overwritten
    by ``resolve_runtime_config()`` with the BRIDGE-mode URL (``http://rag:8004``)
    from Identity, which does not resolve in this host-network container.
    Retries the POST a few times with a short backoff so the sync survives
    the deploy window where RAG may not be serving yet (containers restart
    together). A permanent failure only logs a warning — best-effort.
    """
    import asyncio
    import os

    import aiohttp

    from services.config import INTERNAL_SECRET

    rag_url = os.environ.get("RAG_SVC_URL", "").strip()
    if not rag_url:
        log.warning("[toolchain] RAG_SVC_URL env var is not set; skipping sync")
        return {"status": "ERROR", "count": 0, "error": "RAG_SVC_URL not set"}

    log.warning(f"[toolchain] task starting; RAG_SVC_URL={rag_url}")
    capabilities = discover_toolchain()
    if not capabilities:
        log.warning("[toolchain] no binaries discovered; nothing to sync")
        return {"status": "SKIPPED", "count": 0}
    last_error = "unknown"
    for attempt in range(6):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15.0)) as client:
                async with client.post(
                    f"{rag_url}/rag/sync/capabilities",
                    json={"capabilities": capabilities},
                    headers={"X-Internal-Secret": INTERNAL_SECRET},
                ) as resp:
                    body = await resp.json() if resp.status == 200 else {}
                    if resp.status != 200:
                        last_error = f"status {resp.status}"
                        log.warning(f"[toolchain] sync attempt {attempt + 1}/6 failed: {resp.status}")
                        await asyncio.sleep(5)
                        continue
            log.info(f"[toolchain] synced {len(capabilities)} binaries to RAG")
            return {"status": "SUCCESS", "count": len(capabilities), **body}
        except Exception as e:
            last_error = str(e)[:120]
            log.warning(f"[toolchain] sync attempt {attempt + 1}/6 failed: {e}")
            await asyncio.sleep(5)
    log.warning(f"[toolchain] sync gave up after 6 attempts: {last_error}")
    return {"status": "ERROR", "count": 0, "error": last_error}
