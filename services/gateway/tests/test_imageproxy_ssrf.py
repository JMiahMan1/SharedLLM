"""BUG-05: imageproxy must only ever fetch hosts the user has configured.

A full URL whose path contains ``/imageproxy`` used to be fetched server-side
as-is for ANY host (an SSRF vector: e.g. cloud metadata endpoints). The gateway
now only contacts configured hosts:

* a full URL on an allowed host (mass/abs/ha) is fetched as before;
* a full URL on any other host is **rebased** onto the configured base for the
  service implied by the path — the unknown host is discarded and never
  contacted (SSRF stays closed) — which keeps mismatched-host MA/HA covers
  working (owner directive: never break player functionality);
* 400 only when the implied target service itself is not configured.
"""
from services.gateway.tests.conftest_media import mock_upstream


def _requested(upstream):
    return [str(key[1]) for key in upstream.requests]


def test_imageproxy_never_fetches_metadata_host(client, upstream):
    """A metadata-IP URL is rebased onto configured MA, never fetched as-is."""
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://169.254.169.254/imageproxy?path=art.jpg"},
    )
    assert resp.status_code == 200
    assert b"img" in resp.content
    assert not any("169.254.169.254" in u for u in _requested(upstream))
    assert any("ma.local" in u for u in _requested(upstream))


def test_imageproxy_service_param_cannot_bypass_host_check(client, upstream):
    """service=ma must not fetch an unconfigured host as-is either."""
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")
    resp = client.get(
        "/api/media/imageproxy",
        params={
            "path": "http://169.254.169.254/imageproxy?path=art.jpg",
            "service": "ma",
        },
    )
    assert resp.status_code == 200
    assert not any("169.254.169.254" in u for u in _requested(upstream))
    assert any("ma.local" in u for u in _requested(upstream))


def test_imageproxy_proxies_configured_ma_host(client, upstream):
    """A full URL on the configured MA host is still fetched as-is."""
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://ma.local:8095/imageproxy?path=art.jpg"},
    )
    assert resp.status_code == 200
    assert b"img" in resp.content


def test_imageproxy_rebases_mismatched_ma_host(client, upstream):
    """MA reports a cover on a host that differs from configured mass_url
    (docker alias / LAN IP) — must keep working via the configured base."""
    mock_upstream(upstream, "GET", "http://ma.local:8095/imageproxy", payload="imgbytes")
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://192.168.2.20:8095/imageproxy?path=art.jpg"},
    )
    assert resp.status_code == 200
    assert b"img" in resp.content
    assert not any("192.168.2.20" in u for u in _requested(upstream))
    assert any("ma.local" in u for u in _requested(upstream))


def test_imageproxy_rebases_external_ha_entity_picture(client, upstream):
    """HA entity_picture on an external host (Nabu Casa style
    /api/image/serve/...) must be fetched from configured ha_url."""
    mock_upstream(
        upstream, "GET", "http://ha.local:8123/api/image/serve/abc", payload="imgbytes"
    )
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "https://ha.example.ui.nabu.casa/api/image/serve/abc?ting=512"},
    )
    assert resp.status_code == 200
    assert b"img" in resp.content
    assert not any("nabu.casa" in u for u in _requested(upstream))
    assert any("ha.local" in u for u in _requested(upstream))


def test_imageproxy_400_when_target_service_unconfigured(client, upstream, monkeypatch):
    """Rebasing still fails closed when the target service has no base URL."""
    from services.gateway import main
    from services.gateway.tests.conftest_media import DEFAULT_IDENTITY

    identity = dict(DEFAULT_IDENTITY)
    identity["mass_url"] = ""

    async def _resolve_no_mass(body):
        return dict(identity)

    monkeypatch.setattr(main, "resolve_identity", _resolve_no_mass)
    resp = client.get(
        "/api/media/imageproxy",
        params={"path": "http://169.254.169.254/imageproxy?path=art.jpg"},
    )
    assert resp.status_code == 400
    assert "not configured" in resp.json()["detail"]
