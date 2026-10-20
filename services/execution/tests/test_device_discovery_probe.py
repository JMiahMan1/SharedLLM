"""Port probes read a bounded text prefix, not the whole response body."""

import asyncio

from services.execution import device_discovery


class _Content:
    def __init__(self, body: bytes):
        self.body = body
        self.read_limit = None

    async def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.body[:limit]


class _Response:
    def __init__(self, body: bytes):
        self.content = _Content(body)


def test_probe_text_decodes_a_bounded_prefix():
    body = ("<html><title>ESPHome</title></html>" + "x" * 100).encode()
    response = _Response(body)

    text = asyncio.run(device_discovery._probe_text(response))

    assert "<title>ESPHome</title>" in text
    assert response.content.read_limit == device_discovery.PROBE_BODY_LIMIT


def test_probe_text_survives_bytes_that_are_not_utf8():
    response = _Response(b"\xff\xfeESPHome\x00")

    assert "ESPHome" in asyncio.run(device_discovery._probe_text(response))
