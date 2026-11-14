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


async def test_the_engine_is_a_tts_entity_that_records_when_it_spoke(hub):
    assert hub.states.get(tts.TTS_ENTITY).state == "unknown"
    assert hub.states.get(tts.TTS_ENTITY).attributes["friendly_name"] == "Jarvis"
    await hub.call_service("tts", "speak", {"entity_id": tts.TTS_ENTITY, "media_player_entity_id": "media_player.kitchen",
                                            "message": "Hello"})
    assert hub.states.get(tts.TTS_ENTITY).state.startswith("20")
    tts.register(hub)  # registering again (a re-import) keeps the last spoken time
    assert hub.states.get(tts.TTS_ENTITY).state != "unknown"


async def test_a_voice_the_engine_does_not_have_fails_the_call(hub, monkeypatch):
    async def refuse(text, voice=None):
        raise HubError(f"speech synthesis failed: unknown voice {voice}")

    monkeypatch.setattr(tts, "synthesize", refuse)
    with pytest.raises(HubError, match="unknown voice not_a_voice"):
        await hub.call_service("tts", "speak", {"media_player_entity_id": "media_player.kitchen", "message": "x",
                                                "options": {"voice": "not_a_voice"}})
    assert not next(iter(hub.drivers.values())).played


# Cloned voices: alpaca's audio server (OpenVoice) speaks them. Every profile here is made up.

ALPACA = "http://alpaca.example.test:5000"
PROFILES = {"voices": [{"id": "pat-0a1b2c", "name": "Pat", "created": 2}, {"id": "lee-3d4e5f", "name": "Lee"}]}


@pytest.fixture
def alpaca(monkeypatch):
    from aioresponses import aioresponses

    monkeypatch.setattr(tts, "ALPACA_WEB_URL", ALPACA)
    with aioresponses() as mocked:
        yield mocked


def test_a_kokoro_voice_is_two_letters_and_a_word():
    for v in (None, "", "af_heart", "bm_fable,bm_lewis", "am_eric, af_kore", "zf_xiaobei"):
        assert tts.is_kokoro_voice(v), v
    for v in ("pat-0a1b2c", "Pat", "not_a_voice", "af_heart,Pat"):
        assert not tts.is_kokoro_voice(v), v


async def test_a_cloned_voice_is_spoken_by_alpaca_by_id_or_name(alpaca):
    import base64
    import json

    for _ in range(2):
        alpaca.get(f"{ALPACA}/api/audio/voices", payload=PROFILES)
        alpaca.post(f"{ALPACA}/api/audio/tts", payload={"audio_b64": base64.b64encode(b"RIFFclone").decode(),
                                                        "mime": "audio/wav"})
    assert await tts.synthesize("Dinner is ready", "lee-3d4e5f") == b"RIFFclone"
    assert await tts.synthesize("Dinner is ready", "pat") == b"RIFFclone"  # a name, any case
    posts = [c for (m, _u), calls in alpaca.requests.items() if m == "POST" for c in calls]
    assert [json.loads(json.dumps(c.kwargs["json"])) for c in posts] == [
        {"text": "Dinner is ready", "clone": "lee-3d4e5f"}, {"text": "Dinner is ready", "clone": "pat-0a1b2c"}]


async def test_tts_speak_with_a_cloned_voice_plays_it(alpaca, monkeypatch):
    import base64

    hub, entry = make_hub("cast", entities=[{"entity_id": "media_player.kitchen"}])
    hub.drivers[entry.entry_id] = FakePlayer(hub, entry)
    tts.register(hub)
    tts.reset()
    monkeypatch.setenv("HOMEHUB_EXTERNAL_URL", "http://hub.lan:8123")
    alpaca.get(f"{ALPACA}/api/audio/voices", payload=PROFILES)
    alpaca.post(f"{ALPACA}/api/audio/tts", payload={"audio_b64": base64.b64encode(b"RIFFpat").decode()})
    await hub.call_service("tts", "speak", {"media_player_entity_id": "media_player.kitchen", "message": "Hi",
                                            "options": {"voice": "Pat"}})
    url = hub.drivers[entry.entry_id].played[-1][2]["media_content_id"]
    assert tts.clip(url.rsplit("/", 1)[1].removesuffix(".wav")) == b"RIFFpat"


async def test_an_unknown_cloned_voice_names_the_ones_there_are(alpaca):
    alpaca.get(f"{ALPACA}/api/audio/voices", payload=PROFILES)
    with pytest.raises(HubError, match=r"'Sam' is neither a Kokoro voice nor a cloned voice .*Pat \(pat-0a1b2c\)"):
        await tts.synthesize("x", "Sam")


async def test_two_clones_with_one_name_must_be_named_by_id(alpaca):
    alpaca.get(f"{ALPACA}/api/audio/voices", payload={"voices": [{"id": "pat-1", "name": "Pat"},
                                                                {"id": "pat-2", "name": "pat"}]})
    with pytest.raises(HubError, match="more than one cloned voice is called 'Pat'"):
        await tts.synthesize("x", "Pat")


async def test_a_cloned_voice_without_alpaca_fails_fast(monkeypatch):
    monkeypatch.setattr(tts, "ALPACA_WEB_URL", "")
    with pytest.raises(HubError, match="ALPACA_WEB_URL is not set"):
        await tts.synthesize("x", "Pat")


async def test_alpacas_refusal_is_reported_with_its_words(alpaca):
    alpaca.get(f"{ALPACA}/api/audio/voices", payload=PROFILES)
    alpaca.post(f"{ALPACA}/api/audio/tts", status=503, payload={"error": "TTS model failed to load"})
    with pytest.raises(HubError, match=r"refused /api/audio/tts \(503\): TTS model failed to load"):
        await tts.synthesize("x", "Pat")


async def test_the_editor_lists_kokoro_and_cloned_voices(alpaca, monkeypatch):
    monkeypatch.setenv("EXECUTION_SVC_URL", "http://execution.example.test:8003")
    monkeypatch.setenv("INTERNAL_SECRET", "s")
    alpaca.get("http://execution.example.test:8003/execute/tts/voices", payload={"voices": ["af_heart", "am_adam"]})
    alpaca.get(f"{ALPACA}/api/audio/voices", payload=PROFILES)
    out = await tts.voices()
    assert out["errors"] == {} and out["default"] == "af_heart"
    assert out["voices"] == [{"id": "af_heart", "name": "af_heart", "kind": "kokoro"},
                             {"id": "am_adam", "name": "am_adam", "kind": "kokoro"},
                             {"id": "pat-0a1b2c", "name": "Pat", "kind": "clone"},
                             {"id": "lee-3d4e5f", "name": "Lee", "kind": "clone"}]


async def test_the_editor_is_told_why_cloned_voices_are_missing(monkeypatch):
    from aioresponses import aioresponses

    monkeypatch.setattr(tts, "ALPACA_WEB_URL", "")
    monkeypatch.setenv("EXECUTION_SVC_URL", "http://execution.example.test:8003")
    monkeypatch.setenv("INTERNAL_SECRET", "s")
    with aioresponses() as mocked:
        mocked.get("http://execution.example.test:8003/execute/tts/voices", payload={"voices": ["af_heart"]})
        out = await tts.voices()
    assert [v["id"] for v in out["voices"]] == ["af_heart"]
    assert "ALPACA_WEB_URL is not set" in out["errors"]["clone"]


def test_the_voices_route_needs_a_principal(tmp_path, monkeypatch):
    async def fake_voices():
        return {"voices": [{"id": "af_heart", "name": "af_heart", "kind": "kokoro"}], "default": "af_heart",
                "errors": {}}

    monkeypatch.setattr(tts, "voices", fake_voices)
    client = _api_client(tmp_path)
    assert client.get("/api/homehub/automations/voices").status_code == 401
    r = client.get("/api/homehub/automations/voices", headers={"Authorization": "Bearer s"})
    assert r.status_code == 200 and r.json()["voices"][0]["id"] == "af_heart"
