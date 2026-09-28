"""BUG-07: ABS stream URLs handed to HA/MA devices must not leak the ABS key.

The raw ABS JWT used to be embedded in the stream URL
(``.../stream?format=mp4&token=<abs key>``), which devices persist in their
history (HA records media_content_id). The URL handed to the device must
instead route through the gateway with a short-lived signed media token
(``?user=...&mt=...``, §7.4) that expires.

Since ABS 2.x playback is session-based the device URL is built by
``get_session_track_url`` (``/api/media/stream/abs-session/{session}/{track}``);
the gateway needs no ABS key for it at all, because a session id is the
capability. The BUG-07 properties are asserted against that builder.
"""
import time
from urllib.parse import parse_qs, urlparse

from services.execution import abs_client
from services.shared.media_token import sign, verify

ABS_KEY = "secret-abs-jwt-key-000"


async def test_session_track_url_never_contains_abs_key_even_without_user():
    """The device URL carries no credential at all -- not even a stale one."""
    url = abs_client.get_session_track_url("sess-1", "testuser")
    assert ABS_KEY not in url
    assert "token=" not in url
    assert urlparse(url).path == "/api/media/stream/abs-session/sess-1/0"


async def test_session_track_url_is_signed_for_the_user_and_time_limited():
    """A valid, user-bound, expiring mt= rides along so the gateway can authorize."""
    url = abs_client.get_session_track_url("sess-42", "testuser", 1)
    assert urlparse(url).path == "/api/media/stream/abs-session/sess-42/1"

    query = parse_qs(urlparse(url).query)
    assert query.get("user") == ["testuser"]
    mt = (query.get("mt") or [""])[0]
    assert mt, f"no mt= in URL: {url}"
    assert verify(mt, "testuser") is True
    # Devices cannot refresh mt= mid-audiobook: the token must outlive
    # long playback sessions (DEVICE_TOKEN_TTL_SECONDS, not the 1h default).
    assert verify(mt, "testuser", now=time.time() + 6 * 3600) is True
    assert verify(mt, "testuser", now=time.time() + 13 * 3600) is False

    expired, _exp = sign("testuser", now=time.time() - 7200)
    assert verify(expired, "testuser") is False


async def test_session_track_url_uses_lan_host_for_devices():
    """HA/Cast fetch this URL from the LAN — must be the routable host.

    The docker-internal alias (`http://gateway:11435`) is not resolvable from
    devices, so when EXECUTION_EXTERNAL_HOST is configured (the same host used
    for every :8888 device URL) the gateway URL must be built from it.

    The expected host is read from abs_client — the module that actually builds
    the URL — not from services.config. Other tests reload services.config
    mid-session, which re-derives its constants from the current environment;
    importing it here compared two different import-time snapshots of the same
    name, so the assertion failed on a developer machine (where .env sets
    EXECUTION_EXTERNAL_HOST to a LAN address) while passing in CI.
    """
    url = abs_client.get_session_track_url("sess-1", "testuser")
    netloc = urlparse(url).netloc
    assert netloc.endswith(":11435")
    assert abs_client.EXECUTION_EXTERNAL_HOST, "test env must pin EXECUTION_EXTERNAL_HOST"
    assert netloc == f"{abs_client.EXECUTION_EXTERNAL_HOST}:11435"


async def test_session_track_url_falls_back_to_the_gateway_when_no_external_host(monkeypatch):
    """With EXECUTION_EXTERNAL_HOST unset the URL must use the gateway alias.

    Patching the attribute on abs_client (rather than the environment) is what
    makes this a real test: abs_client captured its constants at import, so an
    os.environ edit would not be seen without the patch.
    """
    monkeypatch.setattr(abs_client, "EXECUTION_EXTERNAL_HOST", "")
    url = abs_client.get_session_track_url("sess-1", "testuser")
    netloc = urlparse(url).netloc
    assert netloc == "gateway:11435"  # the docker-internal alias, unroutable but reachable in-mesh
