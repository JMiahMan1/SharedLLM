"""BUG-11: imageproxy caching, conditional requests and width forwarding.

* 200 responses carry ``Cache-Control: private, max-age=86400, immutable``
  and forward the upstream ``ETag``;
* the client's ``If-None-Match`` is forwarded upstream and an upstream 304
  is returned as 304 with no body;
* optional ``w=`` is forwarded to Music Assistant's imageproxy as ``size=``
  (param name confirmed from MA-generated production image URLs,
  ``.../imageproxy/<id>?size=512&fmt=jpg``; full resize end-to-end still
  (VERIFY) — see docs/MEDIA_UPSTREAM_API_NOTES.md);
* the image body is streamed instead of buffered.
"""
from services.gateway.tests.conftest_media import mock_upstream


def _ma_call(upstream):
    """Return (url, call) for the first request made to the MA host.

    The request log also contains the gateway's logging-service POSTs, so
    callers must filter by host.
    """
    for (_method, url), calls in upstream.requests.items():
        if "ma.local" in str(url):
            return str(url), calls[0]
    return None, None


def test_imageproxy_200_has_cache_headers_and_etag(client, upstream):
    import re

    pattern = re.compile(r"http://ma\.local:8095/imageproxy.*")
    upstream.get(pattern, payload="imgbytes", headers={"ETag": '"img-v1"'})

    resp = client.get("/api/media/imageproxy", params={"path": "/imageproxy?path=art.jpg"})

    assert resp.status_code == 200
    assert b"img" in resp.content
    assert resp.headers["Cache-Control"] == "private, max-age=86400, immutable"
    assert resp.headers.get("ETag") == '"img-v1"'


def test_imageproxy_forwards_if_none_match(client, upstream):
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")

    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "/imageproxy?path=art.jpg"},
        headers={"If-None-Match": '"img-v1"'},
    )

    assert resp.status_code == 200
    _url, call = _ma_call(upstream)
    assert call is not None, "MA upstream never contacted"
    headers = call.kwargs.get("headers") or {}
    assert headers.get("If-None-Match") == '"img-v1"'


def test_imageproxy_upstream_304_returned_as_304_empty(client, upstream):
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", status=304)

    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "/imageproxy?path=art.jpg"},
        headers={"If-None-Match": '"img-v1"'},
    )

    assert resp.status_code == 304
    assert resp.content == b""


def test_imageproxy_w_param_forwards_size_to_ma(client, upstream):
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")

    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "/imageproxy?path=art.jpg", "w": 128},
    )

    assert resp.status_code == 200
    url, _call = _ma_call(upstream)
    assert url is not None, "MA upstream never contacted"
    assert "size=128" in url
