"""Shared /info endpoint for all FastAPI services.

Exposes service version, git commit, and build time.
Each service should mount this on their FastAPI app:

    from shared.info_endpoint import info_router
    app.include_router(info_router)
"""

import os
import subprocess
from fastapi import APIRouter

info_router = APIRouter(tags=["info"])


def _get_git_commit() -> str:
    """Get the current git commit SHA."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.environ.get("GIT_SHA", "unknown")


def _get_git_branch() -> str:
    """Get the current git branch name."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.environ.get("GIT_BRANCH", "unknown")


@info_router.get("/info")
async def service_info():
    """Service version and build info."""
    return {
        "service": os.environ.get("SERVICE_NAME", "unknown"),
        "version": os.environ.get("SERVICE_VERSION", "0.0.0"),
        "git_sha": _get_git_commit(),
        "git_branch": _get_git_branch(),
        "build_date": os.environ.get("BUILD_DATE", ""),
    }
