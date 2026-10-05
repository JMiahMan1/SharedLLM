"""TTS cache bounds and periodic temp-media pruning (BUG-32)."""
import os
import time

import pytest

import services.execution.main as exec_main


@pytest.fixture(autouse=True)
def _clean_audio_cache():
    exec_main.TEMP_AUDIO_CACHE.clear()
    exec_main._TEMP_AUDIO_CACHE_BYTES = 0
    yield
    exec_main.TEMP_AUDIO_CACHE.clear()
    exec_main._TEMP_AUDIO_CACHE_BYTES = 0


def test_cache_evicts_oldest_beyond_max_entries(monkeypatch):
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_CACHE_MAX_ENTRIES", 2)

    exec_main.cache_temp_audio("a", b"1")
    exec_main.cache_temp_audio("b", b"22")
    exec_main.cache_temp_audio("c", b"333")

    assert list(exec_main.TEMP_AUDIO_CACHE) == ["b", "c"]
    assert exec_main._TEMP_AUDIO_CACHE_BYTES == 5


def test_cache_evicts_oldest_beyond_max_bytes(monkeypatch):
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_CACHE_MAX_ENTRIES", 10)
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_CACHE_MAX_BYTES", 4)

    exec_main.cache_temp_audio("a", b"123")
    exec_main.cache_temp_audio("b", b"456")

    assert list(exec_main.TEMP_AUDIO_CACHE) == ["b"]
    assert exec_main._TEMP_AUDIO_CACHE_BYTES == 3


def test_reading_refreshes_recency(monkeypatch):
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_CACHE_MAX_ENTRIES", 2)

    exec_main.cache_temp_audio("a", b"1")
    exec_main.cache_temp_audio("b", b"2")
    assert exec_main.get_temp_audio("a") == b"1"
    exec_main.cache_temp_audio("c", b"3")

    assert list(exec_main.TEMP_AUDIO_CACHE) == ["a", "c"]


def test_single_oversized_clip_is_kept(monkeypatch):
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_CACHE_MAX_BYTES", 1)

    exec_main.cache_temp_audio("big", b"12345")

    assert exec_main.get_temp_audio("big") == b"12345"


def test_replace_does_not_double_count_bytes(monkeypatch):
    exec_main.cache_temp_audio("a", b"123")
    exec_main.cache_temp_audio("a", b"45")

    assert exec_main._TEMP_AUDIO_CACHE_BYTES == 2
    assert exec_main.get_temp_audio("a") == b"45"


def test_prune_removes_only_old_media_files(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    tts_dir = media_dir / "tts"
    tts_dir.mkdir(parents=True)
    old_video = media_dir / "old.mp4"
    old_part = media_dir / "old.mp4.part"
    fresh_video = media_dir / "fresh.mp4"
    cookies = media_dir / "youtube_cookies.txt"
    old_tts = tts_dir / "old.wav"
    for path in (old_video, old_part, fresh_video, cookies, old_tts):
        path.write_bytes(b"x")
    long_ago = time.time() - 48 * 3600
    for path in (old_video, old_part, old_tts):
        os.utime(path, (long_ago, long_ago))

    monkeypatch.setattr(exec_main, "TEMP_MEDIA_DIR", str(media_dir))
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_DIR", str(tts_dir))

    removed = exec_main.prune_temp_media()

    assert sorted(removed) == sorted([str(old_video), str(old_part), str(old_tts)])
    assert fresh_video.exists()
    assert cookies.exists()


def test_prune_ignores_missing_directories(monkeypatch, tmp_path):
    monkeypatch.setattr(exec_main, "TEMP_MEDIA_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(exec_main, "TEMP_AUDIO_DIR", str(tmp_path / "nope" / "tts"))

    assert exec_main.prune_temp_media() == []
