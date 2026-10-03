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


def _mock_track(upstream, session_id: str = "sess1", track: int = 0, *, segment_ready: bool = True):
    """Session track redirects to the ABS HLS playlist, which is served.

    The first HLS segment is mocked too: the gateway now waits for segment 0
    before handing the playlist to the device, because a device that gets a 200
    playlist and then a 404 on its first segment treats the stream as fatally
    broken.
    """
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
    if segment_ready:
        mock_upstream(
            upstream,
            "GET",
            f"http://abs.local:13378/hls/{session_id}/output-0.ts",
            body=b"\x00" * 32,
            status=206,
            content_type="video/mp2t",
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
        # The first-segment wait is a second, independent poll; keep it instant
        # and bounded so the suite does not sit through real retry delays.
        monkeypatch.setattr(main, "ABS_FIRST_SEGMENT_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(main, "ABS_FIRST_SEGMENT_MAX_ATTEMPTS", 2)

    def _mock_segment_ready(self, upstream, session_id: str = "sess1"):
        """The playlist is only served once segment 0 exists."""
        mock_upstream(
            upstream, "GET", f"http://abs.local:13378/hls/{session_id}/output-0.ts",
            body=b"\x00" * 32, status=206, content_type="video/mp2t", repeat=5,
        )

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
        self._mock_segment_ready(upstream)
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
        # The first segment resolves at the origin too — not under the base path.
        mock_upstream(
            upstream, "GET", "https://abs.example.com/hls/sess1/output-0.ts",
            body=b"\x00" * 32, status=206, content_type="video/mp2t", repeat=3,
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


class TestAbsFirstSegmentReadiness:
    """The playlist appears before segment 0 does, and a 404 there is fatal.

    Live: ABS served the playlist (200) while ffmpeg was still writing
    ``output-0.ts``, so a device fetched a perfectly good playlist and then got a
    404 on its very first segment request — which TVs, speakers and cast devices
    treat as a dead stream. They do not retry. So the gateway must not return the
    playlist until the first segment resolves.
    """

    def _fast(self, monkeypatch, attempts: int = 3):
        from services.gateway import main

        monkeypatch.setattr(main, "ABS_PLAYLIST_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(main, "ABS_FIRST_SEGMENT_POLL_INTERVAL", 0.0)
        monkeypatch.setattr(main, "ABS_FIRST_SEGMENT_MAX_ATTEMPTS", attempts)

    def test_waits_for_the_first_segment_before_returning_the_playlist(
        self, client, upstream, monkeypatch
    ):
        self._fast(monkeypatch)
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/sess1/track/0",
            status=302, headers={"Location": "http://abs.local:13378/hls/sess1/output.m3u8"},
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body=M3U8, status=200, content_type="application/vnd.apple.mpegurl",
        )
        # Two 404s while ffmpeg catches up, then the segment lands.
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output-0.ts",
            body="not yet", status=404, content_type="text/plain", repeat=2,
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output-0.ts",
            body=b"\x00" * 32, status=206, content_type="video/mp2t",
        )

        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 200, resp.text
        assert "mpegurl" in resp.headers.get("content-type", "")
        assert "output-0.ts" in resp.text

    def test_gives_up_with_504_when_the_first_segment_never_arrives(
        self, client, upstream, monkeypatch
    ):
        self._fast(monkeypatch, attempts=2)
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/sess1/track/0",
            status=302, headers={"Location": "http://abs.local:13378/hls/sess1/output.m3u8"},
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body=M3U8, status=200, content_type="application/vnd.apple.mpegurl",
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output-0.ts",
            body="still transcoding", status=404, content_type="text/plain", repeat=10,
        )

        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 504
        # A device must be told the truth rather than handed a playlist it
        # cannot start.
        assert "transcoding" in resp.text
        assert "output-0.ts" not in resp.text

    def test_probe_asks_for_a_single_byte_range(self, client, upstream, monkeypatch):
        """Probing must not pull a multi-megabyte segment on every attempt."""
        self._fast(monkeypatch)
        _mock_track(upstream)
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 200
        # The mock answers 206 regardless of the Range header, so a dropped Range
        # would still pass above; assert on the request the probe actually makes.
        import inspect

        from services.gateway import main

        assert 'bytes=0-0' in inspect.getsource(main._await_abs_first_segment)

    def test_a_playlist_with_no_segments_is_not_served(self, client, upstream, monkeypatch):
        self._fast(monkeypatch)
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/sess1/track/0",
            status=302, headers={"Location": "http://abs.local:13378/hls/sess1/output.m3u8"},
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body="#EXTM3U\n#EXT-X-ENDLIST\n",
            status=200, content_type="application/vnd.apple.mpegurl",
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        # Nothing to wait for, so the (empty) playlist is served rather than 504.
        assert resp.status_code == 200
        assert "#EXT-X-ENDLIST" in resp.text

    def test_upstream_5xx_on_the_segment_is_retried_not_served(self, client, upstream, monkeypatch):
        self._fast(monkeypatch, attempts=2)
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/public/session/sess1/track/0",
            status=302, headers={"Location": "http://abs.local:13378/hls/sess1/output.m3u8"},
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output.m3u8",
            body=M3U8, status=200, content_type="application/vnd.apple.mpegurl",
        )
        mock_upstream(
            upstream, "GET", "http://abs.local:13378/hls/sess1/output-0.ts",
            body="boom", status=500, content_type="text/plain", repeat=10,
        )
        mt, _exp = sign("testuser")
        resp = client.get(
            "/api/media/stream/abs-session/sess1/0",
            params={"user": "testuser", "mt": mt},
        )
        assert resp.status_code == 504


class TestMaLibraryUriEndpoint:
    """POST /api/media/ma-library-uri — the ABS id -> MA library URI mapping.

    The browser used to send MA a hand-built ``audiobookshelf://<uuid>``, which MA
    rejects (no media controller for that media type) so the player stays silent.
    MA's numeric library id is only obtainable from MA itself, so the gateway owns
    the lookup.
    """

    def _resolver(self, monkeypatch, *, result=None, error=None):
        from services.shared import ma_library as ma_lib
        from services.shared.ma_library import ResolvedAudiobook

        calls: list[dict] = []

        async def _resolve(mass_url, mass_token, abs_item_id, title, **kw):
            calls.append(
                {"url": mass_url, "token": mass_token, "id": abs_item_id, "title": title}
            )
            if error is not None:
                raise error
            return result or ResolvedAudiobook(
                abs_item_id=abs_item_id, ma_uri="library://audiobook/260", title=title
            )

        monkeypatch.setattr(ma_lib, "resolve_audiobook_uri", _resolve)
        return calls

    def test_returns_the_resolved_uri(self, client, monkeypatch):
        self._resolver(monkeypatch)
        resp = client.post(
            "/api/media/ma-library-uri",
            json={"abs_item_id": "08d24fad-32a7-422d-8374-8501fdbe55d5", "title": "Narnia"},
        )
        assert resp.status_code == 200
        assert resp.json()["ma_uri"] == "library://audiobook/260"
        assert resp.json()["status"] == "SUCCESS"

    def test_uses_the_callers_ma_credentials(self, client, monkeypatch):
        calls = self._resolver(monkeypatch)
        client.post(
            "/api/media/ma-library-uri",
            json={"abs_item_id": "book-1", "title": "Narnia"},
        )
        assert calls[0]["url"] == "http://ma.local:8095"
        assert calls[0]["token"] == "test-mass-token"

    def test_missing_item_id_is_422(self, client, monkeypatch):
        self._resolver(monkeypatch)
        resp = client.post("/api/media/ma-library-uri", json={"abs_item_id": "", "title": "x"})
        assert resp.status_code == 422

    def test_lookup_miss_is_404_with_the_reason(self, client, monkeypatch):
        from services.shared.ma_library import MALibraryLookupError

        self._resolver(
            monkeypatch,
            error=MALibraryLookupError("MA does not have that book", reason="not_in_ma_library"),
        )
        resp = client.post("/api/media/ma-library-uri", json={"abs_item_id": "book-1", "title": "Nope"})
        assert resp.status_code == 404
        # The UI must be able to say *why*, not just "failed".
        assert "does not have that book" in resp.json()["detail"]

    def test_does_not_require_an_entity(self, client, monkeypatch):
        """This is a library lookup, not a device command."""
        self._resolver(monkeypatch)
        resp = client.post("/api/media/ma-library-uri", json={"abs_item_id": "book-1"})
        assert resp.status_code == 200


class TestMaLibraryUriEndpointRequiresAuth:
    """An anonymous caller must not reach MA's library index.

    Regression guard. `_resolve_ma_credentials` resolves through
    `resolve_identity`, which **falls back to the system default user for any
    string** — so before the strict gate this endpoint answered 200 for a request
    with no Authorization header at all, and for `Bearer sk-not-a-real-key`. That
    handed an unauthenticated caller a window onto the whole ABS library index
    (titles, covers, item ids). The gate must be `_require_authenticated`, which
    uses `_acting_identity` → `_resolve_strict_identity` and has no fallback.
    """

    def _resolver(self, monkeypatch):
        from services.shared import ma_library as ma_lib
        from services.shared.ma_library import ResolvedAudiobook

        async def _resolve(mass_url, mass_token, abs_item_id, title, **kw):
            return ResolvedAudiobook(abs_item_id=abs_item_id, ma_uri="library://audiobook/260", title=title)

        monkeypatch.setattr(ma_lib, "resolve_audiobook_uri", _resolve)

    def test_anonymous_is_401(self, monkeypatch):
        from fastapi.testclient import TestClient

        from services.gateway import main

        self._resolver(monkeypatch)
        resp = TestClient(main.app).post(
            "/api/media/ma-library-uri", json={"abs_item_id": "book-1", "title": "Narnia"}
        )
        assert resp.status_code == 401

    def test_bogus_api_key_is_401(self, monkeypatch):
        from fastapi.testclient import TestClient

        from services.gateway import main

        self._resolver(monkeypatch)
        resp = TestClient(main.app, headers={"Authorization": "Bearer sk-not-a-real-key"}).post(
            "/api/media/ma-library-uri", json={"abs_item_id": "book-1", "title": "Narnia"}
        )
        assert resp.status_code == 401

    def test_a_valid_key_still_works(self, client, monkeypatch):
        self._resolver(monkeypatch)
        resp = client.post("/api/media/ma-library-uri", json={"abs_item_id": "book-1", "title": "Narnia"})
        assert resp.status_code == 200

    def test_anonymous_never_reaches_the_ma_resolver(self, monkeypatch):
        """The gate must run before MA is contacted at all.

        Stronger than asserting call order in the source: it observes that the
        resolver is not invoked, so no amount of reordering can leak the lookup.
        """
        from fastapi.testclient import TestClient

        from services.gateway import main
        from services.shared import ma_library as ma_lib

        calls: list[str] = []

        async def _resolve(*a, **kw):
            calls.append("called")
            raise AssertionError("MA was contacted for an anonymous caller")

        monkeypatch.setattr(ma_lib, "resolve_audiobook_uri", _resolve)
        resp = TestClient(main.app).post(
            "/api/media/ma-library-uri", json={"abs_item_id": "book-1", "title": "Narnia"}
        )
        assert resp.status_code == 401
        assert calls == []


class TestAbsStreamClientsAreClosed:
    """Each HLS segment creates an aiohttp session; it must be released.

    Both byte-streaming generators used to finish without closing their
    ClientSession, so an unclosed connector was logged per segment and each one
    held its pool until the loop was collected. A 33-hour audiobook pulls
    thousands of segments, so this is a leak proportional to playback length.
    """

    def test_segment_route_closes_its_session(self, client, upstream):
        import inspect

        from services.gateway import main

        src = inspect.getsource(main.stream_abs_session_segment)
        gen = src.split("async def stream_generator")[1]
        assert "await cli.close()" in gen
        assert "finally:" in gen

    def test_playlist_byte_stream_closes_its_session(self, client, upstream):
        import inspect

        from services.gateway import main

        src = inspect.getsource(main.stream_abs_session)
        gen = src.split("async def stream_generator")[1]
        assert "await cli.close()" in gen

    def test_segment_bytes_still_stream(self, client, upstream):
        """The close must not break delivery."""
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
