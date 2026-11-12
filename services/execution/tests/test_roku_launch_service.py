"""Launching a Roku app uses the service HA's Roku integration (and homehub) actually has."""
from __future__ import annotations

from services.execution.handlers import roku


async def test_roku_launch_is_play_media_with_an_app_id(monkeypatch):
    calls = []

    async def fake_call(ha_url, ha_token, domain, service, entity_id, data=None, **_kw):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    monkeypatch.setattr(roku.ha_client, "call_service", fake_call)
    res = await roku.roku_launch("http://hub.example.com:8123", "tok", "media_player.den_roku", "12")
    assert res.status == "SUCCESS"
    assert calls == [("media_player", "play_media", "media_player.den_roku",
                      {"media_content_id": "12", "media_content_type": "app"})]
