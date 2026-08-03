"""Media artifact validation: verify a generated audio/video file is actually
the format its name claims to be and is structurally playable.

Raven produces media through several paths (TTSRequest interceptor, shell
tools like gTTS/ffmpeg, future video generation). A common silent failure is a
"successful" write that is really an HTML error page, an empty file, or a
container that does not match its extension. This module sniffs magic bytes and
container structure with zero dependencies, so the loop can fail loudly
instead of reporting a fake SUCCESS — the extension is a claim, the bytes are
the truth.
"""

from __future__ import annotations

import os
from typing import Any

AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".oga", ".opus", ".flac", ".aac", ".m4a", ".wma", ".aiff", ".aif"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# Canonical extension used when auto-renaming a file to match its real content.
FORMAT_EXTENSIONS = {
    "mp3": ".mp3",
    "wav": ".wav",
    "ogg": ".ogg",
    "flac": ".flac",
    "aac": ".aac",
    "m4a": ".m4a",
    "mp4": ".mp4",
    "mov": ".mov",
    "mkv": ".mkv",
    "webm": ".webm",
    "avi": ".avi",
    "mpeg": ".mpg",
    "mpegts": ".ts",
    "asf": ".wma",
    "aiff": ".aiff",
}

# Expected sniffer formats per extension. A file whose declared extension is not
# in this set for the detected format is a mismatch (a lie in its name).
_EXT_EXPECTED: dict[str, set[str]] = {
    ".mp3": {"mp3"},
    ".wav": {"wav"},
    ".ogg": {"ogg"},
    ".oga": {"ogg"},
    ".opus": {"ogg"},
    ".flac": {"flac"},
    ".aac": {"aac"},
    ".m4a": {"m4a", "mp4"},
    ".mp4": {"mp4", "m4a"},
    ".m4v": {"mp4", "m4a"},
    ".mov": {"mov", "mp4"},
    ".mkv": {"mkv"},
    ".webm": {"webm", "mkv"},
    ".avi": {"avi"},
    ".wmv": {"asf"},
    ".wma": {"asf"},
    ".mpg": {"mpeg", "mpegts"},
    ".mpeg": {"mpeg", "mpegts"},
    ".ts": {"mpegts"},
    ".m2ts": {"mpegts"},
    ".aiff": {"aiff"},
    ".aif": {"aiff"},
}

_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_ASF_MAGIC = b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"
_MAX_SCAN = 128 * 1024


def media_extension(path: str | None) -> str:
    """Return the lowercase extension (with dot) of a path, or '' when absent."""
    if not path:
        return ""
    name = str(path).split("/")[-1].split("\\")[-1]
    if "." not in name or name.startswith("."):
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def _is_adts(data: bytes) -> bool:
    # ADTS (AAC): syncword 0xFFF (byte1 top nibble 0xF), layer bits 00 at bits 2-1.
    return data[0] == 0xFF and (data[1] & 0xF0) == 0xF0 and ((data[1] >> 1) & 0x3) == 0


def _is_mp3_frame(data: bytes) -> bool:
    # MPEG audio frame sync 0xFFE.. plus a valid layer (01/10/11), not ADTS.
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0 and ((data[1] >> 3) & 0x3) != 0 and not _is_adts(data)


def sniff_media_format(data: bytes) -> str | None:
    """Detect the actual container format from magic bytes. None = unrecognized."""
    if len(data) < 8:
        return None
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:3] == b"ID3":
        return "mp3"
    if data[:4] == b"RIFF":
        if data[8:12] == b"WAVE":
            return "wav"
        if data[8:12] == b"AVI ":
            return "avi"
        return None
    if data[:4] == b"FORM" and data[8:12] == b"AIFF":
        return "aiff"
    if data[:8] == _ASF_MAGIC:
        return "asf"
    if data[:4] == _EBML_MAGIC:
        head = data[:4096]
        if b"webm" in head:
            return "webm"
        if b"matroska" in head:
            return "mkv"
        return "mkv"
    if data[:4] == b"\x00\x00\x01\xba":
        return "mpeg"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand == b"M4A ":
            return "m4a"
        if brand == b"qt  ":
            return "mov"
        return "mp4"
    if _is_adts(data):
        return "aac"
    if _is_mp3_frame(data):
        return "mp3"
    # MPEG transport stream: 0x47 sync at packet boundaries (188 bytes).
    if data[0] == 0x47 and len(data) > 188 and data[188] == 0x47:
        return "mpegts"
    return None


def expected_formats(path: str | None) -> set[str]:
    """Formats the declared extension could legitimately hold."""
    ext = media_extension(path)
    if not ext:
        return set()
    return set(_EXT_EXPECTED.get(ext, set()))


def playability_issues(data: bytes, fmt: str) -> list[str]:
    """Structural sanity checks per format. Empty list = structurally playable."""
    issues: list[str] = []
    n = len(data)
    if fmt == "mp3":
        if n < 128:
            issues.append("file too small to be a playable MP3")
        probe = data
        if data[:3] == b"ID3":
            tag_len = 0
            if n >= 10:
                tag_len = (
                    (data[6] & 0x7F) << 21
                    | (data[7] & 0x7F) << 14
                    | (data[8] & 0x7F) << 7
                    | (data[9] & 0x7F)
                )
            probe = data[10 + tag_len : 10 + tag_len + 4096]
        if not any(
            probe[i] == 0xFF and i + 1 < len(probe) and (probe[i + 1] & 0xE0) == 0xE0 and not _is_adts(probe[i : i + 2])
            for i in range(min(len(probe), 4096) - 1)
        ):
            issues.append("no MPEG audio frame sync found (not decodable MP3 data)")
    elif fmt == "wav":
        if n < 44:
            issues.append("file too small to be a WAV (missing header/data chunks)")
        elif b"data" not in data[:8192]:
            issues.append("no 'data' chunk found in WAV")
    elif fmt == "ogg":
        if n < 27:
            issues.append("truncated Ogg page header")
        elif data[4] != 0:
            issues.append("unsupported Ogg version byte")
    elif fmt == "flac":
        if n < 42:
            issues.append("truncated FLAC (missing STREAMINFO metadata block)")
        elif (data[4] & 0x7F) != 0:
            issues.append("FLAC metadata block does not begin with STREAMINFO")
    elif fmt == "aac":
        frame_len = ((data[3] & 0x03) << 11) | (data[4] << 3) | ((data[5] & 0xE0) >> 5)
        if frame_len < 7:
            issues.append("ADTS frame length field invalid")
        elif frame_len > n:
            issues.append("ADTS frame length exceeds file size")
    elif fmt in ("mp4", "m4a", "mov"):
        box_size = int.from_bytes(data[0:4], "big")
        if box_size < 8:
            issues.append("ftyp box size invalid")
        if n < 100:
            issues.append("file too small to be a playable MP4-family video")
        elif b"moov" not in data[:_MAX_SCAN] and b"mdat" not in data[:_MAX_SCAN] and b"moof" not in data[:_MAX_SCAN]:
            issues.append("no moov/mdat/moof box found (not a playable MP4-family file)")
    elif fmt in ("mkv", "webm"):
        if n < 64:
            issues.append("truncated Matroska/WebM header")
        elif b"matroska" not in data[:4096] and b"webm" not in data[:4096]:
            issues.append("EBML header missing Matroska/WebM document type")
    elif fmt == "avi":
        if n < 100:
            issues.append("file too small to be an AVI")
        elif b"hdrl" not in data[:16384] and b"movi" not in data[:65536]:
            issues.append("no hdrl/movi chunks found (not a playable AVI)")
    elif fmt == "mpeg":
        if n < 1024:
            issues.append("file too small to be MPEG program stream")
        elif data.count(b"\x00\x00\x01", 0, 65536) < 2:
            issues.append("fewer than 2 MPEG start codes found")
    elif fmt == "mpegts":
        if n < 376 or data[376] != 0x47:
            issues.append("MPEG-TS sync broken at packet boundaries")
    elif fmt == "asf":
        if n < 30 or b"ASF_" not in data[:4096]:
            issues.append("no ASF header object found")
    elif fmt == "aiff":
        if n < 12:
            issues.append("truncated AIFF header")
        elif b"COMM" not in data[:8192]:
            issues.append("no COMM chunk found in AIFF")
    return issues


def validate_media_bytes(data: bytes, path: str | None = None) -> dict[str, Any]:
    """Validate raw bytes against a file path's declared extension.

    Returns a dict with: valid, format, expected, mismatch, playable, issues,
    size. valid=False whenever the content is empty, unrecognized as any known
    audio/video container, structurally unplayable, or mismatched with the
    declared extension.
    """
    result: dict[str, Any] = {
        "valid": False,
        "format": None,
        "expected": [],
        "mismatch": False,
        "playable": False,
        "issues": [],
        "size": len(data or b""),
    }
    if not data:
        result["issues"].append("file is empty")
        return result

    fmt = sniff_media_format(data)
    result["format"] = fmt
    expected = expected_formats(path)
    result["expected"] = sorted(expected)
    if fmt is None:
        result["issues"].append(
            "content does not match any known audio/video container (empty, text, "
            "HTML error page, or unknown codec)"
        )
        return result

    if expected and fmt not in expected:
        result["mismatch"] = True
        result["issues"].append(
            f"file declares '{media_extension(path)}' but content is actually {fmt.upper()}"
        )

    issues = playability_issues(data, fmt)
    if issues:
        result["issues"].extend(issues)
    else:
        result["playable"] = True
        result["valid"] = not result["mismatch"]
    return result


def validate_media_file(path: str, max_bytes: int = 64 * 1024 * 1024) -> dict[str, Any]:
    """Read a file from disk (bounded) and validate it like validate_media_bytes."""
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return {
                "valid": False,
                "format": None,
                "expected": [],
                "mismatch": False,
                "playable": False,
                "issues": [f"file too large to validate ({size} bytes)"],
                "size": size,
            }
        if size == 0:
            return {
                "valid": False,
                "format": None,
                "expected": [],
                "mismatch": False,
                "playable": False,
                "issues": ["file is empty"],
                "size": 0,
            }
        with open(path, "rb") as fh:
            data = fh.read()
        return validate_media_bytes(data, path)
    except OSError as e:
        return {
            "valid": False,
            "format": None,
            "expected": [],
            "mismatch": False,
            "playable": False,
            "issues": [f"cannot read file: {e}"],
            "size": -1,
        }


def rewrite_media_extension(path: str, fmt: str) -> str:
    """Return the path with its extension replaced to match the real format.

    Only applies when the current extension actually claims a media format;
    renaming e.g. a .txt is never justified by media validation."""
    ext = media_extension(path)
    canonical = FORMAT_EXTENSIONS.get(fmt)
    if not ext or ext not in MEDIA_EXTS or not canonical or ext == canonical:
        return path
    return path[: -len(ext)] + canonical
