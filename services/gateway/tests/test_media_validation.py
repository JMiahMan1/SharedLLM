"""Tests for media artifact validation (services/shared/media_validation.py)
and its wiring: the TTSRequest interceptor decision helpers, the gateway
post-write lint routing, and the execution-side media lint branch.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.shared.media_validation import (
    MEDIA_EXTS,
    expected_formats,
    media_extension,
    rewrite_media_extension,
    sniff_media_format,
    validate_media_bytes,
)


def _wav_bytes() -> bytes:
    return (
        b"RIFF"
        + struct.pack("<I", 36)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
        + b"data"
        + struct.pack("<I", 8)
        + b"\x00" * 8
    )


def _mp3_bytes(with_id3: bool = True) -> bytes:
    head = b"ID3\x03\x00\x00\x00\x00\x00\x00" if with_id3 else b""
    return head + b"\xff\xfb\x90\x64" + b"\x00" * 200


def _mp4_bytes(brand: bytes = b"isom") -> bytes:
    ftyp = struct.pack(">I", 24) + b"ftyp" + brand + struct.pack(">I", 0) + brand
    moov = struct.pack(">I", 8) + b"moov"
    return ftyp + moov + b"\x00" * 100


def _mkv_bytes(doc_type: bytes) -> bytes:
    return b"\x1a\x45\xdf\xa3\x80" + b"\x00" * 32 + doc_type + b"\x00" * 32


# ── sniffing ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSniffMediaFormat:
    @pytest.mark.parametrize(
        "data, expected",
        [
            (b"OggS\x00\x02\x00\x00\x00\x00", "ogg"),
            (b"fLaC" + b"\x00" * 8, "flac"),
            (_mp3_bytes(with_id3=True), "mp3"),
            (_mp3_bytes(with_id3=False), "mp3"),
            (_wav_bytes(), "wav"),
            (b"RIFF" + b"\x00" * 4 + b"AVI " + b"\x00" * 16, "avi"),
            (b"FORM\x00\x00\x00\x00AIFF" + b"\x00" * 4, "aiff"),
            (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11" + b"\x00" * 8, "asf"),
            (_mkv_bytes(b"matroska"), "mkv"),
            (_mkv_bytes(b"webm"), "webm"),
            (b"\x00\x00\x01\xba" + b"\x00" * 1024, "mpeg"),
            (_mp4_bytes(b"isom"), "mp4"),
            (_mp4_bytes(b"M4A "), "m4a"),
            (_mp4_bytes(b"qt  "), "mov"),
            (b"\xff\xf1\x50\x80" + b"\x00" * 16, "aac"),
            (b"\x47" + b"\x00" * 187 + b"\x11" + b"\x00" * 187, None),  # 0x47 alone is not a TS
            (b"\x47" + b"\x00" * 187 + b"\x47" + b"\x00" * 187, "mpegts"),
            (b"<html>error</html>", None),
            (b"", None),
        ],
    )
    def test_sniff(self, data, expected):
        assert sniff_media_format(data) == expected


# ── validate_media_bytes ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestValidateMediaBytes:
    def test_valid_wav_matches_extension(self):
        result = validate_media_bytes(_wav_bytes(), "narration.wav")
        assert result["valid"] is True
        assert result["format"] == "wav"
        assert result["playable"] is True
        assert result["issues"] == []

    def test_valid_mp3_id3(self):
        result = validate_media_bytes(_mp3_bytes(with_id3=True), "n.mp3")
        assert result["valid"] is True
        assert result["format"] == "mp3"

    def test_valid_mp3_raw(self):
        result = validate_media_bytes(_mp3_bytes(with_id3=False), "n.mp3")
        assert result["valid"] is True

    def test_valid_mp4(self):
        result = validate_media_bytes(_mp4_bytes(b"mp42"), "clip.mp4")
        assert result["valid"] is True
        assert result["format"] == "mp4"

    def test_m4a_brand_ok_for_m4a_extension(self):
        result = validate_media_bytes(_mp4_bytes(b"M4A "), "song.m4a")
        assert result["valid"] is True
        assert result["format"] == "m4a"

    def test_extension_mismatch_is_invalid(self):
        result = validate_media_bytes(_wav_bytes(), "narration.mp3")
        assert result["valid"] is False
        assert result["mismatch"] is True
        assert "actually WAV" in result["issues"][0]

    def test_mp3_named_wav_is_invalid(self):
        result = validate_media_bytes(_mp3_bytes(), "n.wav")
        assert result["valid"] is False
        assert result["mismatch"] is True

    def test_html_error_page_as_wav(self):
        result = validate_media_bytes(b"<html><body>502 Bad Gateway</body></html>", "out.wav")
        assert result["valid"] is False
        assert result["format"] is None
        assert any("HTML" in issue for issue in result["issues"])

    def test_empty_file(self):
        result = validate_media_bytes(b"", "out.mp4")
        assert result["valid"] is False
        assert result["issues"] == ["file is empty"]

    def test_truncated_wav(self):
        result = validate_media_bytes(_wav_bytes()[:20], "t.wav")
        assert result["valid"] is False
        assert result["format"] == "wav"
        assert any("too small" in issue for issue in result["issues"])

    def test_garbage_mp4_without_boxes(self):
        result = validate_media_bytes(b"\x00" * 512, "x.mp4")
        assert result["valid"] is False

    def test_text_file_with_video_extension(self):
        result = validate_media_bytes(b"def fake(): pass\n", "movie.mp4")
        assert result["valid"] is False

    def test_no_extension_but_valid_audio(self):
        result = validate_media_bytes(_wav_bytes(), None)
        assert result["valid"] is True
        assert result["format"] == "wav"

    def test_unknown_extension_never_mismatches(self):
        result = validate_media_bytes(_wav_bytes(), "data.bin")
        assert result["valid"] is True
        assert result["expected"] == []


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestHelpers:
    def test_media_extension(self):
        assert media_extension("a/b/narration.MP3") == ".mp3"
        assert media_extension("notes") == ""
        assert media_extension("notes.txt") == ".txt"
        assert media_extension(None) == ""
        assert ".mp3" in MEDIA_EXTS and ".mp4" in MEDIA_EXTS and ".wav" in MEDIA_EXTS

    def test_expected_formats(self):
        assert expected_formats("n.mp3") == {"mp3"}
        assert expected_formats("n.m4a") == {"m4a", "mp4"}
        assert expected_formats("n.xyz") == set()

    def test_rewrite_media_extension(self):
        assert rewrite_media_extension("narration.wav", "mp3") == "narration.mp3"
        assert rewrite_media_extension("narration.mp3", "mp3") == "narration.mp3"
        assert rewrite_media_extension("a/b/x.wav", "mp4") == "a/b/x.mp4"
        assert rewrite_media_extension("notes.txt", "mp3") == "notes.txt"
        assert rewrite_media_extension("plain", "mp3") == "plain"


# ── gateway wiring: run_post_write_lint routes media files ───────────────────


@pytest.mark.unit
class TestPostWriteLintMediaRouting:
    def test_media_extension_routes_to_lint_endpoint(self):
        from services.gateway import agent_loop

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            return_value=_aio_resp(200, {"status": "SUCCESS", "detail": {"passed": True, "verified": True, "results": []}})
        )
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(agent_loop, "shared_http_client", return_value=mock_ctx):
            import asyncio

            feedback = asyncio.run(agent_loop.run_post_write_lint("assets/narration.mp3", "http://exec:8008", "secret", MagicMock()))
        assert feedback is None
        called_url = mock_client.post.call_args[0][0]
        assert called_url.endswith("/execute/workspace_lint")
        payload = mock_client.post.call_args[1]["json"]
        assert payload["path"] == "assets/narration.mp3"

    def test_non_media_extension_skipped(self):
        from services.gateway import agent_loop

        mock_client = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(agent_loop, "shared_http_client", return_value=mock_ctx):
            import asyncio

            feedback = asyncio.run(agent_loop.run_post_write_lint("assets/image.png", "http://exec:8008", "secret", MagicMock()))
        assert feedback is None
        mock_client.post.assert_not_called()

    def test_invalid_media_surfaces_lint_failure(self):
        from services.gateway import agent_loop

        mock_client = MagicMock()
        mock_client.post = AsyncMock(
            return_value=_aio_resp(
                200,
                {
                    "status": "SUCCESS",
                    "detail": {
                        "passed": False,
                        "verified": True,
                        "results": [{"tool": "media-sniffer", "returncode": 1, "output": "INVALID — file declares '.mp3' but content is actually WAV"}],
                    },
                },
            )
        )
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(agent_loop, "shared_http_client", return_value=mock_ctx):
            import asyncio

            feedback = asyncio.run(agent_loop.run_post_write_lint("assets/narration.mp3", "http://exec:8008", "secret", MagicMock()))
        assert feedback is not None
        assert "LINT FAILED" in feedback
        assert "actually WAV" in feedback


# ── execution wiring: handle_workspace_lint media branch ─────────────────────


@pytest.mark.unit
class TestExecutionMediaLint:
    def test_media_branch_verifies_valid_file(self, tmp_path, monkeypatch):
        from services.execution.handlers import workspace as ws_handler

        monkeypatch.setattr(ws_handler, "WORKSPACE_ROOT", str(tmp_path))
        target = tmp_path / "narration.wav"
        target.write_bytes(_wav_bytes())
        req = SimpleNamespace(path=str(target), workspace_id=None, user_context=None, linter=None, fix=False)
        import asyncio

        result = asyncio.run(ws_handler.handle_workspace_lint(req))
        assert result.status == "SUCCESS"
        detail = result.detail
        assert detail["verified"] is True
        assert detail["passed"] is True
        assert detail["results"][0]["tool"] == "media-sniffer"
        assert "VALID" in detail["results"][0]["output"]

    def test_media_branch_rejects_mismatched_file(self, tmp_path, monkeypatch):
        from services.execution.handlers import workspace as ws_handler

        monkeypatch.setattr(ws_handler, "WORKSPACE_ROOT", str(tmp_path))
        target = tmp_path / "narration.mp3"
        target.write_bytes(_wav_bytes())
        req = SimpleNamespace(path=str(target), workspace_id=None, user_context=None, linter=None, fix=False)
        import asyncio

        result = asyncio.run(ws_handler.handle_workspace_lint(req))
        detail = result.detail
        assert detail["verified"] is True
        assert detail["passed"] is False
        assert "INVALID" in detail["results"][0]["output"]
        assert "actually WAV" in detail["results"][0]["output"]

    def test_media_branch_rejects_empty_file(self, tmp_path, monkeypatch):
        from services.execution.handlers import workspace as ws_handler

        monkeypatch.setattr(ws_handler, "WORKSPACE_ROOT", str(tmp_path))
        target = tmp_path / "empty.mp3"
        target.write_bytes(b"")
        req = SimpleNamespace(path=str(target), workspace_id=None, user_context=None, linter=None, fix=False)
        import asyncio

        result = asyncio.run(ws_handler.handle_workspace_lint(req))
        detail = result.detail
        assert detail["verified"] is True
        assert detail["passed"] is False
        assert "empty" in detail["results"][0]["output"]


class _Resp:
    def __init__(self, status, data):
        self.status = status
        self._data = data

    async def json(self):
        return self._data


def _aio_resp(status: int, data: dict):
    return _Resp(status, data)
