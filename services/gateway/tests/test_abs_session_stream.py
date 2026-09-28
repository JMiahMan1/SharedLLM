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
