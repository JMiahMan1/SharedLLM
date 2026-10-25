"""The Raven RAG warm-up reads its answer, so no connection is left open."""
import asyncio
from contextlib import asynccontextmanager

from services.gateway import main


def test_warmup_reads_the_response_on_the_shared_client(monkeypatch):
    seen: dict = {"read": False, "url": None}

    class _Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def read(self):
            seen["read"] = True
            return b"{}"

    class _Client:
        def post(self, url, **kw):
            seen["url"] = url
            return _Resp()

    @asynccontextmanager
    async def fake_shared():
        yield _Client()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(main, "shared_http_client", fake_shared)
    monkeypatch.setattr(main.asyncio, "sleep", no_sleep)
    asyncio.run(main._warm_rag_lesson_cache())
    assert seen["url"].endswith("/rag/search")
    assert seen["read"] is True
