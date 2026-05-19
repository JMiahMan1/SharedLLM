# services/execution/handlers/video.py
import logging
import asyncio
import subprocess
import json
import re
import os
import sys
import httpx
import urllib.parse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import TEMP_MEDIA_DIR as _TEMP_MEDIA_DIR
try:
    import ha_client
    from schemas import VideoPlayRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import VideoPlayRequest, ExecutionResult

log = logging.getLogger("execution.video")

# Video files stored on disk (streamed for large files)
TEMP_VIDEO_DIR = _TEMP_MEDIA_DIR
os.makedirs(TEMP_VIDEO_DIR, exist_ok=True)


def extract_video_url(query: str) -> str | None:
    """Check if query is already a direct video URL."""
    url_pattern = r"https?://(?:www\.)?(?:youtube\.com/watch\?v=[a-zA-Z0-9_-]+|youtu\.be/[a-zA-Z0-9_-]+|youtube\.com/shorts/[a-zA-Z0-9_-]+|rumble\.com/[a-zA-Z0-9_/]+|vimeo\.com/[0-9]+)"
    match = re.search(url_pattern, query)
    return match.group(0) if match else None


import time

_SEARXNG_URL_CACHE = None
_SEARXNG_CACHE_TS = 0.0
_SEARXNG_CACHE_TTL = 300

async def _get_searxng_url() -> str | None:
    """Resolve SearXNG URL from Identity service global settings (cached)."""
    global _SEARXNG_URL_CACHE, _SEARXNG_CACHE_TS
    if _SEARXNG_URL_CACHE and (time.time() - _SEARXNG_CACHE_TS) < _SEARXNG_CACHE_TTL:
        return _SEARXNG_URL_CACHE
    try:
        from main import IDENTITY_SVC_URL, INTERNAL_SECRET
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{IDENTITY_SVC_URL}/api/settings",
                headers={"X-Internal-Secret": INTERNAL_SECRET}
            )
            if resp.status_code == 200:
                settings_list = resp.json()
                for item in settings_list:
                    if item.get("key") == "searxng_url":
                        url = item.get("value", "").rstrip("/")
                        if url:
                            _SEARXNG_URL_CACHE = url
                            _SEARXNG_CACHE_TS = time.time()
                            return url
    except Exception:
        pass
    return None


async def search_youtube(query: str) -> str | None:
    """Search YouTube via SearXNG HTML search API, with Playwright fallback on empty results."""
    searxng_url = await _get_searxng_url()
    if searxng_url:
        try:
            params = urllib.parse.urlencode({
                "q": f"site:youtube.com/watch {query}",
                "format": "html",
                "categories": "videos",
                "engines": "youtube",
            })
            search_url = f"{searxng_url}/search?{params}"
            log.info(f"[video] SearXNG YouTube search (HTML): {search_url}")
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(search_url)
                resp.raise_for_status()
                html = resp.text

            youtube_pattern = r'href="(https?://(?:www\.)?youtube\.com/watch\?[^"]+)"'
            matches = re.findall(youtube_pattern, html)
            for url in matches:
                if "v=" in url:
                    log.info(f"[video] YouTube search '{query}' -> {url}")
                    return url

            # No results from SearXNG, try without site: restriction
            log.info(f"[video] No results with site: restriction, retrying without")
            params = urllib.parse.urlencode({
                "q": query,
                "format": "html",
                "categories": "videos",
                "engines": "youtube",
            })
            search_url = f"{searxng_url}/search?{params}"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(search_url)
                resp.raise_for_status()
                html = resp.text

            matches = re.findall(youtube_pattern, html)
            for url in matches:
                if "v=" in url:
                    log.info(f"[video] YouTube search '{query}' -> {url}")
                    return url

            log.warning(f"[video] SearXNG returned no results, trying Playwright fallback")
        except Exception as e:
            log.warning(f"[video] SearXNG YouTube search failed, trying Playwright fallback: {e}")

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page()
            await page.goto(f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}", timeout=15000, wait_until="domcontentloaded")
            await page.wait_for_selector("a#video-title", timeout=10000)
            links = await page.query_selector_all("a#video-title")
            for link in links:
                href = await link.get_attribute("href")
                if href:
                    if href.startswith("/watch"):
                        await browser.close()
                        return f"https://www.youtube.com{href}"
                    elif href.startswith("http"):
                        await browser.close()
                        return href
            await browser.close()
    except Exception as e:
        log.error(f"[video] Playwright YouTube search failed: {e}")
    return None


YT_COOKIES_PATH = os.path.join(TEMP_VIDEO_DIR, "youtube_cookies.txt")

async def _ensure_youtube_cookies() -> str | None:
    """Use Playwright to extract YouTube cookies and save to Netscape format."""
    if os.path.exists(YT_COOKIES_PATH):
        return YT_COOKIES_PATH
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://www.youtube.com/", timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            cookies = await context.cookies()
            await browser.close()
            if not cookies:
                return None
            with open(YT_COOKIES_PATH, "w") as f:
                f.write("# Netscape HTTP Cookie File\n")
                for c in cookies:
                    domain = c.get("domain", ".youtube.com")
                    if not domain.startswith("."):
                        domain = "." + domain
                    f.write(f"{domain}\tTRUE\t{c.get('path', '/')}\t{'TRUE' if c.get('secure') else 'FALSE'}\t{c.get('expirationDate', 0)}\t{c['name']}\t{c['value']}\n")
            return YT_COOKIES_PATH
    except Exception as e:
        log.warning(f"Failed to extract YouTube cookies: {e}")
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
        cookies_path = await _ensure_youtube_cookies()
        cmd = ["yt-dlp", "-f", "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/best", "--no-playlist", "--merge-output-format", "mp4", "-o", tmp_path]
        if cookies_path:
            cmd.extend(["--cookies", cookies_path])
        cmd.append(video_url)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
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
        
        title = video_url
        try:
            info_cmd = ["yt-dlp", "--dump-json", "--no-download", "--no-playlist"]
            if cookies_path:
                info_cmd.extend(["--cookies", cookies_path])
            info_cmd.append(video_url)
            info_proc = await asyncio.create_subprocess_exec(
                *info_cmd,
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


PROGRESSIVE_THRESHOLD = 5 * 1024 * 1024  # 5 MB


async def download_video_progressive(video_url: str, threshold: int = PROGRESSIVE_THRESHOLD) -> tuple[str | None, str | None]:
    """
    Download video with progressive playback support.
    Returns (media_id, title) as soon as threshold bytes are downloaded,
    while continuing the download in the background.
    """
    import uuid
    
    media_id = f"vid-roku-{uuid.uuid4().hex[:8]}"
    tmp_path = os.path.join(TEMP_VIDEO_DIR, f"{media_id}.mp4")
    
    try:
        cookies_path = await _ensure_youtube_cookies()
        
        info_cmd = ["yt-dlp", "--dump-json", "--no-download", "--no-playlist"]
        if cookies_path:
            info_cmd.extend(["--cookies", cookies_path])
        info_cmd.append(video_url)
        title = video_url
        info_proc = await asyncio.create_subprocess_exec(
            *info_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        info_out, _ = await info_proc.communicate()
        if info_out:
            try:
                info = json.loads(info_out.decode())
                if info.get("is_live") or info.get("was_live"):
                    log.warning(f"[video.roku] Skipping livestream: {video_url}")
                    return None, None
                title = info.get("title", video_url)
            except json.JSONDecodeError:
                pass
        
        dl_cmd = ["yt-dlp", "-f", "22/18/best[ext=mp4][height<=720]", "--no-playlist", "-o", tmp_path]
        if cookies_path:
            dl_cmd.extend(["--cookies", cookies_path])
        dl_cmd.append(video_url)
        proc = await asyncio.create_subprocess_exec(
            *dl_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        while proc.returncode is None:
            await asyncio.sleep(0.5)
            if os.path.exists(tmp_path):
                current_size = os.path.getsize(tmp_path)
                if current_size >= threshold:
                    log.info(f"[video.roku] Progressive threshold reached: {current_size / 1024 / 1024:.1f} MB / {threshold / 1024 / 1024:.0f} MB — returning control to caller")
                    asyncio.create_task(_wait_for_download(proc, tmp_path, media_id))
                    return media_id, title
        
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error(f"[video.roku] yt-dlp download failed: {stderr.decode()[:300]}")
            return None, None
        
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            log.error(f"[video.roku] Download produced empty file")
            return None, None
        
        file_size = os.path.getsize(tmp_path)
        log.info(f"[video.roku] Downloaded {media_id} ({file_size / 1024 / 1024:.1f} MB)")
        return media_id, title
        
    except Exception as e:
        log.error(f"[video.roku] Download failed: {e}", exc_info=True)
        try:
            os.remove(tmp_path)
        except:
            pass
        return None, None


async def _wait_for_download(proc, tmp_path: str, media_id: str):
    """Background task to wait for download completion and log result."""
    try:
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.error(f"[video.roku] Background download failed for {media_id}: {stderr.decode()[:200]}")
        else:
            file_size = os.path.getsize(tmp_path) if os.path.exists(tmp_path) else 0
            log.info(f"[video.roku] Background download complete for {media_id} ({file_size / 1024 / 1024:.1f} MB)")
    except Exception as e:
        log.error(f"[video.roku] Background download monitor failed: {e}")


async def download_video_for_roku(video_url: str) -> tuple[str | None, str | None]:
    """
    Download video optimized for Roku using pre-generated H.264/AAC formats.
    Uses format 22 (720p) or 18 (360p) which are single-file containers
    that require no local muxing, ensuring immediate streaming readiness.
    """
    import uuid
    
    media_id = f"vid-roku-{uuid.uuid4().hex[:8]}"
    tmp_path = os.path.join(TEMP_VIDEO_DIR, f"{media_id}.mp4")
    
    try:
        cookies_path = await _ensure_youtube_cookies()
        
        info_cmd = ["yt-dlp", "--dump-json", "--no-download", "--no-playlist"]
        if cookies_path:
            info_cmd.extend(["--cookies", cookies_path])
        info_cmd.append(video_url)
        info_proc = await asyncio.create_subprocess_exec(
            *info_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        info_out, info_err = await info_proc.communicate()
        if info_out:
            try:
                info = json.loads(info_out.decode())
                if info.get("is_live") or info.get("was_live"):
                    log.warning(f"[video.roku] Skipping livestream: {video_url}")
                    return None, None
                title = info.get("title", video_url)
            except json.JSONDecodeError:
                title = video_url
        else:
            title = video_url
        
        dl_cmd = ["yt-dlp", "-f", "22/18/best[ext=mp4][height<=720]", "--no-playlist", "-o", tmp_path]
        if cookies_path:
            dl_cmd.extend(["--cookies", cookies_path])
        dl_cmd.append(video_url)
        proc = await asyncio.create_subprocess_exec(
            *dl_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            log.error(f"[video.roku] yt-dlp download failed: {stderr.decode()[:300]}")
            return None, None
        
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            log.error(f"[video.roku] Download produced empty file")
            return None, None
        
        file_size = os.path.getsize(tmp_path)
        log.info(f"[video.roku] Downloaded {media_id} ({file_size / 1024 / 1024:.1f} MB)")
        
        return media_id, title
        
    except Exception as e:
        log.error(f"[video.roku] Download failed: {e}", exc_info=True)
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

    # Step 2: Check if device is Roku
    from handlers import roku as roku_handler
    is_roku = await roku_handler.is_roku_device(ctx.ha_url, ctx.ha_token, full_entity_id)
    if is_roku:
        log.info(f"[video/play] Detected Roku device, using Roku video handler")
        # Wake device in parallel with download
        wake_task = asyncio.create_task(roku_handler.roku_wake_device(ctx.ha_url, ctx.ha_token, full_entity_id))
        media_id, title = await download_video_progressive(video_url)
        if not media_id:
            return ExecutionResult(
                status="FAILURE",
                message=f"Could not download video from {video_url}.",
                service="video_play",
            )
        await wake_task
        from config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            return ExecutionResult(
                status="FAILURE",
                message="EXECUTION_EXTERNAL_HOST is not configured. Cannot stream video to Roku.",
                service="video_play",
            )
        stream_url = f"http://{EXECUTION_EXTERNAL_HOST}:8888/media/{media_id}"
        return await roku_handler.roku_play_video(
            ctx.ha_url, ctx.ha_token, full_entity_id, stream_url, title or req.query,
        )

    # Step 3: Download the video to disk with progressive streaming (non-Roku)
    media_id, title = await download_video_progressive(video_url)
    if not media_id:
        return ExecutionResult(
            status="FAILURE",
            message=f"Could not download video from {video_url}.",
            service="video_play",
        )

    # Step 4: Build the local media URL for HA to stream
    def get_public_host():
        from config import EXECUTION_EXTERNAL_HOST
        if not EXECUTION_EXTERNAL_HOST:
            raise RuntimeError("EXECUTION_EXTERNAL_HOST is not set. Cannot build media URL.")
        return EXECUTION_EXTERNAL_HOST
    
    public_host = get_public_host()
    media_url = f"http://{public_host}:8888/media/{media_id}"
    log.info(f"[video/play] Casting URL: {media_url}")

    # Step 5: Power on the device
    state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    if state and state.get("state") == "off":
        log.info(f"[video/play] Device {full_entity_id} is off. Turning on...")
        await ha_client.call_service(ctx.ha_url, ctx.ha_token, "media_player", "turn_on", full_entity_id)
        await asyncio.sleep(2)

    # Step 6: Cast the video URL
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
