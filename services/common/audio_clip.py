"""Facts about a spoken clip that the announce paths need, read from the bytes themselves.

``wav_duration`` reads a WAV header, so a caller waiting for an announcement to
end waits as long as the clip really is instead of a guessed number of seconds.
``byte_range`` turns an HTTP ``Range`` header into the slice to send, so the
clip servers can answer a speaker that asks for part of a file (Cast and Sonos
renderers do) with a real ``206``.
"""
from __future__ import annotations

import re
import struct

_RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.IGNORECASE)


def wav_duration(data: bytes | None) -> float | None:
    """Seconds of audio in a RIFF/WAVE clip, or None when ``data`` is not one.

    A streaming encoder may write 0 or 0xFFFFFFFF as the data chunk's size; the
    bytes actually present are used then.
    """
    if not data or len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos = 12
    byte_rate = 0
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4:pos + 8])
        body = pos + 8
        if cid == b"fmt " and body + 16 <= len(data):
            (byte_rate,) = struct.unpack("<I", data[body + 8:body + 12])
        elif cid == b"data":
            if not byte_rate:
                return None
            present = len(data) - body
            if size in (0, 0xFFFFFFFF) or size > present:
                size = present
            return size / byte_rate
        pos = body + size + (size & 1)
    return None


def byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """``(start, end)`` inclusive for a single ``bytes=`` range; None means send the whole body.

    Raises ValueError for a range that cannot be satisfied (the caller answers 416).
    A multi-range or malformed header is ignored, as RFC 9110 allows.
    """
    if not header:
        return None
    m = _RANGE_RE.match(header)
    if m is None:
        return None
    first, last = m.group(1), m.group(2)
    if not first and not last:
        return None
    if not first:  # suffix: the last N bytes
        n = int(last)
        if n == 0 or size == 0:
            raise ValueError("unsatisfiable range")
        return max(0, size - n), size - 1
    start = int(first)
    if start >= size:
        raise ValueError("unsatisfiable range")
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def clip_response(data: bytes, media_type: str, range_header: str | None, *, head: bool = False,
                  headers: dict[str, str] | None = None):
    """A Starlette response for an in-memory clip that honours ``Range`` and ``HEAD``.

    ``200`` with the whole clip, ``206`` with ``Content-Range`` for a satisfiable
    range, ``416`` for one past the end. A HEAD answer carries the same headers
    and no body.
    """
    from starlette.responses import Response

    size = len(data)
    base = {"Accept-Ranges": "bytes", **(headers or {})}
    try:
        span = byte_range(range_header, size)
    except ValueError:
        return Response(status_code=416, headers={**base, "Content-Range": f"bytes */{size}"})
    if span is None:
        body, status = data, 200
    else:
        start, end = span
        body, status = data[start:end + 1], 206
        base["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp = Response(body, status_code=status, media_type=media_type, headers=base)
    if head:
        resp.body = b""  # Content-Length still names the body a GET would send
    return resp
