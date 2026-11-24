"""HomeHub's HTTP server must not chunk a streamed response to an HTTP/1.0 client.

An Echo running echo-dot-assist fetches a reply's audio with HTTP/1.0 and reads the WAV header from
the first bytes of the body; httptools sent a chunk size line before "RIFF..." (chunked) and every streamed reply was
silent. h11 closes the connection instead.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import StreamingResponse
from starlette.routing import Route

MAIN = Path(__file__).resolve().parents[1] / "main.py"


def test_homehub_runs_uvicorn_with_h11():
    src = MAIN.read_text()
    assert re.search(r'^HTTP_PROTOCOL = "h11"$', src, re.M)
    assert "http=HTTP_PROTOCOL" in src


async def test_h11_streams_a_body_an_http10_reader_can_read():
    async def gen():
        yield b"RIFF\x00\x00\x00\x00WAVE"
        await asyncio.sleep(0)
        yield b"fmt "

    async def ep(_request):
        return StreamingResponse(gen(), media_type="audio/wav")

    app = Starlette(routes=[Route("/clip.wav", ep)])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, http="h11", log_level="error"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /clip.wav HTTP/1.0\r\nHost: hub\r\n\r\n")
        await writer.drain()
        raw = await reader.read()
        writer.close()
    finally:
        server.should_exit = True
        await task
    head, _, body = raw.partition(b"\r\n\r\n")
    assert b"transfer-encoding" not in head.lower()
    assert body.startswith(b"RIFF") and body[8:12] == b"WAVE"
