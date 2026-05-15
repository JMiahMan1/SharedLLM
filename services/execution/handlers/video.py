# services/execution/handlers/video.py
import logging
import asyncio
import subprocess
import json
import re
try:
    import ha_client
    from schemas import VideoPlayRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import VideoPlayRequest, ExecutionResult

log = logging.getLogger("execution.video")


def extract_video_url(query: str) -> str | None:
    """Check if query is already a direct video URL."""
    url_pattern = r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|rumble\.com/|vimeo\.com/)"
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
            # yt-dlp outputs one JSON line per result
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


async def get_stream_url(video_url: str) -> str | None:
    """Extract a direct stream URL from a video page using yt-dlp CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--no-download",
            "-f", "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/best",
            "--no-playlist",
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            log.error(f"[video] yt-dlp failed: {stderr.decode()[:200]}")
            return None
        
        if not stdout:
            log.warning(f"[video] yt-dlp returned no output for {video_url}")
            return None
        
        # Parse JSON output
        info = json.loads(stdout.decode())
        url = info.get("url", "")
        ext = info.get("ext", "")
        log.info(f"[video] yt-dlp CLI: url={str(url)[:80]}, ext={ext}")
        
        if url and url.startswith("http") and ext == "mp4":
            log.info(f"[video] Found MP4 stream URL")
            return url
        if url and url.startswith("http"):
            log.info(f"[video] Using resolved URL (ext={ext})")
            return url
        
        fallback = info.get("webpage_url") or info.get("original_url") or video_url
        log.warning(f"[video] No stream URL found, falling back to {fallback}")
        return fallback
    except Exception as e:
        log.error(f"[video] Stream extraction failed: {e}", exc_info=True)
        return None


async def get_video_title(video_url: str) -> str:
    """Get video title using yt-dlp CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--dump-json", "--no-download",
            "--no-playlist",
            video_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if stdout:
            info = json.loads(stdout.decode())
            return info.get("title", video_url)
    except:
        pass
    return video_url


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

    # Step 2: Extract direct stream URL
    stream_url = await get_stream_url(video_url)
    if not stream_url:
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not extract stream URL from {video_url}.",
            service="video_play",
        )

    # Step 3: Get video title for feedback
    title = await get_video_title(video_url)

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
            "media_content_id": stream_url,
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
