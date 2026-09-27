"""BUG-10: imageproxy must read identity's real audiobookshelf credential keys.

identity/schemas.py emits ``audiobookshelf_url`` / ``audiobookshelf_api_key``
(there is no ``abs_url`` / ``abs_api_key`` key), but the imageproxy ABS branch
read ``abs_url``/``abs_api_key`` — so every ABS cover request 400'd with
"Audiobookshelf not configured". The host-inference for full URLs had the same
wrong key, routing full ABS URLs (without /api/items/ in the path) to HA.
"""
from services.gateway.tests.conftest_media import mock_upstream


def _abs_call(upstream):
    """Return (url, call) for the first request made to the ABS host."""
    for (_method, url), calls in upstream.requests.items():
        if "abs.local" in str(url):
            return str(url), calls[0]
    return None, None


def _auth_header(call):
    headers = call.kwargs.get("headers") or {}
    return headers.get("Authorization") or headers.get("authorization")


def test_abs_cover_relative_path_uses_identity_key(client, upstream):
    """Relative /api/items/ cover is fetched from configured ABS with its key."""
    mock_upstream(
        upstream, "GET", "http://abs.local:13378/api/items/abc/cover", payload="imgbytes"
    )
    resp = client.get("/api/media/imageproxy", params={"path": "/api/items/abc/cover"})

    assert resp.status_code == 200
    assert b"img" in resp.content
    url, call = _abs_call(upstream)
    assert url is not None, f"ABS host never contacted; requested={ [str(k[1]) for k in upstream.requests] }"
    assert _auth_header(call) == "Bearer test-abs-key"


def test_abs_full_url_host_inference_uses_audiobookshelf_url(client, upstream):
    """A full ABS URL whose path lacks /api/items/ must still route to ABS
    (host match has to read audiobookshelf_url, not the non-existent abs_url)."""
    mock_upstream(
        upstream, "GET", "http://abs.local:13378/api/covers/abc", payload="imgbytes"
    )
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://abs.local:13378/api/covers/abc"},
    )

    assert resp.status_code == 200
    assert b"img" in resp.content
    url, call = _abs_call(upstream)
    assert url is not None, f"ABS host never contacted; requested={ [str(k[1]) for k in upstream.requests] }"
    assert "abs.local:13378" in url
    assert _auth_header(call) == "Bearer test-abs-key"
