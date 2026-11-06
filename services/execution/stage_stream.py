# services/execution/stage_stream.py
"""Live HLS video of a web page, for screens that cannot run a browser.

A Roku has no web engine, so the only way to put a live game board on it is
to send it video. This renders the page in headless Chromium (Playwright,
already in this image), takes the compositor's frames over CDP
``Page.startScreencast``, and encodes them with ffmpeg into a short rolling
HLS playlist that Roku's Media Assistant (or any HLS-capable player) plays.

Design points:

- **Frames are paced, not forwarded.** The screencast only emits a frame when
  the page repaints, and an encoder fed irregularly produces a stream whose
  clock drifts. A writer loop re-sends the latest frame at a fixed rate.
- **A silent audio track is muxed in.** Several TV players refuse, or stall
  on, a video-only HLS stream.
- **Streams end on their own.** One that no player has fetched for
  ``IDLE_TIMEOUT_S`` is stopped, as is any stream older than ``MAX_AGE_S``, so
  a TV that was switched off never leaves Chromium and ffmpeg running.
- **The stream id is the capability.** It is a random token; the media server
  serves ``/stage/<sid>/<file>`` only for live ids and plain file names, since
  a TV fetches header-less and cannot present any other credential.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("execution.stage_stream")

STREAM_ROOT = Path(os.getenv("STAGE_STREAM_DIR") or (Path(tempfile.gettempdir()) / "tv_stage"))
WIDTH = 1280
HEIGHT = 720
FPS = 8
HLS_SEGMENT_S = 2
HLS_LIST_SIZE = 6
#: A TV that is playing re-fetches the playlist every segment; this much
#: silence means it stopped (or was switched off).
IDLE_TIMEOUT_S = 90.0
#: Long enough for any party game; a stream is never meant to run unattended.
MAX_AGE_S = 4 * 3600.0
#: How long ``start`` waits for the first playlist before handing out a URL
#: a player would 404 on.
READY_TIMEOUT_S = 20.0

_SAFE_NAME = re.compile(r"^(index\.m3u8|seg\d{1,8}\.ts)$")


@dataclass
class StageStream:
    sid: str
    url: str
    directory: Path
    started_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    frame: bytes = b""
    proc: asyncio.subprocess.Process | None = None
    tasks: list[asyncio.Task] = field(default_factory=list)
    browser: object | None = None
    playwright: object | None = None
    error: str = ""

    @property
    def playlist(self) -> Path:
        return self.directory / "index.m3u8"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None


_streams: dict[str, StageStream] = {}
_lock = asyncio.Lock()
_reaper: asyncio.Task | None = None


def ffmpeg_command(directory: Path, *, fps: int = FPS, width: int = WIDTH, height: int = HEIGHT) -> list[str]:
    """The encoder: paced JPEG frames on stdin + silence -> rolling H.264/AAC HLS."""
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map", "0:v", "-map", "1:a",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-r", str(fps), "-g", str(fps * HLS_SEGMENT_S), "-keyint_min", str(fps * HLS_SEGMENT_S),
        "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "48k",
        # The silent track never ends; without this ffmpeg outlives its frames.
        "-shortest",
        "-f", "hls", "-hls_time", str(HLS_SEGMENT_S), "-hls_list_size", str(HLS_LIST_SIZE),
        "-hls_flags", "delete_segments+independent_segments+omit_endlist",
        "-hls_segment_filename", str(directory / "seg%05d.ts"),
        str(directory / "index.m3u8"),
    ]


def get_stream(sid: str) -> StageStream | None:
    return _streams.get(sid)


def resolve_file(sid: str, name: str) -> tuple[Path, str] | None:
    """Map a request for ``/stage/<sid>/<name>`` to a file, or None.

    Touches the stream so a TV that is still watching keeps it alive.
    """
    stream = _streams.get(sid)
    if stream is None or not _SAFE_NAME.match(name or ""):
        return None
    path = stream.directory / name
    if not path.is_file():
        return None
    stream.last_access = time.time()
    media_type = "application/vnd.apple.mpegurl" if name.endswith(".m3u8") else "video/mp2t"
    return path, media_type


def list_streams() -> list[dict]:
    now = time.time()
    return [
        {
            "sid": s.sid,
            "url": s.url,
            "alive": s.alive(),
            "age_s": round(now - s.started_at, 1),
            "idle_s": round(now - s.last_access, 1),
            "error": s.error,
        }
        for s in _streams.values()
    ]


async def start(url: str, *, ready_timeout: float = READY_TIMEOUT_S) -> StageStream:
    """Start (or reuse) a live stream of ``url`` and wait for its first playlist."""
    async with _lock:
        for existing in _streams.values():
            if existing.url == url and existing.alive():
                existing.last_access = time.time()
                return existing
        sid = secrets.token_urlsafe(18)
        directory = STREAM_ROOT / sid
        directory.mkdir(parents=True, exist_ok=True)
        stream = StageStream(sid=sid, url=url, directory=directory)
        _streams[sid] = stream
        _ensure_reaper()

    try:
        await _launch(stream)
    except Exception as exc:
        stream.error = str(exc) or exc.__class__.__name__
        await stop(sid)
        raise RuntimeError(f"Could not start the live stream: {stream.error}") from exc

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if stream.playlist.is_file() and stream.playlist.stat().st_size > 0:
            stream.last_access = time.time()
            return stream
        if not stream.alive():
            break
        await asyncio.sleep(0.25)
    err = stream.error or "the encoder produced no playlist"
    await stop(sid)
    raise RuntimeError(f"Could not start the live stream: {err}")


async def _launch(stream: StageStream) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed")
    from playwright.async_api import async_playwright  # pyright: ignore[reportMissingImports]

    pw = await async_playwright().start()
    stream.playwright = pw
    browser = await pw.chromium.launch(
        args=["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage", "--no-sandbox"],
    )
    stream.browser = browser
    page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
    await page.goto(stream.url, wait_until="domcontentloaded", timeout=20000)
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=5000)
    stream.frame = await page.screenshot(type="jpeg", quality=80)

    cdp = await page.context.new_cdp_session(page)

    async def _on_frame(params: dict) -> None:
        with contextlib.suppress(Exception):
            stream.frame = base64.b64decode(params.get("data") or "")
        with contextlib.suppress(Exception):
            await cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})

    cdp.on("Page.screencastFrame", lambda params: asyncio.ensure_future(_on_frame(params)))
    await cdp.send(
        "Page.startScreencast",
        {"format": "jpeg", "quality": 80, "maxWidth": WIDTH, "maxHeight": HEIGHT, "everyNthFrame": 1},
    )

    stream.proc = await asyncio.create_subprocess_exec(
        *ffmpeg_command(stream.directory),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    stream.tasks.append(asyncio.create_task(_pace_frames(stream)))
    stream.tasks.append(asyncio.create_task(_collect_stderr(stream)))
    log.info("[stage_stream] %s streaming %s", stream.sid, stream.url)


async def _pace_frames(stream: StageStream, fps: int = FPS) -> None:
    proc = stream.proc
    if proc is None or proc.stdin is None:
        return
    interval = 1.0 / fps
    next_at = time.monotonic()
    try:
        while proc.returncode is None:
            if stream.frame:
                proc.stdin.write(stream.frame)
                await proc.stdin.drain()
            next_at += interval
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
    except (BrokenPipeError, ConnectionResetError):
        pass
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        stream.error = stream.error or f"frame writer: {exc}"


async def _collect_stderr(stream: StageStream) -> None:
    proc = stream.proc
    if proc is None or proc.stderr is None:
        return
    with contextlib.suppress(Exception):
        data = await proc.stderr.read()
        text = data.decode("utf-8", "replace").strip()
        if text:
            stream.error = stream.error or text[-400:]
            log.warning("[stage_stream] %s ffmpeg: %s", stream.sid, text[-400:])


async def stop(sid: str) -> bool:
    stream = _streams.pop(sid, None)
    if stream is None:
        return False
    for task in stream.tasks:
        task.cancel()
    proc = stream.proc
    if proc is not None and proc.returncode is None:
        with contextlib.suppress(Exception):
            if proc.stdin is not None:
                proc.stdin.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=3)
        if proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
    with contextlib.suppress(Exception):
        if stream.browser is not None:
            await stream.browser.close()  # type: ignore[attr-defined]
    with contextlib.suppress(Exception):
        if stream.playwright is not None:
            await stream.playwright.stop()  # type: ignore[attr-defined]
    shutil.rmtree(stream.directory, ignore_errors=True)
    log.info("[stage_stream] %s stopped", sid)
    return True


async def stop_url(url: str) -> int:
    sids = [s.sid for s in list(_streams.values()) if s.url == url]
    for sid in sids:
        await stop(sid)
    return len(sids)


def expired(stream: StageStream, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return (
        not stream.alive()
        or now - stream.last_access > IDLE_TIMEOUT_S
        or now - stream.started_at > MAX_AGE_S
    )


async def reap_once() -> int:
    stale = [s.sid for s in list(_streams.values()) if expired(s)]
    for sid in stale:
        await stop(sid)
    return len(stale)


def _ensure_reaper() -> None:
    global _reaper
    if _reaper is not None and not _reaper.done():
        return

    async def _loop() -> None:
        while _streams:
            await asyncio.sleep(15)
            with contextlib.suppress(Exception):
                await reap_once()

    _reaper = asyncio.create_task(_loop())
