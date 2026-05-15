# services/execution/handlers/video.py
import logging
import asyncio
import subprocess
import json
import re
import os
try:
    import ha_client
    from schemas import VideoPlayRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import VideoPlayRequest, ExecutionResult

log = logging.getLogger("execution.video")

# Video files stored on disk (streamed for large files)
TEMP_VIDEO_DIR = "/tmp/sharedllm_media"
os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)


def extract_video_url(query: str) -> str | None:
    """Check if query is already a direct video URL."""
    url_pattern = r"https?://(?:www\.)?(?:youtube\.com/watch\?v=[a-zA-Z0-9_-]+|youtu\.be/[a-zA-Z0-9_-]+|youtube\.com/shorts/[a-zA-Z0-9_-]+|rumble\.com/[a-zA-Z0-9_/]+|vimeo\.com/[0-9]+)"
    match = re.search(url_pattern, query)
    return match.group(0) if match else None


async def search_youtube(query: str) -> str | None:
    """Search YouTube and return the webpage URL of the top result."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--no-download",
            f"ytsearch1:{query}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0 and stdout:
            lines = stdout.decode().strip().split("\n")
            for line in lines:
                try:
                    info = json.loads(line)
                    if info.get("webpage_url"):
                        url = info["webpage_url"]
                        log.info(f"[video] YouTube search '{query}' -> {url}")
                        return url
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.error(f"[video] YouTube search failed: {e}")
    return None


async def download_video(video_url: str) -> tuple[str | None, str | None]:
    """
    Download video as MP4 using yt-dlp CLI.
    Returns (media_id, title) or (None, None) on failure.
    Files are stored on disk and streamed via /media/{media_id} endpoint.
    """
    import uuid
    
    media_id = f"vid-{uuid.uuid4().hex[:8]}"
    tmp_path = os.path.join(TEMP_VIDEO_DIR, f"{media_id}.mp4")
    
    try:
        # Download best MP4 with h264+aac for Cast/Roku compatibility
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-f", "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/best",
            "--no-playlist",
            "--merge-output-format", "mp4",
            "-o", tmp_path,
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            log.error(f"[video] yt-dlp download failed: {stderr.decode()[:300]}")
            return None, None
        
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            log.error(f"[video] Download produced empty file")
            return None, None
        
        file_size = os.path.getsize(tmp_path)
        log.info(f"[video] Downloaded {media_id} ({file_size / 1024 / 1024:.1f} MB)")
        
        # Get title
        title = video_url
        try:
            info_proc = await asyncio.create_subprocess_exec(
                "yt-dlp", "--dump-json", "--no-download",
                "--no-playlist", video_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            info_out, _ = await info_proc.communicate()
            if info_out:
                info = json.loads(info_out.decode())
                title = info.get("title", video_url)
        except:
            pass
        
        return media_id, title
        
    except Exception as e:
        log.error(f"[video] Download failed: {e}", exc_info=True)
        try:
            os.remove(tmp_path)
        except:
            pass
        return None, None


async def handle_video_play(req: VideoPlayRequest) -> ExecutionResult:
    ctx = req.user_context
    full_entity_id = ha_client.sanitize_entity_id("media_player", req.entity_id)
    log.info(f"[video/play] user={ctx.user} entity={full_entity_id} query='{req.query}'")

    # Step 1: Resolve to a video URL
    video_url = extract_video_url(req.query)
    if not video_url:
        video_url = await search_youtube(req.query)
        if not video_url:
            return ExecutionResult(
                status="FAILURE",
                message=f"Could not find a video for '{req.query}'.",
                service="video_play",
            )

    # Step 2: Download the video to disk
    media_id, title = await download_video(video_url)
    if not media_id:
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not download video from {video_url}.",
            service="video_play",
        )

    # Step 3: Build the local media URL for HA to stream
    def get_public_host():
        env_host = os.getenv("EXECUTION_EXTERNAL_HOST")
        if env_host:
            return env_host
        return "192.168.2.205"  # Default Production Host
    
    public_host = get_public_host()
    media_url = f"http://{public_host}:8003/media/{media_id}"
    log.info(f"[video/play] Casting URL: {media_url}")

    # Step 4: Power on the device
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    if state and state.get("state") == "off":
        log.info(f"[video/play] Device {full_entity_id} is off. Turning on...")
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", full_entity_id)
        await asyncio.sleep(2)

    # Step 5: Cast the video URL
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "media_player", "play_media",
        full_entity_id,
        {
            "media_content_id": media_url,
            "media_content_type": "video/mp4",
        },
    )

    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Now playing: {title}",
            service="video_play",
        )
    return ExecutionResult(
        status="FAILURE",
        message=f"Video playback failed: {result.get('error')}",
        service="video_play",
        detail=result,
    )
