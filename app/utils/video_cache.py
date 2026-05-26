"""Minimal stub for app.utils.video_cache to support legacy scripts."""
from __future__ import annotations

from pathlib import Path


def get_video_id(url: str) -> str:
    """Stub for get_video_id."""
    return "test_video_id"


async def download_video_progressive(
    url: str,
    video_id: str,
) -> tuple[Path, bool]:
    """Stub for download_video_progressive."""
    return Path("/tmp/video.mp4"), True
