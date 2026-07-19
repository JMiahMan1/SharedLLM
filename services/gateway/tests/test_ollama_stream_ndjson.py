"""Regression tests for OllamaProvider NDJSON stream parsing.

Root cause (mission 14, false-completion): Ollama streams newline-delimited
JSON, but the provider parsed each raw ``iter_any()`` byte-chunk with a single
``json.loads``. When one chunk contained MULTIPLE JSON objects (``{...}\n{...}\n``)
``json.loads`` raised "Extra data" and BOTH objects were dropped -> silent token
loss -> garbled tool-call JSON -> the mission never executed its tools.

These tests feed pathological chunk boundaries through the real ``generate()``
code path and assert that no tokens are lost.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.llm_providers import OllamaProvider


def _ndjson(*contents: str, done_last: bool = True) -> list[str]:
    lines = []
    for i, c in enumerate(contents):
        obj = {"model": "test", "message": {"role": "assistant", "content": c}, "done": False}
        lines.append(json.dumps(obj))
    if done_last:
        lines.append(json.dumps({"model": "test", "message": {"content": ""}, "done": True}))
    return lines


class _FakeContent:
    """Mimics aiohttp response.content with a scripted iter_any() byte stream."""

    def __init__(self, byte_chunks: list[bytes]):
        self._chunks = byte_chunks

    async def iter_any(self):
        for c in self._chunks:
            yield c

    async def read(self):
        return b"".join(self._chunks)


class _FakeResponse:
    def __init__(self, byte_chunks: list[bytes], status: int = 200):
        self.status = status
        self.content = _FakeContent(byte_chunks)

    def raise_for_status(self):
        return None

    async def text(self):
        return ""


def _mock_client(byte_chunks: list[bytes]) -> MagicMock:
    client = MagicMock()

    @asynccontextmanager
    async def _post(*args, **kwargs):
        yield _FakeResponse(byte_chunks)

    client.post = _post
    # slot check (_wait_for_slot): pretend /api/ps is unreachable -> proceed.
    client.get = AsyncMock(side_effect=Exception("no /api/ps"))
    return client


async def _run(byte_chunks: list[bytes]) -> str:
    provider = OllamaProvider(base_url="http://fake:11434")
    collected: list[str] = []

    async def cb(piece: str):
        collected.append(piece)

    client = _mock_client(byte_chunks)

    @asynccontextmanager
    async def _fake_shared_client():
        yield client

    # generate() does `from services.gateway.main import shared_http_client`
    # at call time, so patch it on that module.
    with patch("services.gateway.main.shared_http_client", _fake_shared_client):
        result = await provider.generate(
            model="test",
            messages=[{"role": "user", "content": "hi"}],
            chunk_callback=cb,
        )
    return result


@pytest.mark.asyncio
async def test_multiple_json_objects_in_one_chunk_not_dropped():
    lines = _ndjson("Hello", " world")
    # Pack ALL objects into a single byte chunk (the exact failure shape).
    one_chunk = ("\n".join(lines) + "\n").encode()
    result = await _run([one_chunk])
    assert "Hello world" in result, f"tokens dropped; got {result!r}"


@pytest.mark.asyncio
async def test_json_object_split_across_chunks_reassembled():
    lines = _ndjson("Raven", "Rocks")
    blob = ("\n".join(lines) + "\n").encode()
    # Split at an arbitrary mid-object byte boundary.
    mid = len(blob) // 3
    chunks = [blob[:mid], blob[mid:2 * mid], blob[2 * mid:]]
    result = await _run(chunks)
    assert "RavenRocks" in result, f"reassembly failed; got {result!r}"


@pytest.mark.asyncio
async def test_trailing_object_without_newline_flushed():
    obj = json.dumps({"message": {"content": "tail"}, "done": False})
    # No trailing newline and no done marker on its own line.
    result = await _run([obj.encode()])
    assert "tail" in result, f"trailing object lost; got {result!r}"
