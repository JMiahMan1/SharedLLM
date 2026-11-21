"""Link previews for the private chat: who may ask, what may be fetched, and what comes back.

The sender's device asks the gateway to read a page once, while composing, and
seals the answer into the message. These tests check the route refuses
anything that is not a member parent, refuses every non-public address
(including one reached by a redirect), caps what it reads, reads og/twitter
tags and recognises YouTube, Vimeo and direct video and audio files, and never
logs the address or keeps anything. No test touches the network: names resolve
from a table and every HTTP answer is mocked.
"""
import base64
import io
import logging

import pytest
from aioresponses import aioresponses
from PIL import Image

from services.gateway import private_chat_preview as preview
from services.gateway.tests.test_private_chat import _as, _create, env  # noqa: F401  (env is a fixture)

TOKEN = "room123abc"
ROUTE = f"/api/private-chat/chats/{TOKEN}/link-preview"

#: What each made-up name resolves to (documentation ranges stand in for public addresses).
DNS = {
    "news.example": ["93.184.216.34"],
    "cdn.example": ["93.184.216.35"],
    "www.youtube.com": ["142.250.80.46"],
    "youtube.com": ["142.250.80.46"],
    "youtu.be": ["142.250.80.46"],
    "i.ytimg.com": ["142.250.80.47"],
    "vimeo.com": ["151.101.0.217"],
    "media.example": ["93.184.216.36"],
    "loopback.example": ["127.0.0.1"],
    "lan.example": ["192.168.1.20"],
    "ten.example": ["10.0.0.5"],
    "metadata.example": ["169.254.169.254"],
    "cgnat.example": ["100.64.0.1"],
    "multicast.example": ["224.0.0.1"],
    "zero.example": ["0.0.0.0"],
    "reserved.example": ["240.0.0.1"],
    "v6loop.example": ["::1"],
    "v6link.example": ["fe80::1"],
    "v6ula.example": ["fd00::1"],
    "mapped.example": ["::ffff:127.0.0.1"],
    # What getaddrinfo makes of a short form of 127.0.0.1.
    "127.1": ["127.0.0.1"],
    "mixed.example": ["93.184.216.37", "10.1.2.3"],
}


def _jpeg(width: int = 1200, height: int = 630, color=(200, 80, 60)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG", quality=90)
    return out.getvalue()


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 200, 10, 128)).save(out, "PNG")
    return out.getvalue()


def _page(head: str, body: str = "<p>Hello</p>") -> str:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


class _Counter:
    """The rate limit's store: incr and expire, like Redis."""

    def __init__(self):
        self.kv: dict[str, int] = {}
        self.down = False

    def incr(self, key):
        if self.down:
            raise ConnectionError("redis down")
        self.kv[key] = self.kv.get(key, 0) + 1
        return self.kv[key]

    def expire(self, key, seconds):
        return True


@pytest.fixture
def pv(env, monkeypatch):  # noqa: F811 - env is the private chat fixture
    assert _create(env).status_code == 200
    resolved: list[str] = []

    async def resolve(host, port):
        resolved.append(host)
        if host not in DNS:
            raise OSError("no such name")
        return list(DNS[host])

    counter = _Counter()
    import services.gateway.cache as gateway_cache

    monkeypatch.setattr(preview, "_resolve_host", resolve)
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: counter)
    with aioresponses() as http:
        yield {**env, "http": http, "resolved": resolved, "counter": counter}


def _ask(pv, url, user="alex"):
    return pv["client"].post(ROUTE, json={"url": url}, headers=_as(user))


def _requested(pv) -> list[str]:
    """Every outside address the gateway asked for (its own log shipping to the logging service aside)."""
    from services.gateway import main as gateway_main

    own = str(getattr(gateway_main, "LOGGING_SVC", "http://logging:8006"))
    return [str(key[1]) for key in pv["http"].requests if not str(key[1]).startswith(own)]


def _thumb_size(data_url: str) -> tuple[int, int, int]:
    assert data_url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as im:
        assert im.format == "JPEG"
        return im.width, im.height, len(raw)


# --- What comes back ------------------------------------------------------------


def test_a_page_s_open_graph_tags_become_the_preview_with_a_small_jpeg(pv):
    pv["http"].get("https://news.example/story", body=_page(
        '<meta property="og:title" content="Cabin &amp; lake">'
        '<meta property="og:description" content="A week   by the\nwater.">'
        '<meta property="og:site_name" content="The News">'
        '<meta property="og:image" content="/pics/lake.jpg">'
        "<title>Ignored when og:title is there</title>"), headers={"Content-Type": "text/html; charset=utf-8"})
    pv["http"].get("https://news.example/pics/lake.jpg", body=_jpeg(2400, 1260), headers={"Content-Type": "image/jpeg"})
    resp = _ask(pv, "https://news.example/story")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["url"] == "https://news.example/story"
    assert body["title"] == "Cabin & lake"
    assert body["description"] == "A week by the water."
    assert body["site_name"] == "The News"
    assert body["kind"] == "link" and body["embed"] is None
    width, height, size = _thumb_size(body["image"])
    assert width <= preview.THUMB_MAX_PX and height <= preview.THUMB_MAX_PX
    assert size <= preview.THUMB_MAX_BYTES


def test_without_og_tags_the_title_and_twitter_tags_are_used(pv):
    pv["http"].get("https://news.example/plain", body=_page(
        "<title> Plain &quot;page&quot; </title>"
        '<meta name="twitter:description" content="From twitter">'
        '<meta name="twitter:image" content="https://cdn.example/t.png">'), headers={"Content-Type": "text/html"})
    pv["http"].get("https://cdn.example/t.png", body=_png(800, 400), headers={"Content-Type": "image/png"})
    body = _ask(pv, "https://news.example/plain").json()
    assert body["title"] == 'Plain "page"'
    assert body["description"] == "From twitter"
    assert body["site_name"] == "news.example"
    assert _thumb_size(body["image"])[0] == 480


def test_a_page_in_another_charset_is_read_in_it(pv):
    html = _page('<meta charset="iso-8859-1"><meta property="og:title" content="Caf\xe9">').encode("latin-1")
    pv["http"].get("https://news.example/latin", body=html, headers={"Content-Type": "text/html"})
    assert _ask(pv, "https://news.example/latin").json()["title"] == "Caf\xe9"


def test_long_titles_and_descriptions_are_cut(pv):
    pv["http"].get("https://news.example/long", body=_page(
        f'<meta property="og:title" content="{"t" * 1000}"><meta property="og:description" content="{"d" * 5000}">'),
        headers={"Content-Type": "text/html"})
    body = _ask(pv, "https://news.example/long").json()
    assert len(body["title"]) == preview.MAX_TITLE_CHARS
    assert len(body["description"]) == preview.MAX_DESCRIPTION_CHARS


def test_a_page_with_nothing_to_show_is_a_404(pv):
    pv["http"].get("https://news.example/empty", body="<html><body>hi</body></html>", headers={"Content-Type": "text/html"})
    assert _ask(pv, "https://news.example/empty").status_code == 404


def test_a_picture_that_cannot_be_fetched_leaves_the_preview_without_one(pv):
    pv["http"].get("https://news.example/story", body=_page(
        '<meta property="og:title" content="Story"><meta property="og:image" content="https://cdn.example/gone.jpg">'),
        headers={"Content-Type": "text/html"})
    pv["http"].get("https://cdn.example/gone.jpg", status=404)
    body = _ask(pv, "https://news.example/story").json()
    assert body["title"] == "Story" and body["image"] is None


def test_a_page_that_cannot_be_read_is_a_502(pv):
    pv["http"].get("https://news.example/down", status=500)
    assert _ask(pv, "https://news.example/down").status_code == 502


# --- Videos and audio -------------------------------------------------------------


@pytest.mark.parametrize(("url", "video_id"), [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=42", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/aBcDeFgHiJ0", "aBcDeFgHiJ0"),
    ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/watch?v=tooshort", None),
    ("https://www.youtube.com/channel/UCabc", None),
    ("https://notyoutube.com/watch?v=dQw4w9WgXcQ", None),
])
def test_youtube_addresses(url, video_id):
    assert preview.youtube_id(url) == video_id


@pytest.mark.parametrize(("url", "embed"), [
    ("https://vimeo.com/76979871", {"provider": "vimeo", "id": "76979871"}),
    ("https://vimeo.com/76979871/abcdef1234", {"provider": "vimeo", "id": "76979871", "hash": "abcdef1234"}),
    ("https://player.vimeo.com/video/76979871?h=abcdef1234", {"provider": "vimeo", "id": "76979871", "hash": "abcdef1234"}),
    ("https://vimeo.com/channels/staffpicks/76979871", {"provider": "vimeo", "id": "76979871"}),
    ("https://vimeo.com/about", None),
])
def test_vimeo_addresses(url, embed):
    assert preview.vimeo_embed(url) == embed


@pytest.mark.parametrize(("url", "kind", "mime"), [
    ("https://media.example/clip.mp4", "video", "video/mp4"),
    ("https://media.example/clip.WEBM", "video", "video/webm"),
    ("https://media.example/a/b/home.mov?x=1", "video", "video/quicktime"),
    ("https://media.example/song.mp3", "audio", "audio/mpeg"),
    ("https://media.example/voice.m4a", "audio", "audio/mp4"),
    ("https://media.example/x.aac", "audio", "audio/aac"),
    ("https://media.example/x.ogg", "audio", "audio/ogg"),
    ("https://media.example/x.opus", "audio", "audio/ogg"),
    ("https://media.example/x.wav", "audio", "audio/wav"),
])
def test_a_direct_media_file_is_previewed_without_downloading_it(pv, url, kind, mime):
    resp = _ask(pv, url)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == kind
    assert body["embed"] == {"provider": "file", "src": url, "mime": mime}
    assert body["image"] is None and body["site_name"] == "media.example"
    assert _requested(pv) == []


def test_a_youtube_link_is_a_video_with_its_thumbnail(pv):
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    pv["http"].get(url, body=_page('<meta property="og:title" content="A song">'
                                   '<meta property="og:image" content="https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg">'),
                   headers={"Content-Type": "text/html"})
    pv["http"].get("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", body=_jpeg(1280, 720), headers={"Content-Type": "image/jpeg"})
    body = _ask(pv, url).json()
    assert body["kind"] == "video"
    assert body["embed"] == {"provider": "youtube", "id": "dQw4w9WgXcQ"}
    assert body["title"] == "A song"
    assert _thumb_size(body["image"])[:2] == (480, 270)


def test_a_youtube_link_whose_page_cannot_be_read_still_gets_a_preview(pv):
    pv["http"].get("https://youtu.be/dQw4w9WgXcQ", status=429)
    pv["http"].get("https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", body=_jpeg(480, 360), headers={"Content-Type": "image/jpeg"})
    body = _ask(pv, "https://youtu.be/dQw4w9WgXcQ").json()
    assert body["kind"] == "video" and body["embed"]["id"] == "dQw4w9WgXcQ"
    assert body["title"] == "YouTube video"
    assert body["image"].startswith("data:image/jpeg;base64,")


def test_a_vimeo_link_is_a_video(pv):
    pv["http"].get("https://vimeo.com/76979871", body=_page('<meta property="og:title" content="Trailer">'), headers={"Content-Type": "text/html"})
    body = _ask(pv, "https://vimeo.com/76979871").json()
    assert body["kind"] == "video" and body["embed"] == {"provider": "vimeo", "id": "76979871"}


def test_og_video_and_og_audio_point_at_playable_files(pv):
    pv["http"].get("https://news.example/watch", body=_page(
        '<meta property="og:title" content="Clip"><meta property="og:video:secure_url" content="https://media.example/c.mp4">'),
        headers={"Content-Type": "text/html"})
    pv["http"].get("https://news.example/listen", body=_page(
        '<meta property="og:title" content="Episode"><meta property="og:audio" content="https://media.example/e.mp3">'),
        headers={"Content-Type": "text/html"})
    video = _ask(pv, "https://news.example/watch").json()
    assert video["kind"] == "video" and video["embed"] == {"provider": "file", "src": "https://media.example/c.mp4", "mime": "video/mp4"}
    audio = _ask(pv, "https://news.example/listen").json()
    assert audio["kind"] == "audio" and audio["embed"] == {"provider": "file", "src": "https://media.example/e.mp3", "mime": "audio/mpeg"}


@pytest.mark.parametrize("meta", [
    '<meta property="og:video" content="http://lan.example/cam.mp4">',
    '<meta property="og:video:secure_url" content="https://ten.example/v.webm">',
    '<meta property="og:video" content="http://127.0.0.1/v.mp4">',
    '<meta property="og:video" content="http://[::1]/v.mp4">',
    '<meta property="og:video" content="http://nas.local/v.mp4">',
    '<meta property="og:audio" content="http://metadata.example/a.mp3">',
    '<meta property="og:audio:url" content="http://192.168.1.9/a.mp3">',
    '<meta property="og:audio" content="https://media.example:8443/a.mp3">',
])
def test_og_video_and_og_audio_on_a_private_host_are_dropped_like_og_image(pv, meta):
    pv["http"].get("https://news.example/sneaky", body=_page('<meta property="og:title" content="Sneaky">' + meta),
                   headers={"Content-Type": "text/html"})
    body = _ask(pv, "https://news.example/sneaky").json()
    assert body["title"] == "Sneaky"
    assert body["kind"] == "link" and body["embed"] is None


def test_an_og_video_that_is_an_arbitrary_player_page_stays_a_link(pv):
    pv["http"].get("https://news.example/player", body=_page(
        '<meta property="og:title" content="Somewhere"><meta property="og:video" content="https://news.example/embed/9">'),
        headers={"Content-Type": "text/html"})
    body = _ask(pv, "https://news.example/player").json()
    assert body["kind"] == "link" and body["embed"] is None


def test_an_address_that_answers_with_audio_is_an_audio_file(pv):
    pv["http"].get("https://media.example/stream/42", body=b"ID3" + b"\0" * 100, headers={"Content-Type": "audio/mpeg"})
    body = _ask(pv, "https://media.example/stream/42").json()
    assert body["kind"] == "audio"
    assert body["embed"] == {"provider": "file", "src": "https://media.example/stream/42", "mime": "audio/mpeg"}


# --- What may be fetched -------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://loopback.example/",
    "http://lan.example/",
    "https://ten.example/x",
    "http://metadata.example/latest/meta-data/",
    "http://cgnat.example/",
    "http://multicast.example/",
    "http://zero.example/",
    "http://reserved.example/",
    "http://v6loop.example/",
    "http://v6link.example/",
    "http://v6ula.example/",
    "http://mapped.example/",
    "http://mixed.example/",
    "http://127.0.0.1/",
    "http://127.1/",
    "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.0.1/admin",
    "http://localhost/",
    "http://printer.local/",
    "http://router.lan/",
    "http://news.example:8080/",
    "http://news.example:22/",
    "ftp://news.example/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,hi",
    "https://user:pass@news.example/",
])
def test_non_public_or_unusual_addresses_are_refused_without_a_request(pv, url):
    resp = _ask(pv, url)
    assert resp.status_code == 422, resp.text
    assert _requested(pv) == []


def test_a_redirect_to_a_private_address_is_refused_before_it_is_followed(pv):
    pv["http"].get("https://news.example/short", status=302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    pv["http"].get("https://news.example/short2", status=301, headers={"Location": "http://lan.example/admin"})
    assert _ask(pv, "https://news.example/short").status_code == 422
    assert _ask(pv, "https://news.example/short2").status_code == 422
    assert _requested(pv) == ["https://news.example/short", "https://news.example/short2"]


def test_a_redirect_to_a_public_address_is_followed(pv):
    pv["http"].get("https://news.example/short", status=302, headers={"Location": "/real"})
    pv["http"].get("https://news.example/real", body=_page('<meta property="og:title" content="Real">'), headers={"Content-Type": "text/html"})
    body = _ask(pv, "https://news.example/short").json()
    assert body["title"] == "Real" and body["url"] == "https://news.example/short"


def test_more_than_three_redirects_is_a_502(pv):
    for i in range(5):
        pv["http"].get(f"https://news.example/r{i}", status=302, headers={"Location": f"/r{i + 1}"})
    assert _ask(pv, "https://news.example/r0").status_code == 502
    assert len(_requested(pv)) == preview.MAX_REDIRECTS + 1


def test_a_preview_picture_on_a_private_address_is_not_fetched(pv):
    pv["http"].get("https://news.example/story", body=_page(
        '<meta property="og:title" content="Story"><meta property="og:image" content="http://lan.example/cam.jpg">'),
        headers={"Content-Type": "text/html"})
    body = _ask(pv, "https://news.example/story").json()
    assert body["image"] is None
    assert _requested(pv) == ["https://news.example/story"]


def test_the_connection_only_goes_to_addresses_that_passed(pv):
    """The resolver aiohttp connects through refuses a name that resolves anywhere non-public."""
    import asyncio

    resolver = preview._PublicResolver()
    good = asyncio.run(resolver.resolve("news.example", 443))
    assert [r["host"] for r in good] == ["93.184.216.34"]
    with pytest.raises(OSError):
        asyncio.run(resolver.resolve("mixed.example", 443))


@pytest.mark.parametrize(("address", "public"), [
    ("93.184.216.34", True),
    ("2606:2800:220:1:248:1893:25c8:1946", True),
    ("127.0.0.1", False),
    ("10.0.0.1", False),
    ("172.16.5.4", False),
    ("192.168.1.1", False),
    ("169.254.169.254", False),
    ("100.64.0.1", False),
    ("0.0.0.0", False),
    ("224.0.0.251", False),
    ("255.255.255.255", False),
    ("240.0.0.1", False),
    ("::1", False),
    ("::", False),
    ("fe80::1", False),
    ("fc00::1", False),
    ("ff02::1", False),
    ("::ffff:10.0.0.1", False),
    ("2002:0a00:0001::1", False),
    ("2001:0000:4136:e378:8000:63bf:3fff:fdd2", False),
    ("not an address", False),
])
def test_public_address(address, public):
    assert preview.public_address(address) is public


# --- How much is read -------------------------------------------------------------------


def test_only_the_first_part_of_a_page_is_read(pv, monkeypatch):
    import asyncio

    # The mocked transport cannot stream megabytes, so the cap is scaled down; the code path is the same.
    monkeypatch.setattr(preview, "MAX_HTML_BYTES", 8 * 1024)
    head = '<meta property="og:title" content="Big page">'
    pv["http"].get("https://news.example/big", body=_page(head, "x" * 60_000), headers={"Content-Type": "text/html"})
    _, _, body = asyncio.run(preview.fetch("https://news.example/big", accept="text/html", limit=preview.MAX_HTML_BYTES, truncate=True))
    assert len(body) == preview.MAX_HTML_BYTES
    pv["http"].get("https://news.example/big", body=_page(head, "x" * 60_000), headers={"Content-Type": "text/html"})
    assert _ask(pv, "https://news.example/big").json()["title"] == "Big page"


def test_a_picture_over_the_cap_is_not_downloaded_into_a_thumbnail(pv, monkeypatch):
    monkeypatch.setattr(preview, "MAX_IMAGE_BYTES", 16 * 1024)
    pv["http"].get("https://news.example/story", body=_page(
        '<meta property="og:title" content="Story"><meta property="og:image" content="https://cdn.example/huge.jpg">'),
        headers={"Content-Type": "text/html"})
    pv["http"].get("https://cdn.example/huge.jpg", body=b"\xff" * (preview.MAX_IMAGE_BYTES + 1), headers={"Content-Type": "image/jpeg"})
    assert _ask(pv, "https://news.example/story").json()["image"] is None


def test_a_decompression_bomb_makes_no_thumbnail():
    out = io.BytesIO()
    Image.new("1", (10_000, 10_000)).save(out, "PNG")
    assert preview.thumbnail(out.getvalue()) is None


def test_something_that_is_not_a_picture_makes_no_thumbnail():
    assert preview.thumbnail(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None
    assert preview.thumbnail(b"GIF89a broken") is None


def test_a_noisy_picture_still_fits_the_thumbnail_cap():
    import random

    rng = random.Random(1)
    im = Image.frombytes("RGB", (1600, 1600), bytes(rng.getrandbits(8) for _ in range(1600 * 1600 * 3)))
    out = io.BytesIO()
    im.save(out, "PNG")
    _, _, size = _thumb_size(preview.thumbnail(out.getvalue()))
    assert size <= preview.THUMB_MAX_BYTES


# --- Who may ask -----------------------------------------------------------------------


@pytest.mark.parametrize("caller", ["kid", "teen", "gran", "uncle", "nochat"])
def test_only_the_chat_s_parents_may_ask(pv, caller):
    resp = _ask(pv, "https://news.example/story", user=caller)
    assert resp.status_code in (403, 404)
    assert _requested(pv) == [] and pv["resolved"] == []


def test_a_signed_out_caller_is_refused(pv, monkeypatch):
    from services.gateway import main as gateway_main

    async def nobody(request, body=None):
        return None

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", nobody)
    assert _ask(pv, "https://news.example/story").status_code == 401


def test_a_chat_that_does_not_exist_is_a_404(pv):
    resp = pv["client"].post("/api/private-chat/chats/nosuchchat/link-preview", json={"url": "https://news.example/"}, headers=_as("alex"))
    assert resp.status_code == 404


@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": 42}, {"url": "https://news.example/" + "a" * 3000}, []])
def test_a_missing_or_bad_url_is_a_422(pv, payload):
    assert pv["client"].post(ROUTE, json=payload, headers=_as("alex")).status_code == 422


def test_previews_are_rate_limited_per_person(pv):
    for _ in range(preview.RATE_LIMIT):
        assert _ask(pv, "https://media.example/clip.mp4").status_code == 200
    resp = _ask(pv, "https://media.example/clip.mp4")
    assert resp.status_code == 429 and resp.headers["Retry-After"] == str(preview.RATE_WINDOW_S)
    # The other parent has their own allowance.
    assert _ask(pv, "https://media.example/clip.mp4", user="sam").status_code == 200


def test_without_the_rate_limit_s_store_nothing_is_fetched(pv):
    pv["counter"].down = True
    assert _ask(pv, "https://news.example/story").status_code == 503
    assert _requested(pv) == []


# --- What is kept and said -----------------------------------------------------------------


def test_the_address_is_never_logged_and_nothing_is_kept(pv, caplog):
    secret = "secret-trip-plans"
    pv["http"].get(f"https://news.example/{secret}", body=_page(
        '<meta property="og:title" content="Trip"><meta property="og:image" content="https://cdn.example/secret-pic.jpg">'),
        headers={"Content-Type": "text/html"})
    pv["http"].get("https://cdn.example/secret-pic.jpg", exception=ConnectionError(f"cannot reach https://cdn.example/{secret}"))
    pv["http"].get(f"https://news.example/{secret}-2", exception=ConnectionError(f"cannot reach https://news.example/{secret}-2"))
    before = sorted(p.relative_to(pv["root"]) for p in pv["root"].rglob("*"))
    with caplog.at_level(logging.DEBUG):
        assert _ask(pv, f"https://news.example/{secret}").status_code == 200
        assert _ask(pv, f"https://news.example/{secret}-2").status_code == 502
        assert _ask(pv, f"http://lan.example/{secret}").status_code == 422
    logged = "\n".join(record.getMessage() + (logging.Formatter().formatException(record.exc_info) if record.exc_info else "")
                       for record in caplog.records)
    assert secret not in logged
    assert "news.example" not in logged and "lan.example" not in logged
    assert sorted(p.relative_to(pv["root"]) for p in pv["root"].rglob("*")) == before


def test_the_limits_are_the_documented_ones():
    import asyncio

    import aiohttp

    assert preview.MAX_HTML_BYTES == 512 * 1024
    assert preview.THUMB_MAX_PX == 480 and preview.THUMB_MAX_BYTES == 60 * 1024
    assert preview.MAX_REDIRECTS == 3

    async def made():
        async with preview._session() as session:
            return session.timeout, session.trust_env, session.connector._resolver, session.cookie_jar

    timeout, trust_env, resolver, jar = asyncio.run(made())
    assert (timeout.sock_connect, timeout.total) == (3.0, 6.0)
    # No proxy from the environment, no cookies, and only checked addresses to connect to.
    assert trust_env is False
    assert isinstance(resolver, preview._PublicResolver)
    assert isinstance(jar, aiohttp.DummyCookieJar)
