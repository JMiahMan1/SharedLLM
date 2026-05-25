# services/tests/test_device_registry.py
"""Tests for device_registry.py (aiosqlite backend)."""
import os
import pytest
import tempfile

from execution import device_registry


@pytest.fixture(autouse=True)
def use_tmp_db(monkeypatch):
    """Redirect DB to temp file for each test."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DEVICE_REGISTRY_PATH", tmp.name)
    # Force module-level _db to reset
    device_registry._db = None
    yield
    device_registry._db = None
    try:
        os.unlink(tmp.name)
    except OSError:
        pass


@pytest.mark.asyncio
async def test_set_and_get_device():
    result = await device_registry.set_device(
        "media_player.test_tv",
        ip="192.168.1.100",
        mac="aa:bb:cc:dd:ee:ff",
        friendly_name="Test TV",
        integration="roku",
    )
    assert result["entity_id"] == "media_player.test_tv"
    assert result["ip"] == "192.168.1.100"
    assert result["mac"] == "aa:bb:cc:dd:ee:ff"

    fetched = await device_registry.get_device("media_player.test_tv")
    assert fetched is not None
    assert fetched["ip"] == "192.168.1.100"
    assert fetched["friendly_name"] == "Test TV"


@pytest.mark.asyncio
async def test_update_device():
    await device_registry.set_device(
        "media_player.test_tv",
        ip="192.168.1.100",
        friendly_name="Test TV",
    )
    await device_registry.set_device(
        "media_player.test_tv",
        ip="192.168.1.200",
        mac="aa:bb:cc:dd:ee:ff",
    )
    result = await device_registry.get_device("media_player.test_tv")
    assert result is not None
    assert result["ip"] == "192.168.1.200"
    assert result["mac"] == "aa:bb:cc:dd:ee:ff"
    assert result["friendly_name"] == "Test TV"


@pytest.mark.asyncio
async def test_invalidate_and_clear_stale():
    await device_registry.set_device("media_player.test_tv", ip="192.168.1.100")
    await device_registry.invalidate_device("media_player.test_tv", "timeout")
    result = await device_registry.get_device("media_player.test_tv")
    assert result is not None
    assert result["ip_stale"] == 1
    assert result["ip_stale_reason"] == "timeout"

    await device_registry.clear_stale("media_player.test_tv")
    result = await device_registry.get_device("media_player.test_tv")
    assert result is not None
    assert result["ip_stale"] == 0


@pytest.mark.asyncio
async def test_find_by_ip():
    await device_registry.set_device("media_player.tv1", ip="192.168.1.100")
    await device_registry.set_device("media_player.tv2", ip="192.168.1.101")
    assert await device_registry.find_by_ip("192.168.1.100") == "media_player.tv1"
    assert await device_registry.find_by_ip("192.168.1.999") is None


@pytest.mark.asyncio
async def test_find_by_mac():
    await device_registry.set_device("media_player.tv1", mac="aa:bb:cc:dd:ee:ff")
    assert await device_registry.find_by_mac("AA:BB:CC:DD:EE:FF") == "media_player.tv1"
    assert await device_registry.find_by_mac("00:00:00:00:00:00") is None


@pytest.mark.asyncio
async def test_list_devices():
    await device_registry.set_device("media_player.tv1", ip="192.168.1.1")
    await device_registry.set_device("media_player.tv2", ip="192.168.1.2")
    result = await device_registry.list_devices()
    assert len(result) == 2
    assert "media_player.tv1" in result
    assert "media_player.tv2" in result


@pytest.mark.asyncio
async def test_remove_device():
    await device_registry.set_device("media_player.test_tv", ip="192.168.1.100")
    assert await device_registry.remove_device("media_player.test_tv") is True
    assert await device_registry.get_device("media_player.test_tv") is None
    assert await device_registry.remove_device("media_player.nonexistent") is False


@pytest.mark.asyncio
async def test_needs_rediscovery():
    assert await device_registry.needs_rediscovery("media_player.new_tv") is True
    await device_registry.set_device("media_player.known_tv", ip="192.168.1.100")
    assert await device_registry.needs_rediscovery("media_player.known_tv") is False
    await device_registry.invalidate_device("media_player.known_tv")
    assert await device_registry.needs_rediscovery("media_player.known_tv") is True


@pytest.mark.asyncio
async def test_search_devices():
    await device_registry.set_device("media_player.gracies_tv", ip="192.168.1.100", friendly_name="Gracies TV")
    await device_registry.set_device("media_player.office_tv", ip="192.168.1.101", friendly_name="Office TV")
    results = await device_registry.search_devices("gracie")
    assert len(results) == 1
    assert results[0]["entity_id"] == "media_player.gracies_tv"

    results = await device_registry.search_devices("tv")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_metadata_merge():
    await device_registry.set_device(
        "media_player.test_tv",
        ip="192.168.1.100",
        metadata={"serial": "ABC123"},
    )
    await device_registry.set_device(
        "media_player.test_tv",
        metadata={"model": "50S435"},
    )
    result = await device_registry.get_device("media_player.test_tv")
    assert result is not None
    assert result["metadata"]["serial"] == "ABC123"
    assert result["metadata"]["model"] == "50S435"
