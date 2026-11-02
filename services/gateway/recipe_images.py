"""Food photos for the family cookbook, fetched from the public web.

A recipe imported from a link or the Food.com index names its photo by URL.
The gateway downloads it once and keeps it beside the recipe note in
Nextcloud, so the card still has its picture when the site changes; search
results show theirs through the same fetcher before anything is saved.

Only public addresses are fetched: every hop of a redirect is resolved and
refused if it lands on a private, loopback or link-local address (a recipe
link must never reach the house network), and only images up to a few
megabytes come back.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

import aiohttp

MAX_BYTES = 6 * 1024 * 1024
MAX_REDIRECTS = 3
TIMEOUT = aiohttp.ClientTimeout(total=15.0, connect=6.0)
_UA = "Jarvis-OS/1.0 (family cookbook photos; self-hosted)"
_CACHE_MAX_BYTES = 48 * 1024 * 1024
_cache: OrderedDict[str, tuple[str, bytes]] = OrderedDict()
_cache_bytes = 0


class ImageRefused(Exception):
    """The address or the answer is not a public image we will fetch."""


async def _public(host: str) -> bool:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return bool(infos)


async def check_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ImageRefused("not a web address")
    if parsed.username or parsed.password:
        raise ImageRefused("credentials in the address")
    if not await _public(parsed.hostname):
        raise ImageRefused("not a public address")
    return parsed.geturl()


def cached(key: str) -> tuple[str, bytes] | None:
    hit = _cache.get(key)
    if hit:
        _cache.move_to_end(key)
    return hit


def remember(key: str, mime: str, data: bytes) -> None:
    global _cache_bytes
    if key in _cache:
        return
    _cache[key] = (mime, data)
    _cache_bytes += len(data)
    while _cache_bytes > _CACHE_MAX_BYTES and _cache:
        _, (_, old) = _cache.popitem(last=False)
        _cache_bytes -= len(old)


async def fetch(url: str) -> tuple[str, bytes]:
    """(mime, bytes) of a public image, following up to three checked redirects."""
    hit = cached(url)
    if hit:
        return hit
    target = await check_url(url)
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": _UA}) as session:
        for _ in range(MAX_REDIRECTS + 1):
            async with session.get(target, allow_redirects=False) as resp:
                if resp.status in (301, 302, 303, 307, 308):
                    target = await check_url(urljoin(target, resp.headers.get("Location", "")))
                    continue
                if resp.status != 200:
                    raise ImageRefused(f"HTTP {resp.status}")
                mime = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if not mime.startswith("image/") or mime == "image/svg+xml":
                    raise ImageRefused("not an image")
                length = int(resp.headers.get("Content-Length") or 0)
                if length > MAX_BYTES:
                    raise ImageRefused("too large")
                data = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ImageRefused("too large")
                remember(url, mime, bytes(data))
                return mime, bytes(data)
    raise ImageRefused("too many redirects")


def filename_for(url: str, mime: str) -> str:
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(mime, "jpg")
    return f"photo.{ext}"
