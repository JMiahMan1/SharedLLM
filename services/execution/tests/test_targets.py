"""Tests for room→speaker target resolution (no hardcoded entity IDs)."""
from __future__ import annotations

import pytest

from services.execution.targets import (
    _normalize_room_map,
    normalize_entity_id,
    resolve_targets,
    room_key,
)


class TestNormalize:
    def test_room_key(self):
        assert room_key("Living Room") == "living_room"
        assert room_key("  Kitchen ") == "kitchen"

    def test_normalize_entity_id_adds_domain(self):
        assert normalize_entity_id("kitchen_speaker") == "media_player.kitchen_speaker"
        assert normalize_entity_id("media_player.x") == "media_player.x"

    def test_normalize_room_map_string_value(self):
        m = _normalize_room_map({"Kitchen": "media_player.k"})
        assert m["kitchen"] == ["media_player.k"]

    def test_normalize_room_map_list_value(self):
        m = _normalize_room_map({"Living Room": ["media_player.a", "media_player.b"]})
        assert m["living_room"] == ["media_player.a", "media_player.b"]

    def test_normalize_room_map_rejects_garbage(self):
        assert _normalize_room_map("nope") == {}
        assert _normalize_room_map({"k": 123}) == {}


class TestResolveTargets:
    @pytest.mark.asyncio
    async def test_explicit_ids_win(self, monkeypatch):
        async def boom(*a, **k):
            raise AssertionError("must not fetch config when explicit ids given")

        monkeypatch.setattr("services.execution.targets.fetch_room_speakers", boom)
        out = await resolve_targets(entity_ids=["media_player.a", "b"])
        assert out == ["media_player.a", "media_player.b"]

    @pytest.mark.asyncio
    async def test_rooms_use_config_map(self, monkeypatch):
        async def fake_map():
            return {"kitchen": ["media_player.k_speaker"]}

        monkeypatch.setattr("services.execution.targets.fetch_room_speakers", fake_map)
        out = await resolve_targets(rooms=["Kitchen"], ha_url="", ha_token="")
        assert out == ["media_player.k_speaker"]

    @pytest.mark.asyncio
    async def test_empty_when_nothing_resolved(self, monkeypatch):
        async def fake_map():
            return {}

        monkeypatch.setattr("services.execution.targets.fetch_room_speakers", fake_map)
        out = await resolve_targets(rooms=["Nowhere"], ha_url="", ha_token="")
        assert out == []

    @pytest.mark.asyncio
    async def test_group_id_fallback(self, monkeypatch):
        async def fake_group(gid):
            assert gid == "main"
            return ["media_player.g1"]

        monkeypatch.setattr("services.execution.targets.fetch_media_group", fake_group)
        out = await resolve_targets(group_id="main")
        assert out == ["media_player.g1"]

    @pytest.mark.asyncio
    async def test_never_returns_hardcoded_defaults(self, monkeypatch):
        async def fake_map():
            return {}

        monkeypatch.setattr("services.execution.targets.fetch_room_speakers", fake_map)
        out = await resolve_targets(rooms=["kitchen"], ha_url="", ha_token="")
        assert out == []
        assert "media_player" not in "".join(out)
