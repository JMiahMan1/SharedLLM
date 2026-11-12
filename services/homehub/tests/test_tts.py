"""tts.speak / tts.piper: Jarvis's voice, served from the hub, played on the speaker."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.homehub import tts
from services.homehub.core import Driver, HubError
from services.homehub.tests.conftest import make_hub


class FakePlayer(Driver):
    poll_interval = None

    def __init__(self, hub, entry):
        super().__init__(hub, entry)
        self.played = []

    async def async_call(self, entity, domain, service, data):
        self.played.append((entity.entity_id, service, data))


@pytest.fixture
def hub(monkeypatch):
    hub, entry = make_hub("cast", entities=[{"entity_id": "media_player.kitchen"}])
    hub.drivers[entry.entry_id] = FakePlayer(hub, entry)
    tts.register(hub)
    tts.reset()
    monkeypatch.setenv("HOMEHUB_EXTERNAL_URL", "http://hub.lan:8123")

    async def fake_synth(text, voice=None):
        return f"WAV:{text}:{voice}".encode()

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    return hub


async def test_tts_speak_plays_a_hub_url_on_the_speaker(hub):
    await hub.call_service("tts", "speak", {"entity_id": "tts.piper", "media_player_entity_id": "media_player.kitchen",
                                            "message": "Dinner is ready", "options": {"voice": "am_adam"}})
    player = next(iter(hub.drivers.values()))
    eid, service, data = player.played[-1]
    assert (eid, service) == ("media_player.kitchen", "play_media")
    assert data["media_content_id"].startswith("http://hub.lan:8123/api/tts_proxy/")
    assert data["announce"] is True
    token = data["media_content_id"].rsplit("/", 1)[1].removesuffix(".wav")
    assert tts.clip(token) == b"WAV:Dinner is ready:am_adam"


async def test_legacy_tts_piper_targets_the_media_player(hub):
    await hub.call_service("tts", "piper", {"entity_id": "media_player.kitchen", "message": "Hello"})
    assert next(iter(hub.drivers.values())).played[-1][0] == "media_player.kitchen"


async def test_tts_needs_a_message_and_a_speaker(hub):
    with pytest.raises(HubError):
        await hub.call_service("tts", "speak", {"entity_id": "tts.piper", "message": "x"})
    with pytest.raises(HubError):
        await hub.call_service("tts", "speak", {"media_player_entity_id": "media_player.kitchen", "message": ""})


def test_proxy_serves_fresh_clips_without_a_bearer_and_404s_unknown_tokens(tmp_path):
    from cryptography.fernet import Fernet

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store

    hub, _ = make_hub()
    store = Store(str(tmp_path / "h.db"), Fernet.generate_key().decode())
    tts.reset()
    tts._clips["abc"] = (10**12, b"RIFF")
    client = TestClient(build_app(Runtime(hub, store), Auth(store, "s")))
    r = client.get("/api/tts_proxy/abc.wav")
    assert r.status_code == 200 and r.content == b"RIFF" and r.headers["content-type"] == "audio/wav"
    assert client.get("/api/tts_proxy/nope.wav").status_code == 404


def _api_client(tmp_path):
    from cryptography.fernet import Fernet

    from services.homehub.api import build_app
    from services.homehub.auth import Auth
    from services.homehub.runtime import Runtime
    from services.homehub.store import Store

    hub, _ = make_hub()
    store = Store(str(tmp_path / "h.db"), Fernet.generate_key().decode())
    return TestClient(build_app(Runtime(hub, store), Auth(store, "s")))


def test_tts_get_url_speaks_and_returns_a_proxy_url(tmp_path, monkeypatch):
    """Music Assistant's text announcements ask HA for a spoken URL first."""
    monkeypatch.setenv("HOMEHUB_EXTERNAL_URL", "http://hub.example.com:8123")
    spoken = []

    async def fake_synth(text, voice=None):
        spoken.append((text, voice))
        return b"RIFFwav"

    monkeypatch.setattr(tts, "synthesize", fake_synth)
    tts.reset()
    client = _api_client(tmp_path)
    auth = {"Authorization": "Bearer s"}
    body = {"engine_id": "tts.piper", "message": "Dinner is ready", "options": {"voice": "am_adam"}}
    assert client.post("/api/tts_get_url", json=body).status_code == 401
    r = client.post("/api/tts_get_url", json=body, headers=auth)
    assert r.status_code == 200
    out = r.json()
    assert out["url"].startswith("http://hub.example.com:8123/api/tts_proxy/") and out["url"].endswith(out["path"])
    assert spoken == [("Dinner is ready", "am_adam")]
    assert client.get(out["path"]).content == b"RIFFwav"
    assert client.post("/api/tts_get_url", json={"engine_id": "tts.piper"}, headers=auth).status_code == 400
    assert client.post("/api/tts_get_url", json={"message": "x"}, headers=auth).status_code == 400


async def test_tts_fails_fast_without_a_lan_address(monkeypatch):
    """A loopback URL is useless to a speaker, so a hub with no LAN address says which setting is missing."""
    monkeypatch.delenv("HOMEHUB_EXTERNAL_URL", raising=False)
    monkeypatch.delenv("EXECUTION_EXTERNAL_HOST", raising=False)
    with pytest.raises(HubError, match="HOMEHUB_EXTERNAL_URL or EXECUTION_EXTERNAL_HOST"):
        await tts.make_clip("hello")
    monkeypatch.setenv("EXECUTION_EXTERNAL_HOST", "192.0.2.10")
    monkeypatch.delenv("EXECUTION_SVC_URL", raising=False)
    with pytest.raises(HubError, match="EXECUTION_SVC_URL"):
        await tts.make_clip("hello")
