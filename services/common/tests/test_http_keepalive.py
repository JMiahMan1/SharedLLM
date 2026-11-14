"""Pooled connections are dropped before the services close them.

Every service runs uvicorn with its default 5 s keep-alive; aiohttp keeps an idle
connection 15 s by default, so a request 5-15 s after the last one reused a
connection the server had already closed ("Server disconnected").
"""
import asyncio

from services.common import http


def test_the_shared_client_drops_idle_connections_before_uvicorn_does():
    assert http.INTERNAL_KEEPALIVE_S < 5.0

    async def build():
        session = http._make(True)
        try:
            return session.connector._keepalive_timeout
        finally:
            await session.close()

    assert asyncio.run(build()) == http.INTERNAL_KEEPALIVE_S
