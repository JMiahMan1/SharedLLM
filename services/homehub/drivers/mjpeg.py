"""MJPEG IP cameras (HA's ``mjpeg`` integration), and the camera contract every camera driver follows.

Camera contract (for the API layer)
-----------------------------------
A camera entity's state is ``idle`` with HA's attributes, including
``entity_picture`` = ``/api/camera_proxy/<entity_id>?token=<access_token>``.
Any driver that owns ``camera.*`` entities (this one, ``reolink``) provides:

``async def async_camera_image(self, entity) -> bytes``
    One JPEG. ``GET /api/camera_proxy/<entity_id>`` answers with it
    (``image/jpeg``). Raises ``DeviceUnavailable`` when the camera does not
    answer.

``async def async_camera_stream_url(self, entity) -> str | None``
    Where the live stream is. An ``http(s)`` URL is an MJPEG stream the API can
    relay as is for ``GET /api/camera_proxy_stream/<entity_id>``. Anything else
    (``rtsp://``) or ``None`` means "no MJPEG here": the API then serves
    ``camera_proxy_stream`` the way HA does for such cameras, as
    ``multipart/x-mixed-replace`` built from repeated ``async_camera_image``
    calls. The URL may carry credentials, so it must never be shown to a client.

``def access_tokens(self, entity_id) -> list[str]``
    The tokens a ``?token=`` may carry for this camera (the newest two, as HA
    keeps them), so the API can accept ``entity_picture`` links without a
    bearer token. Tokens rotate every five minutes (HA's interval).

This driver also offers ``async_open_mjpeg_stream(entity)`` -> ``(content_type,
async iterator of bytes)``, which relays the camera's stream with its auth
applied, for an API that would rather not hand the URL to a second client.
``camera_proxy_stream`` relays it when a driver has it, and falls back to
frames from ``async_camera_image`` when it fails.

Optional, for the hub's own camera API (``/api/homehub/cameras``):

``snapshot_interval`` (class attribute, seconds)
    How often ``camera_proxy_stream`` asks for a frame when it builds the
    stream from snapshots. Default 1.0.

``def camera_info(self, entity) -> dict``
    Extra facts for the camera list (``battery``, ``sleeping``, ``model``).

``async def async_list_recordings(self, entity, start, end) -> list[dict]``
    Recordings between two aware datetimes, each ``{"file", "start", "end",
    "duration", "triggers", "size", "stream"}`` (times as ISO strings with an
    offset, ``duration`` in seconds, ``triggers`` lower-case names such as
    ``person``). Any order; the API sorts them.

``async def async_open_recording(self, entity, file, *, range_header) -> RecordingStream``
    One recording a ``<video>`` element can play, streamed. ``range_header``
    is the client's ``Range`` header, passed on so seeking works. Raises
    ``EntityNotFound`` for a file this camera did not list.

``def supports_recordings(self, entity) -> bool`` (optional)
    Narrows recordings to some of the driver's cameras; without it every
    camera of a driver with ``async_list_recordings`` has them.

Config (all in ``entry.options``, as HA stores it): ``mjpeg_url``,
``still_image_url`` (optional), ``username``, ``password``,
``authentication`` (``basic`` or ``digest``) and ``verify_ssl``. Those are
where and how to reach the camera, nothing more.

**The protocol says nothing about the device.** An MJPEG stream is an HTTP
response: no maker, model, firmware or MAC. The driver confirms only what it
knows itself (a camera that gives stills and an MJPEG stream, published with
every state), never the device fields HA had, which stay marked ``imported``.
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp

from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, ServiceNotFound

#: HA rotates camera access tokens this often and accepts the newest two.
TOKEN_ROTATE_S = 300.0
IMAGE_TIMEOUT = aiohttp.ClientTimeout(total=10, sock_connect=5)
#: A stream is long lived: only the connect and each read are bounded.
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=15)
#: Give up looking for a frame in a stream after this many bytes (a few frames).
MAX_FRAME_SEARCH = 8 * 1024 * 1024
SOI, EOI = b"\xff\xd8", b"\xff\xd9"


@dataclass
class RecordingStream:
    """A recording on its way to a client: the upstream status (200, or 206 for a range) and its body."""

    status: int
    content_type: str
    body: AsyncIterator[bytes]
    #: Only the headers a player needs: Content-Length, Content-Range, Accept-Ranges.
    headers: dict[str, str] = field(default_factory=dict)


class CameraTokens:
    """Per-camera access tokens, rotated like HA's (newest two stay valid)."""

    def __init__(self) -> None:
        self._tokens: dict[str, deque[str]] = {}
        self._rotated: dict[str, float] = {}

    def current(self, entity_id: str) -> str:
        if entity_id not in self._tokens:
            self.rotate(entity_id)
        return self._tokens[entity_id][-1]

    def rotate(self, entity_id: str) -> str:
        self._tokens.setdefault(entity_id, deque(maxlen=2)).append(secrets.token_hex(32))
        self._rotated[entity_id] = time.monotonic()
        return self._tokens[entity_id][-1]

    def due(self, entity_id: str) -> bool:
        return time.monotonic() - self._rotated.get(entity_id, 0.0) >= TOKEN_ROTATE_S

    def valid(self, entity_id: str) -> list[str]:
        return list(self._tokens.get(entity_id, ()))


def camera_attributes(entity_id: str, token: str, *, supported_features: int = 0,
                      extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """HA's camera state attributes."""
    attrs = {
        "access_token": token,
        "entity_picture": f"/api/camera_proxy/{entity_id}?token={token}",
        "supported_features": supported_features,
    }
    attrs.update(extra or {})
    return attrs


def extract_jpeg(buf: bytes) -> tuple[bytes | None, int]:
    """The first complete JPEG in an MJPEG byte buffer, and where it ended.

    Uses the part's ``Content-Length`` when the camera sends one (an EXIF
    thumbnail inside a frame has its own end marker), else the first end
    marker after the start of the image.
    """
    start = buf.find(SOI)
    if start < 0:
        return None, 0
    head = buf[:start]
    length = None
    for line in head.split(b"\n")[::-1]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            with contextlib.suppress(ValueError):
                length = int(value.strip())
            break
    if length:
        if len(buf) - start < length:
            return None, 0
        return buf[start:start + length], start + length
    end = buf.find(EOI, start + 2)
    if end < 0:
        return None, 0
    return buf[start:end + 2], end + 2


class MjpegDriver(Driver):
    platform = "mjpeg"
    #: Nothing to read from the camera; the poll only rotates the picture token.
    poll_interval = 60.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.tokens = CameraTokens()
        self._session: aiohttp.ClientSession | None = None

    # -- config ---------------------------------------------------------------
    def _opt(self, key: str, default: Any = None) -> Any:
        # HA keeps these in options; older entries had them in data.
        if key in self.entry.options:
            return self.entry.options[key]
        return self.entry.data.get(key, default)

    @property
    def mjpeg_url(self) -> str:
        return str(self._opt("mjpeg_url") or "")

    @property
    def still_url(self) -> str:
        return str(self._opt("still_image_url") or "")

    def _auth_kwargs(self) -> dict[str, Any]:
        user, password = self._opt("username"), self._opt("password")
        if not user:
            return {}
        if str(self._opt("authentication") or "basic").lower() == "digest":
            return {"middlewares": (aiohttp.DigestAuthMiddleware(str(user), str(password or "")),)}
        return {"auth": aiohttp.BasicAuth(str(user), str(password or ""))}

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            verify = bool(self._opt("verify_ssl", True))
            kw = self._auth_kwargs()
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=verify), **kw)
        return self._session

    # -- lifecycle ------------------------------------------------------------
    async def async_setup(self) -> None:
        if not self.mjpeg_url:
            raise RuntimeError("mjpeg entry has no mjpeg_url")
        for ent in self.entities:
            if ent.domain == "camera":
                self.tokens.rotate(ent.entity_id)
                self._publish(ent)

    async def async_poll(self) -> None:
        for ent in self.entities:
            if ent.domain == "camera" and self.tokens.due(ent.entity_id):
                self.tokens.rotate(ent.entity_id)
                self._publish(ent)

    async def async_unload(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _publish(self, ent: EntityEntry) -> None:
        self.publish(ent, "idle", camera_attributes(ent.entity_id, self.tokens.current(ent.entity_id)))

    def access_tokens(self, entity_id: str) -> list[str]:
        return self.tokens.valid(entity_id)

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        # camera.snapshot writes a file on HA's disk; there is no disk to write to here.
        raise ServiceNotFound(f"mjpeg cannot run {domain}.{service}")

    # -- the camera contract --------------------------------------------------
    async def async_camera_image(self, entity: EntityEntry) -> bytes:
        errors = []
        if self.still_url:
            try:
                async with self._client().get(self.still_url, timeout=IMAGE_TIMEOUT) as resp:
                    if resp.status == 200:
                        body = await resp.read()
                        if body:
                            return body
                    errors.append(f"still image HTTP {resp.status}")
            except (TimeoutError, aiohttp.ClientError) as exc:
                errors.append(f"still image: {type(exc).__name__}: {exc}")
        try:
            return await self._frame_from_stream()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            errors.append(f"stream: {type(exc).__name__}: {exc}")
        raise DeviceUnavailable(f"{entity.entity_id}: " + "; ".join(errors))

    async def _frame_from_stream(self) -> bytes:
        async with asyncio.timeout(IMAGE_TIMEOUT.total or 10):
            async with self._client().get(self.mjpeg_url, timeout=IMAGE_TIMEOUT) as resp:
                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}")
                ctype = resp.headers.get("Content-Type", "")
                if ctype.startswith("image/"):
                    return await resp.read()
                buf = b""
                async for chunk in resp.content.iter_chunked(65536):
                    buf += chunk
                    frame, _ = extract_jpeg(buf)
                    if frame:
                        return frame
                    if len(buf) > MAX_FRAME_SEARCH:
                        break
        raise ValueError("no JPEG frame in the stream")

    async def async_camera_stream_url(self, entity: EntityEntry) -> str | None:
        url = self.mjpeg_url
        user, password = self._opt("username"), self._opt("password")
        if not url or not user or str(self._opt("authentication") or "basic").lower() != "basic":
            return url or None
        parts = urlsplit(url)
        if parts.username:
            return url
        netloc = f"{quote(str(user), safe='')}:{quote(str(password or ''), safe='')}@{parts.netloc}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    async def async_open_mjpeg_stream(self, entity: EntityEntry) -> tuple[str, AsyncIterator[bytes]]:
        """The camera's own stream, relayed. The caller must exhaust or close the iterator."""
        session = self._client()
        try:
            resp = await session.get(self.mjpeg_url, timeout=STREAM_TIMEOUT)
        except (TimeoutError, aiohttp.ClientError) as exc:
            raise DeviceUnavailable(f"{entity.entity_id}: {exc}") from exc
        if resp.status != 200:
            resp.release()
            raise DeviceUnavailable(f"{entity.entity_id}: stream HTTP {resp.status}")

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.content.iter_chunked(65536):
                    yield chunk
            finally:
                resp.release()

        return resp.headers.get("Content-Type", "multipart/x-mixed-replace"), body()
