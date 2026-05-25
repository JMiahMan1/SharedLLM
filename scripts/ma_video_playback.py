#!/usr/bin/env python3
"""
Test script to verify if Music Assistant (mass) entity can handle video playback
or if it results in audio-only/failure.
"""
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.settings import SERVER_URL
from app.domains.shared import execute_ha_service
from app.utils.video_cache import download_video_progressive, get_video_id

# Setup logging
logging.basicConfig(level=logging.INFO)

# Override Cache Dir to ensuring writing to mounted volume
os.environ["CAST_CACHE_DIR"] = "/data/temp_videos"
# Create it if not exists (handled by video_cache but good to ensure parent)
# os.makedirs("/data/temp_videos", exist_ok=True)

MA_ENTITY: str = "media_player.music_assistant"  # noqa: F821
TEST_VIDEO_URL: str = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # noqa: F821


async def test_ma_video_target() -> None:
    print(f"\n[TEST] Testing Video Playback targeting Music Assistant entity: {MA_ENTITY}")

    # 1. Download Video
    print(f"[SETUP] Downloading video: {TEST_VIDEO_URL}")
    video_id = get_video_id(TEST_VIDEO_URL)
    file_path, ready = await download_video_progressive(TEST_VIDEO_URL, video_id)

    if not ready or not file_path:
        print("Failed to download video")
        return

    local_url = f"{SERVER_URL}/cast_video/{file_path.name}"
    print(f"Video ready at: {local_url}")

    # 2. Stop Previous Session (Clean Slate)
    print(f"[SETUP] Stopping {MA_ENTITY}...")
    await execute_ha_service("media_player", "media_stop", MA_ENTITY, {"user": "admin"}, {}, None)  # type: ignore[misc]
    await asyncio.sleep(2)

    # 3. Play to MA Entity
    print(f"[ACTION] Sending play_media to {MA_ENTITY} with type='video'...")

    # Payload
    payload: dict[str, str] = {
        "media_content_id": local_url,
        "media_content_type": "video",
    }

    try:
        res = await execute_ha_service(
            "media_player",
            "play_media",
            MA_ENTITY,
            {"user": "admin"},
            payload,
            None,
        )
        assert isinstance(res, dict)
        print(f"[RESULT] HA Service Call: {res}")

    except Exception as e:
        print(f"Service call failed: {e}")

    # 4. Check State
    print("[VERIFY] check state in 5 seconds...")
    await asyncio.sleep(5)

    import requests

    try:
        resp = requests.get(f"http://ai.local:11435/api/ha/state/{MA_ENTITY}", timeout=5)
        state_data = resp.json()
        print(f"\n[STATE] Entity: {MA_ENTITY}")
        print(f"  State: {state_data.get('state')}")
        print(f"  App ID: {state_data.get('attributes', {}).get('app_id')}")
        print(f"  Media Content Type: {state_data.get('attributes', {}).get('media_content_type')}")
        print(f"  Media Title: {state_data.get('attributes', {}).get('media_title')}")

    except Exception as e:
        print(f"Failed to get status: {e}")


if __name__ == "__main__":
    asyncio.run(test_ma_video_target())
