"""BUG-07: ABS stream URLs handed to HA/MA devices must not leak the ABS key.

The raw ABS JWT used to be embedded in the stream URL
(``.../stream?format=mp4&token=<abs key>``), which devices persist in their
history (HA records media_content_id). The URL handed to the device must
instead route through the gateway with a short-lived signed media token
(``?user=...&mt=...``, §7.4) that expires.
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from services.execution.handlers import audiobookshelf as abs_handler
from services.shared.media_token import sign, verify

ABS_KEY = "secret-abs-jwt-key-000"


def _req() -> SimpleNamespace:
    return SimpleNamespace(
        entity_id="media_player.office",
        book_id="book-123",
        query=None,
        user_context=SimpleNamespace(
            user="testuser",
            ha_url="http://ha.local",
            ha_token="ha-token",
        ),
    )


async def test_handle_play_hands_ha_signed_gateway_url():
    """HA receives a gateway URL with a valid mt=, never the raw ABS key."""
    captured: dict = {}

    async def fake_call_service(ha_url, ha_token, domain, service, entity_id, data=None, **kw):
        captured.update(data or {})
        return {"ok": True}

    with (
        patch.object(
            abs_handler.ha_client,
            "get_state",
            new=AsyncMock(return_value={"state": "playing", "attributes": {}}),
        ),
        patch.object(
            abs_handler.ha_client,
            "call_service",
            new=AsyncMock(side_effect=fake_call_service),
        ),
        patch(
            "services.execution.handlers.roku.is_roku_device",
            new=AsyncMock(return_value=False),
        ),
    ):
        result = await abs_handler._handle_play("http://abs.local:13378", ABS_KEY, _req())

    assert result.status == "SUCCESS", result.message
    url = captured.get("media_content_id")
    assert url, f"no media_content_id handed to HA: {captured}"
    assert ABS_KEY not in url
    assert "/api/media/stream/audiobookshelf/book-123" in url

    # The signed token must be present, valid for the user, and time-limited.
    from urllib.parse import parse_qs, urlparse

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


async def test_stream_url_never_contains_abs_key_even_without_user():
    """get_stream_url must not fall back to embedding credentials."""
    url = await abs_handler.abs_client.get_stream_url("book-9", "testuser")
    assert ABS_KEY not in url
    assert "token=" not in url


async def test_stream_url_uses_lan_host_for_devices():
    """HA/Cast fetch this URL from the LAN — must be the routable host.

    The docker-internal alias (`http://gateway:11435`) is not resolvable from
    devices, so when EXECUTION_EXTERNAL_HOST is configured (the same host used
    for every :8888 device URL) the gateway URL must be built from it.
    """
    from urllib.parse import urlparse

    from services.config import EXECUTION_EXTERNAL_HOST

    url = await abs_handler.abs_client.get_stream_url("book-9", "testuser")
    netloc = urlparse(url).netloc
    assert netloc.endswith(":11435")
    if EXECUTION_EXTERNAL_HOST:
        assert netloc == f"{EXECUTION_EXTERNAL_HOST}:11435"
