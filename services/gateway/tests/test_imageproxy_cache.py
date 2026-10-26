"""BUG-11: imageproxy caching, conditional requests and width forwarding.

* 200 responses carry ``Cache-Control: private, max-age=86400, immutable``
  and forward the upstream ``ETag``;
* the client's ``If-None-Match`` is forwarded upstream and an upstream 304
  is returned as 304 with no body;
* optional ``w=`` is snapped up to a ``size=`` Music Assistant's imageproxy
  accepts (0/80/160/256/512/1024 - anything else is a 400) and forwarded;
  the param name came from MA-generated production image URLs
  (``.../imageproxy/<id>?size=512&fmt=jpg``; see
  docs/MEDIA_UPSTREAM_API_NOTES.md);
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
    # MA only resizes to 0/80/160/256/512/1024 and refuses anything else with a
    # 400 ("Unsupported size 128: ..."), so a requested width is snapped up to
    # the next size it accepts.
    assert "size=160" in url


def test_imageproxy_ma_size_is_snapped_to_a_size_ma_accepts():
    """Every width the UI asks for becomes one MA's imageproxy will serve."""
    from services.gateway.main import _ma_image_size

    assert _ma_image_size(1) == 80
    assert _ma_image_size(44) == 80
    assert _ma_image_size(80) == 80
    assert _ma_image_size(81) == 160
    assert _ma_image_size(96) == 160  # the width a production 400 was logged for
    assert _ma_image_size(128) == 160
    assert _ma_image_size(160) == 160
    assert _ma_image_size(256) == 256
    assert _ma_image_size(257) == 512
    assert _ma_image_size(760) == 1024
    assert _ma_image_size(1024) == 1024
    assert _ma_image_size(4000) == 1024  # never above the largest MA offers
    assert _ma_image_size(-5) == 80

def test_imageproxy_reports_a_missing_cover_as_404(client, upstream):
    import re

    upstream.get(re.compile(r"http://ma\.local:8095/imageproxy.*"), status=404, body="nope")

    resp = client.get("/api/media/imageproxy", params={"path": "/imageproxy?path=art.jpg"})
    assert resp.status_code == 404


def test_imageproxy_does_not_follow_a_redirect(client, upstream):
    import re

    # The host allow-list only vets the URL we were asked for, so a redirect
    # must be reported rather than followed.
    upstream.get(
        re.compile(r"http://ma\.local:8095/imageproxy.*"),
        status=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        body="",
    )

    resp = client.get("/api/media/imageproxy", params={"path": "/imageproxy?path=art.jpg"})
    assert resp.status_code == 404


def test_imageproxy_reports_an_upstream_error_instead_of_streaming_it(client, upstream):
    import re

    upstream.get(re.compile(r"http://ma\.local:8095/imageproxy.*"), status=500, body="broken")

    resp = client.get("/api/media/imageproxy", params={"path": "/imageproxy?path=art.jpg"})
    assert resp.status_code == 500
    assert b"broken" not in resp.content
