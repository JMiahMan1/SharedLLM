"""Fan-out announce tests: multi-target resolution + no hardcoded speakers."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.execution.announce_routes import run_announcement, run_broadcast
from services.execution.schemas import UserContext


def _ctx() -> UserContext:
    return UserContext(user="u1", is_admin=True, ha_url="http://ha", ha_token="t")


@pytest.mark.asyncio
async def test_announcement_fans_out_to_each_target(monkeypatch):
    called = []

    async def fake_announce(req):
        called.append(req.entity_id)
        return SimpleNamespace(status="SUCCESS", message="ok")

    async def fake_resolve(**kwargs):
        return ["media_player.a", "media_player.b"]

    monkeypatch.setattr(
        "services.execution.announce_routes._resolve_for_request",
        lambda **kw: fake_resolve(**kw),
    )

    req = SimpleNamespace(
        target_devices=None,
        target_entity_ids=["media_player.a", "media_player.b"],
        target_rooms=[],
        group_id=None,
        message="hello",
        volume=0.5,
    )
    result = await run_announcement(req, _ctx(), announce_fn=fake_announce)
    assert result["status"] == "SUCCESS"
    assert called == ["media_player.a", "media_player.b"]
    assert "2/2" in result["message"]


@pytest.mark.asyncio
async def test_announcement_fails_when_no_targets(monkeypatch):
    async def fake_resolve(**kwargs):
        return []

    monkeypatch.setattr(
        "services.execution.announce_routes._resolve_for_request",
        lambda **kw: fake_resolve(**kw),
    )
    req = SimpleNamespace(
        target_devices=[],
        target_entity_ids=[],
        target_rooms=["missing"],
        group_id=None,
        message="hi",
        volume=None,
    )
    result = await run_announcement(req, _ctx(), announce_fn=AsyncMockSuccess())
    assert result["status"] == "FAILURE"
    assert "No announcement targets" in result["message"]


@pytest.mark.asyncio
async def test_partial_failure_reports_counts(monkeypatch):
    async def fake_resolve(**kwargs):
        return ["media_player.ok", "media_player.bad"]

    monkeypatch.setattr(
        "services.execution.announce_routes._resolve_for_request",
        lambda **kw: fake_resolve(**kw),
    )

    async def flaky(req):
        if req.entity_id.endswith("bad"):
            return SimpleNamespace(status="FAILURE", message="boom")
        return SimpleNamespace(status="SUCCESS", message="ok")

    req = SimpleNamespace(
        target_devices=None,
        target_entity_ids=None,
        target_rooms=[],
        group_id=None,
        message="x",
        volume=None,
    )
    result = await run_announcement(req, _ctx(), announce_fn=flaky)
    assert result["status"] == "SUCCESS"
    assert "1/2" in result["message"]


@pytest.mark.asyncio
async def test_broadcast_uses_same_resolution(monkeypatch):
    seen = {}

    async def fake_resolve(**kwargs):
        seen.update(kwargs)
        return ["media_player.room_speaker"]

    monkeypatch.setattr(
        "services.execution.announce_routes._resolve_for_request",
        lambda **kw: fake_resolve(**kw),
    )

    async def fake_announce(req):
        return SimpleNamespace(status="SUCCESS", message="ok")

    req = SimpleNamespace(
        target_entity_ids=[],
        target_rooms=["kitchen"],
        group_id=None,
        message="broadcast",
        volume=0.4,
    )
    result = await run_broadcast(req, _ctx(), announce_fn=fake_announce)
    assert result["status"] == "SUCCESS"
    assert seen.get("rooms") == ["kitchen"]


class AsyncMockSuccess:
    async def __call__(self, req):
        return SimpleNamespace(status="SUCCESS", message="ok")
