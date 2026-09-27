"""BUG-05: imageproxy must only fetch full URLs whose host is configured.

A full URL whose path contains ``/imageproxy`` is fetched server-side as-is
(see media_imageproxy). Without a host allowlist that is an SSRF vector: any
requester can make the gateway fetch arbitrary hosts (e.g. cloud metadata
endpoints). Allowed hosts are the user's configured mass_url /
audiobookshelf_url / ha_url hosts.
"""
from services.gateway.tests.conftest_media import mock_upstream


def test_imageproxy_rejects_unknown_host(client, upstream):
    """A metadata-service URL must be rejected with 400, never fetched."""
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://169.254.169.254/imageproxy?x"},
    )
    assert resp.status_code == 400


def test_imageproxy_service_param_cannot_bypass_host_check(client, upstream):
    """service=ma must not allow fetching an unconfigured host as-is."""
    resp = client.get(
        "/api/media/imageproxy",
        params={
            "path": "http://169.254.169.254/imageproxy?x",
            "service": "ma",
        },
    )
    assert resp.status_code == 400


def test_imageproxy_proxies_configured_ma_host(client, upstream):
    """A full URL on the configured MA host is still fetched as-is."""
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://ma.local:8095/imageproxy?path=art.jpg"},
    )
    assert resp.status_code == 200
    assert b"img" in resp.content
