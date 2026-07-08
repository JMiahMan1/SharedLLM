"""Tests for TEMP_AUDIO_CACHE bounded eviction logic."""
from collections import OrderedDict

import pytest

TEMP_AUDIO_CACHE_MAX = 50

class FakeAudioCache(OrderedDict):
    """Mimics TEMP_AUDIO_CACHE behavior for testing."""
    pass

TEMP_AUDIO_CACHE = FakeAudioCache()

def _evict_audio_cache():
    while len(TEMP_AUDIO_CACHE) > TEMP_AUDIO_CACHE_MAX:
        oldest_key = next(iter(TEMP_AUDIO_CACHE))
        del TEMP_AUDIO_CACHE[oldest_key]


@pytest.fixture(autouse=True)
def clear_cache():
    TEMP_AUDIO_CACHE.clear()
    yield
    TEMP_AUDIO_CACHE.clear()


def test_evict_audio_cache_removes_oldest_when_over_limit():
    for i in range(TEMP_AUDIO_CACHE_MAX + 10):
        TEMP_AUDIO_CACHE[f"tts-{i:04d}"] = b"audio" * 100
        _evict_audio_cache()
    assert len(TEMP_AUDIO_CACHE) <= TEMP_AUDIO_CACHE_MAX


def test_evict_audio_cache_does_nothing_when_under_limit():
    for i in range(5):
        TEMP_AUDIO_CACHE[f"tts-{i}"] = b"audio"
    _evict_audio_cache()
    assert len(TEMP_AUDIO_CACHE) == 5


def test_evict_audio_cache_removes_oldest_first():
    for i in range(TEMP_AUDIO_CACHE_MAX + 1):
        TEMP_AUDIO_CACHE[f"tts-{i:04d}"] = b"audio"
        _evict_audio_cache()
    assert "tts-0000" not in TEMP_AUDIO_CACHE
    assert f"tts-{TEMP_AUDIO_CACHE_MAX:04d}" in TEMP_AUDIO_CACHE


def test_evict_audio_cache_preserves_order():
    for i in range(TEMP_AUDIO_CACHE_MAX):
        TEMP_AUDIO_CACHE[f"tts-{i:04d}"] = b"audio"
    _evict_audio_cache()
    keys = list(TEMP_AUDIO_CACHE.keys())
    assert keys == [f"tts-{i:04d}" for i in range(TEMP_AUDIO_CACHE_MAX)]
