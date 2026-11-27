"""Link previews for the private chat, made once by the sender's device.

The private chat promises that the server cannot read a message, so a preview
follows Signal's model: the *sender's* app asks for one while composing, puts
it inside the sealed message, and the other parent's app only ever draws what
it decrypted. Nothing is fetched when a message is read, so no site (and no
video host) learns that a message was opened.

A browser cannot fetch another site's page itself (CORS), so the sender's app
asks this route (``POST /api/private-chat/chats/<token>/link-preview``) to do
it. The route is careful because it makes this server fetch an address a
person typed:

* Only a member of that private chat who is still a parent may ask, at most
  ``RATE_LIMIT`` times per ``RATE_WINDOW_S``.
* Only ``http``/``https`` on ports 80 and 443. The host is resolved here and
  refused when any address it resolves to is not public (private, loopback,
  link-local, multicast, reserved, carrier-grade NAT...). The connection goes
  only to the addresses that passed (``_PublicResolver``), so a name cannot
  resolve to a public address for the check and a private one for the fetch.
  Every redirect (``MAX_REDIRECTS`` at most) is checked the same way.
* Connect within ``CONNECT_TIMEOUT_S``, everything within ``TOTAL_TIMEOUT_S``;
  at most ``MAX_HTML_BYTES`` of a page and ``MAX_IMAGE_BYTES`` of a picture are
  read. No cookies are kept or sent, and no proxy settings are used.
* The page's address is never logged and nothing is stored: the answer goes
  back to the device and is forgotten. Errors are logged by type only (an
  aiohttp error's text contains the address).

The answer is ``{url, title, description, site_name, kind, image, embed}``:
``kind`` is ``"link"``, ``"video"`` or ``"audio"``; ``image`` is a small JPEG
thumbnail as a data URL (re-encoded here, so the receiver never decodes a
stranger's file format); ``embed`` says how the receiver may play it once
tapped: ``{"provider": "youtube", "id"}``, ``{"provider": "vimeo", "id",
"hash"?}`` or ``{"provider": "file", "src", "mime"}``.
"""
from __future__ import annotations

import asyncio
import base64
import codecs
import io
import ipaddress
import logging
import re
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.gateway import private_chat

log = logging.getLogger("gateway.private_chat_preview")

router = APIRouter(prefix="/api/private-chat")

CONNECT_TIMEOUT_S = 3.0
TOTAL_TIMEOUT_S = 6.0
MAX_REDIRECTS = 3
#: Enough for any page's <head>; the rest is not read.
MAX_HTML_BYTES = 512 * 1024
#: A picture larger than this is not made into a thumbnail.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
#: Pictures with more pixels than this are not decoded (a decompression bomb is small on the wire).
MAX_IMAGE_PIXELS = 40_000_000
THUMB_MAX_PX = 480
#: The thumbnail's JPEG bytes at most; it travels inside the sealed message.
THUMB_MAX_BYTES = 60 * 1024
MAX_URL_CHARS = 2048
MAX_TITLE_CHARS = 300
MAX_DESCRIPTION_CHARS = 500
MAX_SITE_CHARS = 100
#: Previews per person per window.
RATE_LIMIT = 30
RATE_WINDOW_S = 60
ALLOWED_PORTS = frozenset({80, 443})
USER_AGENT = "Mozilla/5.0 (compatible; JarvisLinkPreview/1.0)"

VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}
AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}
#: Content types a direct file may answer with, and the type the player is told.
_MEDIA_CONTENT_TYPES = {
    "video/mp4": "video/mp4",
    "video/webm": "video/webm",
    "video/quicktime": "video/quicktime",
    "audio/mpeg": "audio/mpeg",
    "audio/mp3": "audio/mpeg",
    "audio/mp4": "audio/mp4",
    "audio/x-m4a": "audio/mp4",
    "audio/aac": "audio/aac",
    "audio/ogg": "audio/ogg",
    "audio/opus": "audio/ogg",
    "audio/wav": "audio/wav",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
}

_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIMEO_ID = re.compile(r"^[0-9]{5,12}$")
_VIMEO_HASH = re.compile(r"^[0-9a-f]{6,20}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACE = re.compile(r"\s+")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]{1,40})""", re.I)


class Refused(Exception):
    """The address may not be fetched from here (scheme, port or a non-public host)."""


class FetchFailed(Exception):
    """The address could not be fetched (network, status, size or time)."""


# --- Who may ask, and how often -----------------------------------------------


def _now() -> float:
    """The clock the window is counted on. A seam, so a test cannot straddle a boundary."""
    return time.time()


def _claim(user: str, now: float | None = None) -> bool:
    """Count one preview for ``user``; False once ``RATE_LIMIT`` are used in this window."""
    from services.gateway.cache import get_redis

    window = int((now if now is not None else _now()) // RATE_WINDOW_S)
    key = f"private-chat:link-preview:{user}:{window}"
    r = get_redis()
    count = r.incr(key)
    if count == 1:
        r.expire(key, RATE_WINDOW_S * 2)
    return int(count) <= RATE_LIMIT


# --- Addresses ------------------------------------------------------------------


def public_address(value: str) -> bool:
    """Whether an IP address is one the open internet can reach (never this network's or a reserved one)."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return public_address(str(mapped))
        if ip.teredo is not None:
            return False
    return bool(
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


async def _resolve_host(host: str, port: int) -> list[str]:
    """Every address a name resolves to (replaced in tests: they never touch the network)."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _split(url: str) -> tuple[str, str, int]:
    """Scheme, host and port of an address this route may fetch; Refused otherwise."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise Refused("unparsable") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise Refused("scheme")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host or parts.username is not None or parts.password is not None:
        raise Refused("host")
    port = port if port is not None else (443 if scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise Refused("port")
    return scheme, host, port


async def public_addresses(host: str, port: int) -> list[str]:
    """The host's addresses when every one of them is public; Refused when any is not."""
    try:
        ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
        literal = True
    except ValueError:
        literal = False
    if literal:
        addresses = [host.strip("[]")]
    else:
        if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
            raise Refused("local name")
        try:
            addresses = await _resolve_host(host, port)
        except OSError as exc:
            raise FetchFailed("resolve") from exc
    if not addresses or not all(public_address(a) for a in addresses):
        raise Refused("non-public address")
    return addresses


async def check_url(url: str) -> None:
    """Refused unless the address is http(s) on a usual port and its host is public."""
    _, host, port = _split(url)
    await public_addresses(host, port)


class _PublicResolver(AbstractResolver):
    """Resolves only to public addresses, so the connection goes where the check looked."""

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        try:
            addresses = await public_addresses(host, port)
        except (Refused, FetchFailed) as exc:
            raise OSError("refused") from exc
        return [
            ResolveResult(
                hostname=host,
                host=address,
                port=port,
                family=socket.AF_INET6 if ":" in address else socket.AF_INET,
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
            for address in addresses
        ]

    async def close(self) -> None:
        return None


# --- Fetching -------------------------------------------------------------------


async def _read_capped(resp: aiohttp.ClientResponse, limit: int, *, truncate: bool) -> bytes:
    """At most ``limit`` bytes; past it the rest is dropped (a page) or the whole is refused (a picture)."""
    length = resp.headers.get("Content-Length")
    if not truncate and length and length.isdigit() and int(length) > limit:
        raise FetchFailed("too large")
    out = bytearray()
    while True:
        chunk = await resp.content.read(min(64 * 1024, limit + 1 - len(out)))
        if not chunk:
            break
        out.extend(chunk)
        if len(out) > limit:
            if not truncate:
                raise FetchFailed("too large")
            del out[limit:]
            break
    return bytes(out)


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=_PublicResolver(), limit=4, force_close=True),
        timeout=aiohttp.ClientTimeout(total=TOTAL_TIMEOUT_S, sock_connect=CONNECT_TIMEOUT_S),
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
    )


async def fetch(url: str, *, accept: str, limit: int, truncate: bool) -> tuple[str, str, bytes]:
    """The final address, its content type and body, following at most ``MAX_REDIRECTS`` checked redirects."""
    current = url
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_S), _session() as session:
            for _ in range(MAX_REDIRECTS + 1):
                await check_url(current)
                async with session.get(current, allow_redirects=False, headers={"Accept": accept}) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            raise FetchFailed("redirect without a location")
                        current = urljoin(current, location)
                        continue
                    if resp.status != 200:
                        raise FetchFailed(f"status {resp.status}")
                    content_type = resp.headers.get("Content-Type", "")
                    body = await _read_capped(resp, limit, truncate=truncate)
                    return current, content_type, body
            raise FetchFailed("too many redirects")
    except (Refused, FetchFailed):
        raise
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
        raise FetchFailed(type(exc).__name__) from None


# --- Reading a page -------------------------------------------------------------


class _Head(HTMLParser):
    """The page's meta tags (first value of each name wins) and its <title>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self.done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.done:
            return
        if tag == "meta":
            found = {k.lower(): (v or "") for k, v in attrs}
            name = (found.get("property") or found.get("name") or found.get("itemprop") or "").strip().lower()
            content = found.get("content", "").strip()
            if name and content and name not in self.meta:
                self.meta[name] = content
        elif tag == "title":
            self._in_title = True
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.done and len(self.title) < 4 * MAX_TITLE_CHARS:
            self.title += data


def _charset(content_type: str, body: bytes) -> str:
    match = re.search(r"charset\s*=\s*\"?([A-Za-z0-9_.:-]{1,40})", content_type, re.I)
    found = match.group(1) if match else None
    if not found:
        sniffed = _META_CHARSET.search(body[:4096])
        found = sniffed.group(1).decode("ascii", "ignore") if sniffed else None
    try:
        return codecs.lookup(found).name if found else "utf-8"
    except LookupError:
        return "utf-8"


def parse_head(body: bytes, content_type: str = "") -> tuple[dict[str, str], str]:
    """The meta tags and title of an HTML page (or as much of it as was read)."""
    parser = _Head()
    try:
        parser.feed(body.decode(_charset(content_type, body), errors="replace"))
        parser.close()
    except Exception:
        pass
    return parser.meta, parser.title


def _clean(value: Any, limit: int) -> str:
    text = _SPACE.sub(" ", _CONTROL.sub("", str(value or ""))).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _http_url(value: str | None, base: str) -> str | None:
    if not value:
        return None
    url = urljoin(base, value.strip())
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > MAX_URL_CHARS:
        return None
    return url


# --- What kind of thing it is ----------------------------------------------------


def _host(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    return host


def youtube_id(url: str) -> str | None:
    """The video id of a YouTube address (watch, youtu.be, shorts, embed, live), or None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = _host(url)
    segments = [s for s in parts.path.split("/") if s]
    found: str | None = None
    if host == "youtu.be" and segments:
        found = segments[0]
    elif host in ("youtube.com", "youtube-nocookie.com"):
        if parts.path == "/watch":
            found = (parse_qs(parts.query).get("v") or [""])[0]
        elif len(segments) >= 2 and segments[0] in ("shorts", "embed", "live", "v"):
            found = segments[1]
    return found if found and _YOUTUBE_ID.match(found) else None


def vimeo_embed(url: str) -> dict[str, str] | None:
    """A Vimeo video's id (and an unlisted video's hash), or None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = _host(url)
    if host not in ("vimeo.com", "player.vimeo.com"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    for index, segment in enumerate(segments):
        if _VIMEO_ID.match(segment):
            embed = {"provider": "vimeo", "id": segment}
            following = segments[index + 1] if index + 1 < len(segments) else (parse_qs(parts.query).get("h") or [""])[0]
            if following and _VIMEO_HASH.match(following):
                embed["hash"] = following
            return embed
    return None


def media_file(url: str) -> tuple[str, str] | None:
    """``("video"|"audio", mime)`` for an address that names a video or audio file, or None."""
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return None
    for ext, mime in VIDEO_TYPES.items():
        if path.endswith(ext):
            return "video", mime
    for ext, mime in AUDIO_TYPES.items():
        if path.endswith(ext):
            return "audio", mime
    return None


def embed_for(url: str) -> tuple[str, dict[str, str]] | None:
    """How a receiver may play this address after a tap, as ``(kind, embed)``, or None for a plain link."""
    video_id = youtube_id(url)
    if video_id:
        return "video", {"provider": "youtube", "id": video_id}
    vimeo = vimeo_embed(url)
    if vimeo:
        return "video", vimeo
    media = media_file(url)
    if media:
        kind, mime = media
        return kind, {"provider": "file", "src": url, "mime": mime}
    return None


# --- Thumbnails -------------------------------------------------------------------


def thumbnail(data: bytes) -> str | None:
    """A small JPEG of a picture, as a data URL, or None when it is not a picture this can read safely."""
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - the gateway image has Pillow
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in ("JPEG", "PNG", "GIF", "WEBP"):
                return None
            width, height = source.size
            if width < 16 or height < 16 or width * height > MAX_IMAGE_PIXELS:
                return None
            if source.format == "JPEG":
                source.draft("RGB", (THUMB_MAX_PX * 2, THUMB_MAX_PX * 2))
            image = ImageOps.exif_transpose(source) or source
            image = image.convert("RGBA")
            flat = Image.new("RGB", image.size, (15, 23, 42))
            flat.paste(image, mask=image.getchannel("A"))
            for size, qualities in ((THUMB_MAX_PX, (82, 72, 62, 52, 42)), (320, (60, 45))):
                small = flat.copy()
                small.thumbnail((size, size))
                for quality in qualities:
                    out = io.BytesIO()
                    small.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
                    if out.tell() <= THUMB_MAX_BYTES:
                        return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
    except Exception as exc:
        log.debug("[private-chat] no thumbnail: %s", type(exc).__name__)
    return None


async def _thumbnail_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        _, content_type, body = await fetch(url, accept="image/*", limit=MAX_IMAGE_BYTES, truncate=False)
    except (Refused, FetchFailed) as exc:
        log.debug("[private-chat] no preview picture: %s", type(exc).__name__)
        return None
    if content_type and not content_type.lower().startswith("image/"):
        return None
    return await asyncio.to_thread(thumbnail, body)


async def _public_embed(url: str | None) -> tuple[str, dict[str, str]] | None:
    """The embed for a page's og:video / og:audio address, only when that address passes the same public check
    as og:image. The receiver's phone loads it directly, so a page must not be able to point it at a private host."""
    if not url:
        return None
    found = embed_for(url)
    if not found:
        return None
    try:
        await check_url(url)
    except (Refused, FetchFailed) as exc:
        log.info("[private-chat] page media address refused: %s (%s)", type(exc).__name__, exc)
        return None
    return found


# --- The preview -------------------------------------------------------------------


def _file_name(url: str) -> str:
    path = urlsplit(url).path
    return unquote(path.rsplit("/", 1)[-1]) or _host(url)


async def build_preview(url: str) -> dict[str, Any] | None:
    """The preview of one address, or None when there is nothing to show. Refused / FetchFailed propagate."""
    await check_url(url)
    site = _host(url)
    known = embed_for(url)
    if known and known[1]["provider"] == "file":
        # A video or audio file: nothing to read, and downloading it here would be wasteful.
        kind, embed = known
        return {"url": url, "title": _clean(_file_name(url), MAX_TITLE_CHARS), "description": "", "site_name": _clean(site, MAX_SITE_CHARS),
                "kind": kind, "image": None, "embed": embed}

    try:
        final, content_type, body = await fetch(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                                                limit=MAX_HTML_BYTES, truncate=True)
    except FetchFailed:
        if not known:
            raise
        final, content_type, body = url, "", b""

    kind: str = "link"
    embed: dict[str, str] | None = None
    if known:
        kind, embed = known
    ctype = content_type.split(";", 1)[0].strip().lower()
    if not embed and ctype in _MEDIA_CONTENT_TYPES:
        mime = _MEDIA_CONTENT_TYPES[ctype]
        return {"url": url, "title": _clean(_file_name(url), MAX_TITLE_CHARS), "description": "", "site_name": _clean(site, MAX_SITE_CHARS),
                "kind": "audio" if mime.startswith("audio/") else "video", "image": None,
                "embed": {"provider": "file", "src": url, "mime": mime}}
    if ctype.startswith("image/"):
        image = await asyncio.to_thread(thumbnail, body) if len(body) < MAX_HTML_BYTES else await _thumbnail_of(final)
        return {"url": url, "title": _clean(_file_name(url), MAX_TITLE_CHARS), "description": "", "site_name": _clean(site, MAX_SITE_CHARS),
                "kind": "link", "image": image, "embed": None}

    meta, page_title = parse_head(body, content_type) if body else ({}, "")
    if not embed:
        found = embed_for(final)
        if found and found[1]["provider"] != "file":
            kind, embed = found
    if not embed:
        for key in ("og:video:secure_url", "og:video:url", "og:video", "twitter:player:stream"):
            found = await _public_embed(_http_url(meta.get(key), final))
            if found and found[0] == "video":
                kind, embed = found
                break
    if not embed:
        for key in ("og:audio:secure_url", "og:audio:url", "og:audio"):
            found = await _public_embed(_http_url(meta.get(key), final))
            if found and found[0] == "audio":
                kind, embed = found
                break

    title = _clean(meta.get("og:title") or meta.get("twitter:title") or page_title, MAX_TITLE_CHARS)
    description = _clean(meta.get("og:description") or meta.get("twitter:description") or meta.get("description"), MAX_DESCRIPTION_CHARS)
    site_name = _clean(meta.get("og:site_name") or site, MAX_SITE_CHARS)
    image_url = None
    for key in ("og:image:secure_url", "og:image:url", "og:image", "twitter:image", "twitter:image:src"):
        image_url = _http_url(meta.get(key), final)
        if image_url:
            break
    if not image_url and embed and embed.get("provider") == "youtube":
        image_url = f"https://i.ytimg.com/vi/{embed['id']}/hqdefault.jpg"
    image = await _thumbnail_of(image_url)
    if not (title or description or image or embed):
        return None
    if not title and embed and embed.get("provider") == "youtube":
        title = "YouTube video"
    elif not title and embed and embed.get("provider") == "vimeo":
        title = "Vimeo video"
    return {"url": url, "title": title, "description": description, "site_name": site_name, "kind": kind, "image": image, "embed": embed}


@router.post("/chats/{token}/link-preview")
async def link_preview(token: str, request: Request):
    """Fetch one page for its preview, for the sender to seal into the message. Nothing is logged or kept."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="A JSON body is required.") from exc
    url = body.get("url") if isinstance(body, dict) else None
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_CHARS:
        raise HTTPException(status_code=422, detail="url is required.")
    url = url.strip()
    user, _chat, _people = await private_chat._member_chat(request, token)
    try:
        allowed = await asyncio.to_thread(_claim, user)
    except Exception as exc:
        log.warning("[private-chat] link preview limit unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Link previews are unavailable right now.") from exc
    if not allowed:
        return JSONResponse(status_code=429, content={"detail": "Too many link previews; try again in a minute."},
                            headers={"Retry-After": str(RATE_WINDOW_S)})
    try:
        preview = await build_preview(url)
    except Refused:
        raise HTTPException(status_code=422, detail="That address cannot be previewed.") from None
    except FetchFailed:
        raise HTTPException(status_code=502, detail="The page could not be read.") from None
    except Exception as exc:
        log.warning("[private-chat] link preview failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="The page could not be read.") from None
    if preview is None:
        raise HTTPException(status_code=404, detail="Nothing to preview.")
    return preview
