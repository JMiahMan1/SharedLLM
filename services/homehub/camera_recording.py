"""Motion recording for cameras that cannot record themselves (the ``camera_recording`` integration).

Reolink cameras keep their own recordings (on their card or their hub). A plain
camera (an ESPHome ESP32 camera, an MJPEG camera such as a 3D printer's) has
nowhere to keep anything, so the hub records for it, with the settings a
Reolink camera has:

- it takes single pictures from the camera (``async_camera_image``, the camera
  contract in ``drivers/mjpeg.py``), ``fps`` a second, and spots motion by
  comparing each with the one before and a slowly updated background
  (:class:`MotionDetector`). Never the camera's MJPEG stream: small camera
  servers (an ESP32's) often serve one stream at a time, and holding it would
  leave the family's live view without a picture;
- a clip starts with the seconds before the motion (``pre_s``), runs while
  motion goes on, and ends ``post_s`` after it stops (or at ``max_s``);
- the frames, with their real timing, become an H.264 MP4 (ffmpeg), so every
  browser plays it;
- clips go to Nextcloud over WebDAV, one folder per camera and local day
  (``<folder>/<camera>/<yyyy-mm-dd>/<yyyymmdd-hhmmss>_<seconds>s_motion.mp4``),
  and days older than ``retention_days`` are deleted;
- motion is also a ``binary_sensor`` (device class ``motion``), so automations
  can announce it, like a Reolink camera's motion sensor;
- the clips appear in the camera's Recordings tab, through the same contract
  Reolink's driver keeps (``async_list_recordings``/``async_open_recording``).

A clip that cannot be uploaded is kept on the hub (``<data>/camera-recordings/
pending``) and uploaded later; until then it is still listed and playable.
Battery cameras are never watched (every picture wakes them).

Everything is set at runtime, through ``/hub/camera-recording`` (the Cameras
page's "Motion recording" panel):

``entry.data``
    ``url``, ``username``, ``password`` - the Nextcloud account (an app
    password), stored encrypted like every integration's credentials.

``entry.options``
    ``folder`` - the Nextcloud folder clips go into (default ``Jarvis/Cameras``).
    ``cameras`` - per camera entity id: ``enabled``, ``sensitivity`` (1-100),
    ``pre_s``, ``post_s``, ``max_s``, ``fps`` (frames a second watched and
    recorded), ``retention_days``, ``schedule`` (``{"from": "HH:MM", "to":
    "HH:MM"}`` local time, or none for always).
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from zoneinfo import ZoneInfo

import aiohttp

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, Hub, HubError, camera_rotation

log = logging.getLogger("homehub.camera_recording")

DOMAIN = "camera_recording"
DEFAULT_FOLDER = "Jarvis/Cameras"
#: Per-camera defaults: what a Reolink camera ships with, near enough.
DEFAULTS: dict[str, Any] = {
    "enabled": False, "sensitivity": 60, "pre_s": 3, "post_s": 10, "max_s": 120, "fps": 4,
    "retention_days": 14, "schedule": None,
}
#: (low, high) for each number a camera setting takes.
LIMITS: dict[str, tuple[int, int]] = {
    "sensitivity": (1, 100), "pre_s": (0, 10), "post_s": (2, 60), "max_s": (10, 600), "fps": (1, 10),
    "retention_days": (1, 365),
}
_CAMERA_ID = re.compile(r"camera\.[a-z0-9_]+")
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
#: ``<yyyymmdd-hhmmss>_<seconds>s_<trigger>[_r<turn>].mp4``; ``_r90`` marks a clip saved already turned.
_CLIP_NAME = re.compile(r"(\d{8})-(\d{6})_(\d{1,4})s_([a-z]+)(?:_r(90|180|270))?\.mp4")
#: ffmpeg filters that turn a picture clockwise.
_TURN = {0: "", 90: "transpose=clock,", 180: "hflip,vflip,", 270: "transpose=cclock,"}
_DAY_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_read=60)
#: How often the driver re-checks which cameras record, uploads waiting clips and prunes old days.
SYNC_S = 60.0
PRUNE_EVERY_S = 3600.0
#: Waits between attempts when a camera stops giving pictures.
RETRY_S = (2.0, 5.0, 15.0, 30.0, 60.0)
FFMPEG = "ffmpeg"


# -- settings -------------------------------------------------------------------

def camera_settings(options: dict[str, Any], entity_id: str) -> dict[str, Any]:
    """A camera's settings with the defaults filled in."""
    cams = options.get("cameras")
    own = cams.get(entity_id) if isinstance(cams, dict) else None
    return {**DEFAULTS, **(own if isinstance(own, dict) else {})}


def _number(eid: str, key: str, value: Any) -> int:
    low, high = LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value \
            or not low <= value <= high:
        raise ValueError(f"{eid}: {key} must be a whole number from {low} to {high}")
    return int(value)


def validate_options(patch: dict[str, Any]) -> dict[str, Any]:
    """A settings change, checked; raises ``ValueError`` with words for people."""
    out: dict[str, Any] = {}
    if "folder" in patch:
        folder = str(patch["folder"] or "").strip().strip("/")
        parts = folder.split("/") if folder else []
        if not parts or any(p in ("", ".", "..") or "\\" in p for p in parts) or len(folder) > 200:
            raise ValueError("The folder must be a Nextcloud folder path such as Jarvis/Cameras")
        out["folder"] = folder
    if "cameras" in patch:
        cams = patch["cameras"]
        if not isinstance(cams, dict):
            raise ValueError("cameras must be an object of camera settings")
        clean: dict[str, Any] = {}
        for eid, cam in cams.items():
            if not _CAMERA_ID.fullmatch(str(eid)):
                raise ValueError(f"{eid!r} is not a camera")
            if cam is None:
                clean[eid] = None
                continue
            if not isinstance(cam, dict):
                raise ValueError(f"The settings of {eid} must be an object")
            unknown = sorted(set(cam) - set(DEFAULTS))
            if unknown:
                raise ValueError(f"{eid}: unknown settings {', '.join(unknown)}")
            item: dict[str, Any] = {}
            if "enabled" in cam:
                item["enabled"] = bool(cam["enabled"])
            for key in LIMITS:
                if key in cam:
                    item[key] = _number(eid, key, cam[key])
            if "schedule" in cam:
                item["schedule"] = _schedule(eid, cam["schedule"])
            merged = {**DEFAULTS, **item}
            if merged["post_s"] >= merged["max_s"]:
                raise ValueError(f"{eid}: a clip's longest length must be more than the time kept after motion")
            clean[eid] = item
        out["cameras"] = clean
    return out


def _schedule(eid: str, value: Any) -> dict[str, str] | None:
    if value in (None, "", {}):
        return None
    if not isinstance(value, dict) or not all(_HHMM.fullmatch(str(value.get(k) or "")) for k in ("from", "to")):
        raise ValueError(f"{eid}: the schedule must be from and to times like 22:00 and 06:00")
    if value["from"] == value["to"]:
        raise ValueError(f"{eid}: the schedule's start and end must differ (leave it empty to record always)")
    return {"from": value["from"], "to": value["to"]}


def merge_options(current: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """``current`` with a validated change applied; a camera's settings are merged key by key, None removes them."""
    out = dict(current)
    for key, value in change.items():
        if key != "cameras":
            out[key] = value
            continue
        cams = dict(out.get("cameras") or {})
        for eid, cam in value.items():
            if cam is None:
                cams.pop(eid, None)
            else:
                cams[eid] = {**(cams.get(eid) or {}), **cam}
        out["cameras"] = cams
    return out


def in_schedule(schedule: dict[str, str] | None, now: datetime) -> bool:
    """Whether ``now`` (local) is inside a ``from``-``to`` window; a window may cross midnight."""
    if not schedule:
        return True
    hm = now.strftime("%H:%M")
    start, end = schedule["from"], schedule["to"]
    return start <= hm < end if start < end else hm >= start or hm < end


# -- motion ---------------------------------------------------------------------

#: The size frames are compared at: small enough to be cheap, large enough to see a person at the far wall.
ANALYSIS_SIZE = (96, 72)
#: A change in the whole picture's brightness this large is the lights or the night mode, not motion.
LIGHT_SHIFT = 28.0
#: How fast the background follows the picture (per analysed frame).
BACKGROUND_RATE = 0.08
#: Frames in a row that must show motion before it counts (one noisy frame does not).
MOTION_FRAMES = 2


def thresholds(sensitivity: int) -> tuple[float, float]:
    """(how much a pixel must change, what share of the picture must change) for a 1-100 sensitivity."""
    s = max(1, min(100, sensitivity)) / 100.0
    pixel = 14.0 + 30.0 * (1.0 - s)
    area = 0.002 + 0.10 * (1.0 - s) ** 2
    return pixel, area


def gray_frame(jpeg: bytes) -> Any:
    """A JPEG as a small grey float array (Pillow decodes it at a reduced size directly)."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(jpeg))
    im.draft("L", (ANALYSIS_SIZE[0] * 2, ANALYSIS_SIZE[1] * 2))
    im = im.convert("L").resize(ANALYSIS_SIZE, Image.BILINEAR)
    return np.asarray(im, dtype=np.float32)


class MotionDetector:
    """Frame-by-frame motion; ``feed`` says whether there is motion now.

    A pixel counts as moving when it differs from the previous frame *and* from a slowly updated
    background. The first alone flickers with sensor noise on a still scene; the second alone keeps
    a "ghost" for many seconds after something leaves (the background still remembers it), which
    would make every clip run long.
    """

    def __init__(self, sensitivity: int) -> None:
        self.pixel, self.area = thresholds(sensitivity)
        self.background: Any = None
        self.previous: Any = None
        self.hits = 0
        self.changed = 0.0

    def _restart(self, gray: Any) -> bool:
        self.background, self.previous, self.hits, self.changed = gray, gray, 0, 0.0
        return False

    def feed(self, gray: Any) -> bool:
        import numpy as np

        bg, prev = self.background, self.previous
        if bg is None or bg.shape != gray.shape:
            return self._restart(gray)
        if abs(float(gray.mean()) - float(prev.mean())) > LIGHT_SHIFT:
            # The lights came on, or the camera switched to night mode: start again from this picture.
            return self._restart(gray)
        moving = (np.abs(gray - prev) > self.pixel) & (np.abs(gray - bg) > self.pixel)
        self.changed = float(moving.mean())
        self.background = bg * (1.0 - BACKGROUND_RATE) + gray * BACKGROUND_RATE
        self.previous = gray
        self.hits = self.hits + 1 if self.changed >= self.area else 0
        return self.hits >= MOTION_FRAMES


# -- clips ----------------------------------------------------------------------

def clip_name(start: datetime, seconds: float, trigger: str = "motion", rotation: int = 0) -> str:
    turned = f"_r{rotation}" if rotation else ""
    return f"{start.strftime('%Y%m%d-%H%M%S')}_{max(1, round(seconds))}s_{trigger}{turned}.mp4"


def parse_clip_name(name: str, tz: ZoneInfo) -> tuple[datetime, int, str, int] | None:
    """(start, seconds, trigger, the turn the clip was saved with), or None for any other name."""
    m = _CLIP_NAME.fullmatch(name)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=tz)
    except ValueError:
        return None
    return start, int(m.group(3)), m.group(4), int(m.group(5) or 0)


class Clip:
    """One recording being made: its frames go to a work folder as they come, then ffmpeg makes the MP4."""

    def __init__(self, work: Path, started: float) -> None:
        self.dir = work / uuid.uuid4().hex
        self.dir.mkdir(parents=True)
        self.started = started
        self.times: list[float] = []

    def add(self, at: float, jpeg: bytes) -> None:
        (self.dir / f"{len(self.times):06d}.jpg").write_bytes(jpeg)
        self.times.append(at)

    @property
    def seconds(self) -> float:
        return (self.times[-1] - self.times[0]) if len(self.times) > 1 else 0.0

    def concat_list(self, last_frame_s: float) -> str:
        """ffmpeg's concat list: each frame shown until the next came, so the clip keeps real time."""
        lines = []
        for i, at in enumerate(self.times):
            nxt = self.times[i + 1] if i + 1 < len(self.times) else at + last_frame_s
            lines += [f"file '{i:06d}.jpg'", f"duration {max(0.01, nxt - at):.3f}"]
        lines.append(f"file '{len(self.times) - 1:06d}.jpg'")  # the concat demuxer drops the last duration otherwise
        return "\n".join(lines) + "\n"

    async def encode(self, out: Path, fps: int, rotation: int = 0) -> None:
        """The MP4, turned ``rotation`` degrees clockwise (the camera's turn, so the file is upright anywhere)."""
        if not self.times:
            raise RuntimeError("a clip with no frames")
        (self.dir / "frames.txt").write_text(self.concat_list(1.0 / fps))
        proc = await asyncio.create_subprocess_exec(
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(self.dir / "frames.txt"), "-vf", f"{_TURN[rotation]}scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-movflags", "+faststart",
            str(out), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {err.decode(errors='replace').strip()[-300:]}")

    def discard(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


# -- Nextcloud ------------------------------------------------------------------

class NextcloudError(HubError):
    """Nextcloud refused or failed; ``status`` is what the hub answers with."""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class DavItem:
    name: str
    is_dir: bool
    size: int | None = None


class Nextcloud:
    """The few WebDAV calls the recorder needs, as one Nextcloud user."""

    def __init__(self, url: str, username: str, password: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            raise NextcloudError("The Nextcloud address must be http(s)://host, with no user name in it", 400)
        self.root = f"{url.rstrip('/')}/remote.php/dav/files/{quote(username, safe='')}"
        self.authorization = aiohttp.encode_basic_auth(username, password)
        self._session: aiohttp.ClientSession | None = None

    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=_HTTP_TIMEOUT, headers={
                "User-Agent": "JarvisHomeHub/1.0", "Authorization": self.authorization})
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    def url(self, path: str) -> str:
        return f"{self.root}/{quote(path.strip('/'))}"

    async def _check(self, resp: aiohttp.ClientResponse, what: str, ok: tuple[int, ...]) -> None:
        if resp.status in ok:
            return
        if resp.status == 401:
            raise NextcloudError("Nextcloud did not accept the login", 401)
        raise NextcloudError(f"Nextcloud answered HTTP {resp.status} to {what}", 502)

    async def ensure_folder(self, path: str) -> None:
        """Make the folder and every folder above it (an existing one is fine)."""
        so_far = ""
        for part in [p for p in path.strip("/").split("/") if p]:
            so_far = f"{so_far}/{part}" if so_far else part
            async with self.session().request("MKCOL", self.url(so_far)) as resp:
                await self._check(resp, f"making {so_far}", (201, 405))

    async def list(self, path: str) -> list[DavItem] | None:
        """The folder's entries, or None when it does not exist."""
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
                '<d:getcontentlength/></d:prop></d:propfind>')
        async with self.session().request("PROPFIND", self.url(path), data=body,
                                          headers={"Depth": "1", "Content-Type": "application/xml"}) as resp:
            if resp.status == 404:
                return None
            await self._check(resp, f"listing {path}", (207,))
            text = await resp.text()
        ns = {"d": "DAV:"}
        items: list[DavItem] = []
        here = urlsplit(self.url(path)).path.rstrip("/")
        for r in ET.fromstring(text).findall("d:response", ns):
            href = urlsplit(unquote(r.findtext("d:href", "", ns))).path.rstrip("/")
            if href == unquote(here):
                continue
            is_dir = r.find(".//d:resourcetype/d:collection", ns) is not None
            size = r.findtext(".//d:getcontentlength", None, ns)
            items.append(DavItem(href.rsplit("/", 1)[-1], is_dir, int(size) if size and size.isdigit() else None))
        return items

    async def upload(self, path: str, local: Path) -> None:
        folder = path.rsplit("/", 1)[0]
        await self.ensure_folder(folder)
        with local.open("rb") as fh:
            async with self.session().put(self.url(path), data=fh,
                                          headers={"Content-Type": "video/mp4",
                                                   "Content-Length": str(local.stat().st_size)}) as resp:
                await self._check(resp, f"uploading {path}", (200, 201, 204))

    async def delete(self, path: str) -> None:
        async with self.session().delete(self.url(path)) as resp:
            await self._check(resp, f"deleting {path}", (200, 204, 404))

    async def open(self, path: str, range_header: str | None) -> aiohttp.ClientResponse:
        headers = {"Range": range_header} if range_header else {}
        resp = await self.session().get(self.url(path), headers=headers)
        if resp.status not in (200, 206):
            resp.release()
            if resp.status == 404:
                raise NextcloudError("That recording is gone", 404)
            await self._check(resp, f"reading {path}", (200, 206))
        return resp


# -- one camera -------------------------------------------------------------------

@dataclass
class CameraStatus:
    watching: bool = False
    motion: bool = False
    recording: bool = False
    last_motion: float | None = None
    last_clip: str | None = None
    error: str | None = None
    clips: int = 0
    frames: int = 0
    note: str | None = None
    _published: tuple = field(default=(), repr=False)


class CameraWatcher:
    """Watches one camera: frames in, motion out, clips to the recorder."""

    def __init__(self, recorder: CameraRecordingDriver, entity_id: str, settings: dict[str, Any]) -> None:
        self.recorder = recorder
        self.entity_id = entity_id
        self.settings = settings
        self.status = CameraStatus()
        self.detector = MotionDetector(settings["sensitivity"])
        self.buffer: deque[tuple[float, bytes]] = deque()
        self.clip: Clip | None = None
        self.last_motion = 0.0
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.run(), name=f"camera-recording {self.entity_id}")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        if self.clip is not None:
            await self.finish()  # keep what was being recorded
        self.status.watching = False

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                async for jpeg in self.frames():
                    attempt = 0
                    await self.on_frame(time.time(), jpeg)
                raise DeviceUnavailable("the camera's stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status.watching, self.status.error = False, str(exc) or type(exc).__name__
                self.recorder.publish_motion(self, available=False)
                if self.clip is not None:
                    await self.finish()
                wait = RETRY_S[min(attempt, len(RETRY_S) - 1)]
                attempt += 1
                log.info("%s: no pictures (%s); trying again in %.0f s", self.entity_id, self.status.error, wait)
                await asyncio.sleep(wait)

    async def frames(self) -> AsyncIterator[bytes]:
        """Single pictures, ``fps`` a second (see the module docstring for why never the stream)."""
        ent, driver = self.recorder.camera(self.entity_id)
        gap = 1.0 / self.settings["fps"]
        while True:
            began = time.monotonic()
            yield await driver.async_camera_image(ent)
            await asyncio.sleep(max(0.0, gap - (time.monotonic() - began)))

    async def on_frame(self, at: float, jpeg: bytes) -> None:
        s = self.settings
        self.status.watching, self.status.error = True, None
        self.status.frames += 1
        self.buffer.append((at, jpeg))
        while self.buffer and at - self.buffer[0][0] > s["pre_s"]:
            self.buffer.popleft()
        local = datetime.fromtimestamp(at, self.recorder.tz)
        try:
            gray = await asyncio.to_thread(gray_frame, jpeg)
        except Exception:
            return  # a torn picture: skip it, the next one will do
        moving = self.detector.feed(gray)
        if moving:
            self.last_motion = at
            self.status.last_motion = at
        active = moving or (self.last_motion and at - self.last_motion < 1.5)
        self.status.motion = bool(active)
        self.recorder.publish_motion(self, available=True)
        if self.clip is not None:
            self.clip.add(at, jpeg)
            if at - self.last_motion >= s["post_s"] or at - self.clip.started >= s["max_s"]:
                await self.finish()
        elif moving and in_schedule(s.get("schedule"), local):
            self.clip = Clip(self.recorder.work_dir, self.buffer[0][0])
            for t, frame in self.buffer:
                self.clip.add(t, frame)
            self.status.recording = True

    async def finish(self) -> None:
        clip, self.clip = self.clip, None
        self.status.recording = False
        if clip is None:
            return
        if len(clip.times) < 2:
            clip.discard()
            return
        self.recorder.spawn(self.recorder.save_clip(self.entity_id, clip, self.settings["fps"]))


# -- the integration ----------------------------------------------------------------

class CameraRecordingDriver(Driver):
    """Records motion clips from the cameras its options name, into Nextcloud."""

    platform = DOMAIN
    poll_interval = SYNC_S

    def __init__(self, hub: Hub, entry: Any) -> None:
        super().__init__(hub, entry)
        data = entry.data or {}
        self.nextcloud = Nextcloud(str(data.get("url") or ""), str(data.get("username") or ""),
                                   str(data.get("password") or ""))
        self.secrets = [s for s in (str(data.get("password") or ""),) if s]
        self.tz = ZoneInfo(hub.time_zone or "UTC")
        base = hub.data_dir
        if not base:
            raise DeviceUnavailable("The hub has no data folder for recordings")
        self.base = Path(base) / "camera-recordings"
        self.work_dir = self.base / "work"
        self.pending_dir = self.base / "pending"
        self.watchers: dict[str, CameraWatcher] = {}
        self.uploading: set[str] = set()
        self.last_error: str | None = None
        self.last_prune = 0.0
        self._tasks: set[asyncio.Task] = set()

    # -- lifecycle ------------------------------------------------------------
    @property
    def folder(self) -> str:
        return str((self.entry.options or {}).get("folder") or DEFAULT_FOLDER).strip("/")

    async def async_setup(self) -> None:
        if not shutil.which(FFMPEG):
            raise DeviceUnavailable("ffmpeg is not installed in the hub's image")
        try:
            await self.nextcloud.ensure_folder(self.folder)
        except (aiohttp.ClientError, TimeoutError) as exc:
            await self.nextcloud.close()
            raise DeviceUnavailable(f"Nextcloud did not answer: {type(exc).__name__}") from None
        except NextcloudError:
            await self.nextcloud.close()
            raise
        shutil.rmtree(self.work_dir, ignore_errors=True)  # half-made clips from before a restart
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        await self.sync()

    async def async_poll(self) -> None:
        await self.sync()
        await self.upload_pending()
        if time.monotonic() - self.last_prune >= PRUNE_EVERY_S:
            self.last_prune = time.monotonic()
            await self.prune()

    async def async_unload(self) -> None:
        for w in list(self.watchers.values()):
            await w.stop()
        self.watchers.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.nextcloud.close()

    def spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- cameras --------------------------------------------------------------
    def camera(self, entity_id: str) -> tuple[EntityEntry, Any]:
        ent = self.hub.entities.get(entity_id)
        if ent is None or ent.domain != "camera" or ent.disabled_by:
            raise DeviceUnavailable(f"{entity_id} is not a camera")
        driver = self.hub.drivers.get(ent.config_entry_id or "")
        if driver is None or not hasattr(driver, "async_camera_image"):
            raise DeviceUnavailable(f"{entity_id} is not running")
        return ent, driver

    def eligibility(self, entity_id: str) -> str | None:
        return ineligible(self.hub, entity_id)

    def wanted(self) -> dict[str, dict[str, Any]]:
        cams = (self.entry.options or {}).get("cameras") or {}
        out = {}
        for eid in cams:
            settings = camera_settings(self.entry.options or {}, eid)
            if settings["enabled"] and self.eligibility(eid) is None:
                out[eid] = settings
        return out

    async def sync(self) -> None:
        """Start, restart (settings changed) or stop each camera's watcher to match the options."""
        wanted = self.wanted()
        for eid in list(self.watchers):
            w = self.watchers[eid]
            if eid not in wanted or w.settings != wanted[eid]:
                await w.stop()
                del self.watchers[eid]
                if eid not in wanted:
                    self.publish_motion(w, available=False)
        for eid, settings in wanted.items():
            if eid not in self.watchers:
                w = CameraWatcher(self, eid, settings)
                self.watchers[eid] = w
                self.motion_entity(eid)
                w.start()

    # -- the motion sensors -----------------------------------------------------
    def motion_entity(self, camera_id: str) -> EntityEntry:
        from services.homehub.flows import free_entity_id

        uid = f"{camera_id}:motion"
        for e in self.hub.entities.values():
            if e.config_entry_id == self.entry.entry_id and e.unique_id == uid:
                return e
        cam = self.hub.entities.get(camera_id)
        base = camera_id.split(".", 1)[1] + "_motion"
        ent = EntityEntry(entity_id=free_entity_id(self.hub, "binary_sensor", base, set()), platform=DOMAIN,
                          unique_id=uid, config_entry_id=self.entry.entry_id,
                          device_id=cam.device_id if cam is not None else None,
                          area_id=cam.area_id if cam is not None else None,
                          original_name=f"{self.hub.friendly_name(cam) if cam is not None else camera_id} motion",
                          device_class="motion", original_device_class="motion")
        self.hub.entities[ent.entity_id] = ent
        if self.hub.store is not None:
            self.hub.store.save_entity(ent)
        self.log.info("motion sensor %s for %s", ent.entity_id, camera_id)
        return ent

    def publish_motion(self, watcher: CameraWatcher, *, available: bool) -> None:
        st = watcher.status
        state = ("on" if st.motion else "off") if available else "unavailable"
        key = (state, st.recording)
        if key == st._published:
            return
        st._published = key
        ent = self.motion_entity(watcher.entity_id)
        if ent.disabled_by:
            return
        self.publish(ent, state, {"device_class": "motion", "camera": watcher.entity_id,
                                  "recording": st.recording})

    # -- clips ------------------------------------------------------------------
    def remote_path(self, camera_id: str, name: str) -> str:
        parsed = parse_clip_name(name, self.tz)
        if parsed is None:
            raise NextcloudError("Not a recording name", 400)
        return f"{self.folder}/{camera_id.split('.', 1)[1]}/{parsed[0].date().isoformat()}/{name}"

    def pending_path(self, camera_id: str, name: str) -> Path:
        return self.pending_dir / camera_id.split(".", 1)[1] / name

    async def save_clip(self, camera_id: str, clip: Clip, fps: int) -> None:
        # Saved the way the camera is turned in Jarvis (its entity options), so the file is upright in
        # Nextcloud too; the name says so, and the app does not turn it again.
        cam = self.hub.entities.get(camera_id)
        rotation = camera_rotation(cam) if cam is not None else 0
        name = clip_name(datetime.fromtimestamp(clip.times[0], self.tz), clip.seconds, rotation=rotation)
        local = self.pending_path(camera_id, name)
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            await clip.encode(local, fps, rotation)
        except Exception as exc:
            self.log.warning("%s: a clip could not be made: %s", camera_id, exc)
            with contextlib.suppress(OSError):
                local.unlink()
            return
        finally:
            clip.discard()
        w = self.watchers.get(camera_id)
        if w is not None:
            w.status.clips += 1
            w.status.last_clip = name
        await self.upload_one(camera_id, name)

    async def upload_one(self, camera_id: str, name: str) -> bool:
        local = self.pending_path(camera_id, name)
        key = f"{camera_id}/{name}"
        if key in self.uploading or not local.exists():
            return False
        self.uploading.add(key)
        try:
            await self.nextcloud.upload(self.remote_path(camera_id, name), local)
        except Exception as exc:
            self.last_error = f"Upload failed, kept on the hub for now: {exc or type(exc).__name__}"
            self.log.info("%s: %s", key, self.last_error)
            return False
        finally:
            self.uploading.discard(key)
        local.unlink(missing_ok=True)
        self.last_error = None
        return True

    async def upload_pending(self) -> None:
        if not self.pending_dir.exists():
            return
        for folder in sorted(p for p in self.pending_dir.iterdir() if p.is_dir()):
            for clip in sorted(folder.glob("*.mp4")):
                if not await self.upload_one(f"camera.{folder.name}", clip.name):
                    return  # Nextcloud is still away: try again next time

    async def prune(self) -> None:
        """Delete each camera's day folders older than its retention (local copies too)."""
        today = datetime.now(self.tz).date()
        cams = (self.entry.options or {}).get("cameras") or {}
        for eid in cams:
            keep = camera_settings(self.entry.options or {}, eid)["retention_days"]
            oldest = today - timedelta(days=keep - 1)
            obj = eid.split(".", 1)[1]
            try:
                days = await self.nextcloud.list(f"{self.folder}/{obj}") or []
            except Exception as exc:
                self.log.info("pruning %s: %s", eid, exc)
                continue
            for item in days:
                if item.is_dir and _DAY_NAME.fullmatch(item.name) and date.fromisoformat(item.name) < oldest:
                    with contextlib.suppress(Exception):
                        await self.nextcloud.delete(f"{self.folder}/{obj}/{item.name}")
                        self.log.info("deleted %s's recordings of %s", eid, item.name)
            local = self.pending_dir / obj
            for clip in local.glob("*.mp4") if local.exists() else []:
                parsed = parse_clip_name(clip.name, self.tz)
                if parsed is not None and parsed[0].date() < oldest:
                    clip.unlink(missing_ok=True)

    # -- the recordings contract (the Cameras page's Recordings tab) -----------------
    def records(self, camera_id: str) -> bool:
        """Whether this camera has (or had) recordings here: it is in the options."""
        return camera_id in ((self.entry.options or {}).get("cameras") or {})

    def supports_recordings(self, entity: EntityEntry) -> bool:
        return self.records(entity.entity_id)

    async def async_list_recordings(self, entity: EntityEntry, start: datetime, end: datetime) -> list[dict[str, Any]]:
        obj = entity.entity_id.split(".", 1)[1]
        found: dict[str, dict[str, Any]] = {}
        failure: Exception | None = None
        day, last = start.astimezone(self.tz).date(), end.astimezone(self.tz).date()
        while day <= last and failure is None:
            try:
                items = await self.nextcloud.list(f"{self.folder}/{obj}/{day.isoformat()}") or []
            except (NextcloudError, aiohttp.ClientError, TimeoutError) as exc:
                failure = exc  # still list the clips waiting on the hub
                break
            for item in items:
                rec = self._recording(item.name, item.size, start, end)
                if rec is not None:
                    found[item.name] = rec
            day += timedelta(days=1)
        local = self.pending_dir / obj
        for clip in local.glob("*.mp4") if local.exists() else []:
            rec = self._recording(clip.name, clip.stat().st_size, start, end)
            if rec is not None:
                found.setdefault(clip.name, rec)
        if failure is not None and not found:
            raise failure if isinstance(failure, NextcloudError) else NextcloudError(
                f"Nextcloud did not answer: {type(failure).__name__}", 502)
        return list(found.values())

    def _recording(self, name: str, size: int | None, start: datetime, end: datetime) -> dict[str, Any] | None:
        parsed = parse_clip_name(name, self.tz)
        if parsed is None:
            return None
        began, seconds, trigger, rotation = parsed
        finished = began + timedelta(seconds=seconds)
        if finished < start or began > end:
            return None
        # rotation: the turn already in the file; the app turns it only by what the camera's turn adds.
        return {"file": name, "start": began.isoformat(), "end": finished.isoformat(), "duration": seconds,
                "triggers": [trigger], "size": size, "stream": None, "rotation": rotation}

    async def async_open_recording(self, entity: EntityEntry, file: str, *, range_header: str | None = None) -> Any:
        from services.homehub.drivers.mjpeg import RecordingStream

        if parse_clip_name(file, self.tz) is None or "/" in file:
            raise NextcloudError("No such recording", 404)
        local = self.pending_path(entity.entity_id, file)
        if local.exists():
            return _local_recording(local, range_header)
        resp = await self.nextcloud.open(self.remote_path(entity.entity_id, file), range_header)

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    yield chunk
            finally:
                resp.release()

        headers = {k: v for k in ("Content-Length", "Content-Range", "Accept-Ranges")
                   if (v := resp.headers.get(k))}
        return RecordingStream(resp.status, "video/mp4", body(), headers)

    # -- status ---------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {"error": self.last_error, "cameras": {
            eid: {"watching": w.status.watching, "motion": w.status.motion, "recording": w.status.recording,
                  "error": w.status.error, "clips": w.status.clips, "last_clip": w.status.last_clip,
                  "last_motion": datetime.fromtimestamp(w.status.last_motion, self.tz).isoformat()
                  if w.status.last_motion else None}
            for eid, w in sorted(self.watchers.items())}}


def _local_recording(path: Path, range_header: str | None) -> Any:
    """A clip still on the hub, with byte ranges (players seek with them)."""
    from services.homehub.drivers.mjpeg import RecordingStream

    size = path.stat().st_size
    first, last, status = 0, size - 1, 200
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (range_header or "").strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            first = int(m.group(1))
            last = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:
            first = max(0, size - int(m.group(2)))
        if first > last:
            raise NextcloudError("That range is outside the recording", 416)
        status = 206

    async def body() -> AsyncIterator[bytes]:
        with path.open("rb") as fh:
            fh.seek(first)
            left = last - first + 1
            while left > 0:
                chunk = fh.read(min(64 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = {"Content-Length": str(last - first + 1), "Accept-Ranges": "bytes"}
    if status == 206:
        headers["Content-Range"] = f"bytes {first}-{last}/{size}"
    return RecordingStream(status, "video/mp4", body(), headers)


def ineligible(hub: Hub, entity_id: str) -> str | None:
    """Why the hub cannot record this camera, or None when it can."""
    ent = hub.entities.get(entity_id)
    if ent is None or ent.domain != "camera" or ent.disabled_by:
        return "not a camera"
    driver = hub.drivers.get(ent.config_entry_id or "")
    info = getattr(driver, "camera_info", None)
    if callable(info):
        with contextlib.suppress(Exception):
            if (info(ent) or {}).get("battery"):
                return "it runs on a battery, and every picture would wake it"
    return None


def records_itself(hub: Hub, entity_id: str) -> bool:
    """Whether the camera keeps its own recordings (a Reolink camera on its card or its hub)."""
    ent = hub.entities.get(entity_id)
    driver = hub.drivers.get(ent.config_entry_id or "") if ent is not None else None
    if driver is None or isinstance(driver, CameraRecordingDriver) \
            or not hasattr(driver, "async_list_recordings") or not hasattr(driver, "async_open_recording"):
        return False
    check = getattr(driver, "supports_recordings", None)
    try:
        return bool(check(ent)) if callable(check) else True
    except Exception:
        return False


def service(hub: Hub) -> CameraRecordingDriver | None:
    """The running recorder, if there is one."""
    for driver in hub.drivers.values():
        if isinstance(driver, CameraRecordingDriver):
            return driver
    return None


def recorder_for(hub: Hub, entity_id: str) -> CameraRecordingDriver | None:
    """The recorder when it keeps this camera's recordings."""
    svc = service(hub)
    return svc if svc is not None and svc.records(entity_id) else None


def household_account_from_env() -> dict[str, str] | None:
    """The household's Nextcloud account (Jarvis's own) from the environment.

    Called once, when the hub starts (``main.py`` keeps it on ``hub.household_nextcloud``); the settings
    page can then copy it into the integration instead of typing it again. None when it is not set.
    """
    account = {k: (os.getenv(env) or "").strip() for k, env in
               (("url", "NEXTCLOUD_URL"), ("username", "NEXTCLOUD_USER"), ("password", "NEXTCLOUD_PASS"))}
    return account if all(account.values()) else None
