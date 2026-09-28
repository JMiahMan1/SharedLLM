"""BUG-26 / P2-T17: gateway proxies ABS playback-session HLS to devices.

Live ABS 2.x replaced ``/api/items/:id/stream`` with session playback:
``POST /api/items/:id/play[/episode]`` -> session, then
``GET /public/session/:sid/track/:i`` -> 302 -> an HLS playlist whose segment
URIs are relative to the ABS host. The gateway:

- ``GET /api/media/stream/abs-session/{sid}/{i}`` — mt-token auth, fetches the
  session track, and rewrites the m3u8's relative segment URIs to this
  gateway's own segment route (the ABS host never reaches the device).
- ``GET /api/media/stream/abs-session/{sid}/{i}/{segment}`` — proxies one
  segment's bytes from the ABS ``/hls`` route.
"""
from services.gateway.tests.conftest_media import mock_upstream
from services.shared.media_token import sign

M3U8 = (
    "#EXTM3U\n"
    "#EXT-X-VERSION:3\n"
    "#EXT-X-ALLOW-CACHE:NO\n"
    "#EXT-X-TARGETDURATION:6\n"
    "#EXT-X-MEDIA-SEQUENCE:0\n"
    "#EXT-X-PLAYLIST-TYPE:VOD\n"
    "#EXTINF:6,\n"
    "output-0.ts\n"
    "#EXTINF:6,\n"
    "output-1.ts\n"
)


def _mock_track(upstream, session_id: str = "sess1", track: int = 0):
    """Session track redirects to the ABS HLS playlist, which is served."""
    mock_upstream(
        upstream,
        "GET",
        f"http://abs.local:13378/public/session/{session_id}/track/{track}",
        status=302,
        headers={"Location": f"http://abs.local:13378/hls/{session_id}/output.m3u8"},
    )
    mock_upstream(
        upstream,
        "GET",
        f"http://abs.local:13378/hls/{session_id}/output.m3u8",
        body=M3U8,
        status=200,
        content_type="application/vnd.apple.mpegurl",
    )


class TestAbsSessionTrackEndpoint:
    def test_serves_rewritten_m3u8(self, client, upstream):
        _mock_track(upstream)
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 200
        assert "mpegurl" in resp.headers.get("content-type", "")
        body = resp.text
        assert "#EXTM3U" in body
        assert "#EXT-X-PLAYLIST-TYPE:VOD" in body
        # Segment URIs are rewritten to the gateway's own route (with mt) —
        # the ABS host never reaches the device.
        gateway_seg = (
            "http://testserver/api/media/stream/abs-session/sess1/0/output-0.ts"
            "?user=testuser&mt="
        )
        assert gateway_seg in body
        assert "abs.local" not in body
        # No bare relative segment line survives.
        assert "\noutput-0.ts\n" not in body

    def test_rejects_invalid_media_token(self, client, upstream):
        _mock_track(upstream)
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": "garbage"},
        )
        assert resp.status_code == 403

    def test_rejects_token_bound_to_another_user(self, client, upstream):
        _mock_track(upstream)
        mt, _exp = sign("otheruser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 403

    def test_passthrough_upstream_error_status(self, client, upstream):
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/missing/track/0",
            body="Not Found", status=404, content_type="text/plain",
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/missing/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 404
        # Upstream body passed through (a real unknown route returns JSON detail).
        assert resp.text == "Not Found"

    def test_abs_url_not_configured_returns_400(self, client, upstream, monkeypatch):
        from services.gateway import main

        async def _resolve(body):
            return {"user": "testuser"}

        monkeypatch.setattr(main, "resolve_identity", _resolve)
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 400


class TestAbsSessionPlaylistReadiness:
    """BUG-33: ABS writes the HLS playlist only once transcoding starts.

    A freshly started session answers ``/public/session/:sid/track/:i`` with a
    302 straight away, but the playlist it points at 404s for as long as ffmpeg
    needs to probe the source (a cold 8.8h audiobook took >25s live). The
    gateway must poll that URL instead of handing the device the 404.
    """

    def _fast_poll(self, monkeypatch, attempts: int = 3):
        from services.gateway import main

        monkeypatch.setattr(main, "ABS_PLAYLIST_MAX_ATTEMPTS", attempts)
        monkeypatch.setattr(main, "ABS_PLAYLIST_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(main, "ABS_PLAYLIST_READY_TIMEOUT", 5.0)

    def test_polls_until_the_playlist_appears(self, client, upstream, monkeypatch):
        self._fast_poll(monkeypatch)
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/sess1/track/0",
            status=302, headers={"Location": "http://abs.local:13378/hls/sess1/output.m3u8"},
        )
        # Not ready yet, not ready yet, then ready.
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body="Not Found", status=404, content_type="text/plain",
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body="Not Found", status=404, content_type="text/plain",
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body=M3U8, status=200, content_type="application/vnd.apple.mpegurl",
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 200
        assert "abs-session/sess1/0/output-0.ts" in resp.text

    def test_playlist_never_ready_is_a_504_naming_the_session(self, client, upstream, monkeypatch):
        self._fast_poll(monkeypatch, attempts=2)
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/sess1/track/0",
            status=302, headers={"Location": "http://abs.local:13378/hls/sess1/output.m3u8"},
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body="Not Found", status=404, content_type="text/plain", repeat=5,
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 504
        assert "sess1" in resp.text

    def test_root_relative_redirect_does_not_repeat_the_base_path(
        self, client, upstream, monkeypatch,
    ):
        """ABS redirects with /hls/... at the origin, not under its router base."""
        from services.gateway import main

        self._fast_poll(monkeypatch, attempts=1)

        async def _resolve(body):
            return {
                "user": "testuser",
                "audiobookshelf_url": "https://abs.example.com/audiobookshelf",
            }

        monkeypatch.setattr(main, "resolve_identity", _resolve)
        # The track request keeps the configured router base path; the redirect
        # target is at the origin.
        mock_upstream(
            upstream, "GET",
            "https://abs.example.com/audiobookshelf/public/session/sess1/track/1",
            status=302, headers={"Location": "/hls/sess1/output.m3u8"},
        )
        mock_upstream(
            upstream, "GET", "https://abs.example.com/hls/sess1/output.m3u8",
            body=M3U8, status=200, content_type="application/vnd.apple.mpegurl", repeat=3,
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/1",
            params={"user": "testuser", "mt": mt},
        )
        # Both URLs above are the only ones registered, and aioresponses raises
        # ClientConnectionError (-> 502) for an unregistered URL: reaching 200
        # proves the track kept its /audiobookshelf base path while the
        # redirect target resolved to the origin, not .../audiobookshelf/hls.
        assert resp.status_code == 200
        assert "abs-session/sess1/1/output-0.ts" in resp.text


class TestAbsSessionSegmentEndpoint:
    def test_proxies_segment_bytes(self, client, upstream):
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output-0.ts",
            body=b"ts-bytes", status=200, content_type="video/mp2t",
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0/output-0.ts",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 200
        assert resp.content == b"ts-bytes"
        assert "mp2t" in resp.headers.get("content-type", "")

    def test_rejects_invalid_media_token(self, client, upstream):
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output-0.ts",
            body=b"ts-bytes", status=200, content_type="video/mp2t",
        )
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0/output-0.ts",
            params={"user": "testuser", "mt": "garbage"},
        )
        assert resp.status_code == 403

    def test_abs_url_not_configured_returns_400(self, client, upstream, monkeypatch):
        from services.gateway import main

        async def _resolve(body):
            return {"user": "testuser"}

        monkeypatch.setattr(main, "resolve_identity", _resolve)
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0/output-0.ts",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 400
