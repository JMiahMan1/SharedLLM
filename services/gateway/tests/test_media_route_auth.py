"""Media routes authenticate URLs with the signed ``?mt=`` token only (§7.4).

The legacy ``?token=<api key>`` on media stream and image routes was retired in
Phase 9 once the web UI and the Android app had moved to ``mt``. Other routes
that genuinely cannot send headers (log stream, artifact downloads, the
Sendspin and MA JSON-RPC websockets) still read ``?token=``.
"""
import re

from fastapi.testclient import TestClient
from starlette.requests import Request

from services.gateway import main
from services.gateway.tests.conftest_media import TEST_USER
from services.shared.media_token import sign

MA_IMAGE = re.compile(r"http://ma\.local:8095/imageproxy.*")


def _headerless(client) -> TestClient:
    """A client with no Authorization header; the ``client`` fixture's stubs stay active."""
    return TestClient(main.app)


def _request(path: str, query: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(), "headers": []})


def test_imageproxy_accepts_a_signed_media_token(client, upstream):
    upstream.get(MA_IMAGE, payload="imgbytes")
    mt, _ = sign(TEST_USER)

    resp = _headerless(client).get(
        "/api/media/imageproxy",
        params={"path": "/imageproxy?path=art.jpg", "user": TEST_USER, "mt": mt},
    )

    assert resp.status_code == 200
    assert b"img" in resp.content


def test_imageproxy_rejects_a_tampered_media_token(client, upstream):
    upstream.get(MA_IMAGE, payload="imgbytes")
    mt, _ = sign("someone-else")

    resp = _headerless(client).get(
        "/api/media/imageproxy",
        params={"path": "/imageproxy?path=art.jpg", "user": TEST_USER, "mt": mt},
    )

    assert resp.status_code == 403


def test_imageproxy_refuses_the_api_key_in_the_query_string(client, upstream):
    upstream.get(MA_IMAGE, payload="imgbytes")

    resp = _headerless(client).get(
        "/api/media/imageproxy",
        params={"path": "/imageproxy?path=art.jpg", "token": "test-token"},
    )

    assert resp.status_code == 401


def test_ma_stream_refuses_the_api_key_in_the_query_string(client):
    resp = _headerless(client).get(
        "/api/media/stream/music-assistant",
        params={"uri": "library://track/1", "token": "test-token"},
    )

    assert resp.status_code == 401


def test_media_paths_ignore_query_token():
    assert main._query_api_key(_request("/api/media/imageproxy", "token=k")) == ""
    assert main._query_api_key(_request("/api/media/stream/music-assistant", "token=k")) == ""
    assert main._presented_api_key(_request("/api/media/events", "token=k")) == ""


def test_non_media_paths_still_read_query_token():
    assert main._query_api_key(_request("/api/logs/stream", "token=k")) == "k"
    assert main._presented_api_key(_request("/api/artifacts/a1/stream", "token=k")) == "k"
