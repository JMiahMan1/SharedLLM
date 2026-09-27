"""BUG-08: the port-8888 media file server must require a signed media token.

The server had no auth and guessable ids (``vid-`` + 8 hex). Per §7.4 these
URLs are fetched header-less by devices (HA, Roku, browser), so auth is a
short-lived signed media token in the query string (``?user=...&mt=...``):
missing/expired/tampered token → 403, valid → 200. In-progress ``.part`` files
stay served (progressive playback — owner decision) but behind the same auth.
"""
import os
import secrets
import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import services.execution.main as exec_main
from services.shared.media_token import sign, verify


def _client() -> TestClient:
    return TestClient(exec_main.create_media_server_app())


def _authed() -> dict:
    mt, _exp = sign("testuser")
    return {"user": "testuser", "mt": mt}


def test_media_file_server_requires_token():
    resp = _client().get("/media/whatever")
    assert resp.status_code == 403


def test_media_file_server_rejects_expired_token():
    mt, _exp = sign("testuser", now=time.time() - 7200)
    resp = _client().get("/media/whatever", params={"user": "testuser", "mt": mt})
    assert resp.status_code == 403


def test_media_file_server_serves_with_valid_token():
    media_id = secrets.token_urlsafe(16)
    exec_main.TEMP_AUDIO_CACHE[media_id] = b"wav-bytes"
    try:
        resp = _client().get(f"/media/{media_id}", params=_authed())
        assert resp.status_code == 200
        assert resp.content == b"wav-bytes"
    finally:
        exec_main.TEMP_AUDIO_CACHE.pop(media_id, None)


def test_media_file_server_serves_part_files_behind_auth():
    """Progressive playback: .part files ARE served (mid-download), but only
    with a valid signed media token (never without auth)."""
    media_id = secrets.token_urlsafe(16)
    part_path = os.path.join(exec_main.TEMP_MEDIA_DIR, f"{media_id}.mp4.part")
    with open(part_path, "wb") as f:
        f.write(b"partial-bytes")
    try:
        assert _client().get(f"/media/{media_id}").status_code == 403
        resp = _client().get(f"/media/{media_id}", params=_authed())
        assert resp.status_code == 200
        assert resp.content == b"partial-bytes"
    finally:
        os.unlink(part_path)


def test_media_file_server_unknown_id_is_404():
    resp = _client().get(f"/media/{secrets.token_urlsafe(16)}", params=_authed())
    assert resp.status_code == 404


def test_media_file_url_carries_valid_signed_token():
    from services.execution.media_links import media_file_url

    url = media_file_url("some-media-id", "testuser", "1.2.3.4")
    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "1.2.3.4:8888"
    assert parsed.path == "/media/some-media-id"
    query = parse_qs(parsed.query)
    assert query.get("user") == ["testuser"]
    mt = (query.get("mt") or [""])[0]
    assert mt, f"no mt= in {url}"
    assert verify(mt, "testuser") is True
    assert verify(mt, "otheruser") is False
    assert "token=" not in url
    # Device URLs have no refresh path: must survive long playback sessions.
    assert verify(mt, "testuser", now=time.time() + 6 * 3600) is True
    assert verify(mt, "testuser", now=time.time() + 13 * 3600) is False
