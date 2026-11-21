"""Teaching a voice at the satellite assigned to that person.

The end-to-end test runs the real pieces with fakes only at the edges: the
speech-to-text proxy (``wyoming_stt``) with a fake Whisper and a fake encoder,
one in-memory Redis shared by the proxy and the gateway, the gateway's teaching
turn, and Identity's real endpoints on an in-memory database. Voices are
made-up vectors and people are made-up names.
"""
import asyncio
import json
import random
from urllib.parse import urlparse

import numpy as np
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.gateway.cache as gateway_cache
import services.gateway.main as gateway_main
import services.identity.main as identity_main
from services.common import voice_enroll, voice_speaker
from services.execution import wyoming_stt
from services.gateway.schemas import ResolvedCredentials
from services.identity import voice
from services.identity.models import DeviceAssignment, User, VoicePendingEnrolment, VoiceSignature

OFFICE = "assist_satellite.office_test_assist_satellite"
SHARED = "assist_satellite.shared_test_assist_satellite"
NOBODYS = "assist_satellite.spare_test_assist_satellite"


def _voice_of(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(voice.DIMENSIONS)]


VOICES = {"robin": _voice_of(11), "casey": _voice_of(12)}


def _take(who: str, n: int) -> list[float]:
    rng = random.Random(f"{who}:{n}")
    return [x + rng.gauss(0, 0.15) for x in VOICES[who]]


class _Store:
    """One in-memory Redis, seen synchronously by the gateway."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def getdel(self, key):
        return self.values.pop(key, None)

    def delete(self, key):
        return 1 if self.values.pop(key, None) is not None else 0

    def decr(self, key):
        self.values[key] = str(int(self.values.get(key) or 0) - 1)
        return int(self.values[key])

    def scan_iter(self, match="*", count=None):
        prefix = match.rstrip("*")
        return iter([k for k in list(self.values) if k.startswith(prefix)])


class _AsyncStore:
    """The same store as the proxy sees it (redis.asyncio)."""

    def __init__(self, store: _Store):
        self.store = store

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store.set(key, value, ex)

    async def decr(self, key):
        return self.store.decr(key)

    async def delete(self, key):
        return self.store.delete(key)


class _Resp:
    def __init__(self, response):
        self.status = response.status_code
        self._body = response.json()

    async def json(self, content_type=None):
        return self._body


class _IdentityClient:
    """The gateway's HTTP client, answered by Identity's real app."""

    def __init__(self, client: TestClient):
        self.client = client
        self.calls: list[str] = []

    async def get(self, url, params=None, headers=None, timeout=None):
        path = urlparse(url).path
        self.calls.append(path)
        return _Resp(self.client.get(path, params=params))

    async def post(self, url, json=None, headers=None, timeout=None):
        path = urlparse(url).path
        self.calls.append(path)
        return _Resp(self.client.post(path, json=json))


@pytest.fixture(name="world")
def world_fixture(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="robin", display_name="Robin"))
        session.add(User(id=2, username="casey", display_name="Casey"))
        session.add(DeviceAssignment(device_id=OFFICE, user_id=1))
        session.add(DeviceAssignment(device_id=SHARED, user_id=1))
        session.add(DeviceAssignment(device_id=SHARED, user_id=2))
        session.commit()
    monkeypatch.setattr(identity_main, "engine", engine)

    def _session():
        with Session(engine) as s:
            yield s

    identity_main.app.dependency_overrides[identity_main.get_session] = _session
    identity_main.app.dependency_overrides[identity_main.require_internal] = lambda: None
    identity = _IdentityClient(TestClient(identity_main.app))
    notices: list[dict] = []

    async def _send(payload, **_kw):
        notices.append(payload)
        return True

    monkeypatch.setattr(gateway_main._notify, "send", _send)
    store = _Store()
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: store)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: identity)
    yield {"engine": engine, "store": store, "identity": identity, "notices": notices}
    identity_main.app.dependency_overrides = {}


def _samples(engine, username: str) -> list[dict]:
    with Session(engine) as session:
        row = session.get(VoiceSignature, username)
        return voice.load_samples(row.samples) if row else []


def _held(engine, username: str) -> list[dict]:
    """Satellite takes held for ``username``'s approval, across sessions."""
    with Session(engine) as session:
        rows = session.exec(select(VoicePendingEnrolment).where(VoicePendingEnrolment.username == username)).all()
        return [s for r in rows for s in voice.load_samples(r.samples)]


def _signed_in_as(username: str) -> None:
    """Identity's signed-in routes answer as ``username`` (their own app)."""
    def _require(request_session: Session = Depends(identity_main.get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    identity_main.app.dependency_overrides[identity_main.require_api_key] = _require


def _give_profile(engine, username: str, who: str) -> list[dict]:
    """``username`` enrolled from a browser, with ``who``'s voice."""
    samples = [{"embedding": _take(who, 100 + n), "speech_seconds": 4.0, "recorded_at": "2026-10-18T00:00:00+00:00"}
               for n in range(voice.MIN_SAMPLES)]
    with Session(engine) as session:
        session.add(VoiceSignature(username=username, model=voice.MODEL, samples=json.dumps(samples)))
        session.commit()
    return samples


def _event(kind: str, data: dict | None = None, payload: bytes = b"") -> bytes:
    body = json.dumps(data or {}).encode()
    header = {"type": kind, "data_length": len(body)}
    if payload:
        header["payload_length"] = len(payload)
    return json.dumps(header).encode() + b"\n" + body + payload


async def _speak(monkeypatch, store: _Store, engine, text: str, embedding: list[float] | None) -> str:
    """One command through the real proxy; returns the transcript it passed on."""

    async def whisper(reader, writer):
        while (event := await wyoming_stt.read_event(reader)) is not None:
            if event.type == "audio-stop":
                writer.write(_event("transcript", {"text": text}))
                await writer.drain()

    upstream = await asyncio.start_server(whisper, "127.0.0.1", 0)
    monkeypatch.setattr(wyoming_stt, "UPSTREAMS", [f"127.0.0.1:{upstream.sockets[0].getsockname()[1]}"])

    def fake_embed(samples, min_speech_seconds):
        if embedding is None:
            raise wyoming_stt.voiceprint.VoiceprintError("Only 0.3s of speech was heard")
        return {"embedding": embedding, "speech_seconds": 3.1, "model": voice.MODEL}

    monkeypatch.setattr(wyoming_stt.voiceprint, "embed_samples", fake_embed)

    async def identity_verdict(samples):
        # The proxy's own identify, with Identity's matching reached directly.
        try:
            signature = wyoming_stt.voiceprint.embed_samples(samples, 1.0)
        except wyoming_stt.voiceprint.VoiceprintError:
            return None
        with Session(engine) as session:
            candidates = voice.rank(signature["embedding"], identity_main._voice_profiles(session))
        verdict = voice.decide(candidates) if candidates else {"matched": False, "nobody_enrolled": True}
        verdict.update(embedding=signature["embedding"], model=signature["model"], speech_seconds=3.1)
        return verdict

    monkeypatch.setattr(wyoming_stt, "identify", identity_verdict)
    proxy = await asyncio.start_server(lambda r, w: wyoming_stt._handle(r, w, _AsyncStore(store)), "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.sockets[0].getsockname()[1])
    pcm = (np.sin(np.arange(16000) / 10) * 8000).astype("<i2").tobytes()
    for raw in (
        _event("transcribe", {"language": "en"}),
        _event("audio-start", {"rate": 16000, "width": 2, "channels": 1}),
        _event("audio-chunk", {"rate": 16000, "width": 2, "channels": 1}, pcm),
        _event("audio-stop"),
    ):
        writer.write(raw)
    await writer.drain()
    reply = await asyncio.wait_for(wyoming_stt.read_event(reader), 5)
    writer.close()
    proxy.close()
    upstream.close()
    return reply.data["text"]


async def _turn(world, monkeypatch, text, embedding, satellite=OFFICE):
    """A spoken turn: the proxy, then the gateway as the household (not recognised)."""
    transcript = await _speak(monkeypatch, world["store"], world["engine"], text, embedding)
    speaker = voice_speaker.decode(world["store"].getdel(voice_speaker.key(transcript) or ""))
    creds = ResolvedCredentials(user=speaker or "household", identified_by="voice" if speaker else None)
    return speaker, await gateway_main._voice_teaching_reply(transcript, creds, satellite)



async def _teach(world, monkeypatch, who: str, satellite=OFFICE) -> str:
    """A whole session at ``satellite`` spoken by ``who``; the last reply."""
    await _turn(world, monkeypatch, "Jarvis, learn my voice.", _take(who, 0), satellite)
    reply = ""
    for n in range(1, voice_enroll.TAKES + 1):
        _, reply = await _turn(world, monkeypatch, voice_enroll.sentence(n - 1), _take(who, n), satellite)
    return reply


@pytest.mark.asyncio
async def test_teaching_a_new_voice_is_held_until_the_owner_approves_it_end_to_end(world, monkeypatch):
    store, engine, notices = world["store"], world["engine"], world["notices"]

    speaker, reply = await _turn(world, monkeypatch, "Jarvis, learn my voice.", _take("robin", 0))
    assert speaker is None  # nobody is enrolled yet: this is the household
    assert reply.startswith("Okay, Robin.") and reply.endswith("?")
    assert voice_enroll.sentence(0) in reply
    assert store.get(voice_enroll.OPEN_KEY)

    for n in range(1, voice_enroll.TAKES):
        speaker, reply = await _turn(world, monkeypatch, voice_enroll.sentence(n - 1), _take("robin", n))
        assert f"Got it, {n} of {voice_enroll.TAKES}." in reply and reply.endswith("?")
        assert len(_held(engine, "robin")) == n
        assert _samples(engine, "robin") == []  # held apart, not in the profile
    _, reply = await _turn(world, monkeypatch, voice_enroll.sentence(3), _take("robin", 9))
    assert "approve them in your Jarvis app" in reply and "sent you a notice" in reply
    assert not reply.endswith("?")

    # Nothing is recognised from held takes, and the session is over.
    assert _samples(engine, "robin") == []
    assert not [k for k in store.values if k.startswith("voice:enroll")]
    speaker, _ = await _turn(world, monkeypatch, "Who am I?", _take("robin", 42))
    assert speaker is None

    # The owner, and only the owner, got a notice with the two answers.
    assert [n["user"] for n in notices] == ["robin"]
    (notice,) = notices
    assert notice["data"]["url"] == "/identity"
    approve, deny = notice["actions"]
    assert approve["callback"] == deny["callback"] == "voice_pending_decide"
    assert approve["params"]["approve"] is True and deny["params"]["approve"] is False

    # Robin approves from their own signed-in app.
    client = world["identity"].client
    _signed_in_as("robin")
    mine = client.get("/api/users/me/voice").json()
    assert [p["samples"] for p in mine["pending"]] == [voice_enroll.TAKES]
    resp = client.post(f"/api/users/me/voice/pending/{approve['params']['id']}")
    assert resp.status_code == 200 and resp.json()["enrolled"] and resp.json()["pending"] == []
    samples = _samples(engine, "robin")
    assert len(samples) == voice_enroll.TAKES
    assert {s["source"] for s in samples} == {"satellite"} and len({s["session"] for s in samples}) == 1
    assert _held(engine, "robin") == []

    # And now the satellite recognises Robin.
    speaker, reply = await _turn(world, monkeypatch, "Who am I?", _take("robin", 43))
    assert speaker == "robin" and reply is None


@pytest.mark.asyncio
async def test_a_stranger_cannot_enrol_into_the_owners_existing_profile(world, monkeypatch):
    """Casey at Robin's satellite: refused at the first sentence, Robin's samples untouched."""
    engine = world["engine"]
    before = _give_profile(engine, "robin", "robin")

    _, reply = await _turn(world, monkeypatch, "Jarvis, learn my voice.", _take("casey", 0))
    assert reply.startswith("Okay, Robin.")
    for n in range(1, voice_enroll.MAX_MISSES + 1):
        _, reply = await _turn(world, monkeypatch, voice_enroll.sentence(0), _take("casey", n))
        if n < voice_enroll.MAX_MISSES:
            assert "didn't sound like the voice I already know for Robin" in reply
    assert "let's stop for now" in reply

    assert _samples(engine, "robin") == before  # nothing added, nothing evicted
    assert _held(engine, "robin") == [] and _samples(engine, "casey") == []
    assert world["notices"] == []


@pytest.mark.asyncio
async def test_the_owner_with_a_profile_is_taught_directly(world, monkeypatch):
    engine = world["engine"]
    before = _give_profile(engine, "robin", "robin")
    reply = await _teach(world, monkeypatch, "robin")
    assert "I've learned your voice" in reply
    samples = _samples(engine, "robin")
    assert len(samples) == min(voice.MAX_SAMPLES, len(before) + voice_enroll.TAKES)
    assert sum(s.get("source") == "satellite" for s in samples) == voice_enroll.TAKES
    assert world["notices"] == []


@pytest.mark.asyncio
async def test_a_stranger_teaching_a_new_owner_is_only_held_and_the_owner_can_refuse(world, monkeypatch):
    """With no profile, a stranger's sentences never reach Robin's profile unless Robin says so."""
    engine, client = world["engine"], world["identity"].client
    reply = await _teach(world, monkeypatch, "casey")
    assert "approve them in your Jarvis app" in reply
    assert _samples(engine, "robin") == [] and len(_held(engine, "robin")) == voice_enroll.TAKES
    pending_id = world["notices"][0]["actions"][0]["params"]["id"]

    # Casey, signed in as herself, cannot approve takes held for Robin.
    _signed_in_as("casey")
    assert client.post(f"/api/users/me/voice/pending/{pending_id}").status_code == 404
    assert client.delete(f"/api/users/me/voice/pending/{pending_id}").status_code == 404
    assert _samples(engine, "robin") == [] and _samples(engine, "casey") == []

    # Robin says it was not them.
    _signed_in_as("robin")
    resp = client.delete(f"/api/users/me/voice/pending/{pending_id}")
    assert resp.status_code == 200 and resp.json()["pending"] == []
    assert _held(engine, "robin") == [] and _samples(engine, "robin") == []


def test_approving_never_evicts_samples_already_in_a_profile(world):
    """Robin enrolled in the browser while satellite takes waited: those fill only free places."""
    engine, client = world["engine"], world["identity"].client
    for n in range(voice_enroll.TAKES):
        body = {"username": "robin", "embedding": _take("robin", n), "model": voice.MODEL, "session": "office"}
        assert client.post("/api/voice/enroll", json=body).json()["status"] == "PENDING"
    full = [{"embedding": _take("robin", 200 + n), "speech_seconds": 4.0} for n in range(voice.MAX_SAMPLES - 1)]
    with Session(engine) as session:
        session.add(VoiceSignature(username="robin", model=voice.MODEL, samples=json.dumps(full)))
        session.commit()
    _signed_in_as("robin")
    resp = client.post("/api/users/me/voice/pending/office")
    assert resp.status_code == 200 and resp.json()["added"] == 1
    samples = _samples(engine, "robin")
    assert samples[: len(full)] == full and len(samples) == voice.MAX_SAMPLES


def test_approving_refuses_takes_unlike_a_profile_made_meanwhile(world):
    engine, client = world["engine"], world["identity"].client
    for n in range(2):
        body = {"username": "robin", "embedding": _take("casey", n), "model": voice.MODEL, "session": "office"}
        assert client.post("/api/voice/enroll", json=body).status_code == 200
    before = _give_profile(engine, "robin", "robin")
    _signed_in_as("robin")
    assert client.post("/api/users/me/voice/pending/office").status_code == 422
    assert _samples(engine, "robin") == before


@pytest.mark.asyncio
async def test_someone_else_cutting_in_is_refused_and_not_stored(world, monkeypatch):
    await _turn(world, monkeypatch, "learn my voice", _take("robin", 0))
    await _turn(world, monkeypatch, "first sentence", _take("robin", 1))
    _, reply = await _turn(world, monkeypatch, "second sentence", _take("casey", 1))
    assert "didn't sound like your earlier sentences" in reply and reply.endswith("?")
    assert len(_held(world["engine"], "robin")) == 1


@pytest.mark.asyncio
async def test_too_little_speech_asks_again_then_gives_up(world, monkeypatch):
    await _turn(world, monkeypatch, "learn my voice", _take("robin", 0))
    for _ in range(voice_enroll.MAX_MISSES - 1):
        _, reply = await _turn(world, monkeypatch, "hm", None)
        assert "didn't catch enough of your voice" in reply
    _, reply = await _turn(world, monkeypatch, "hm", None)
    assert "let's stop for now" in reply
    assert gateway_main._teaching_state(OFFICE) is None


@pytest.mark.asyncio
async def test_saying_stop_ends_the_session(world, monkeypatch):
    await _turn(world, monkeypatch, "learn my voice", _take("robin", 0))
    _, reply = await _turn(world, monkeypatch, "stop learning my voice", _take("robin", 1))
    assert "stopped learning your voice" in reply
    assert gateway_main._teaching_state(OFFICE) is None
    assert _samples(world["engine"], "robin") == []
    assert world["notices"] == []


@pytest.mark.asyncio
async def test_stopping_after_held_takes_asks_the_owner_to_approve(world, monkeypatch):
    await _turn(world, monkeypatch, "learn my voice", _take("robin", 0))
    await _turn(world, monkeypatch, "first sentence", _take("robin", 1))
    _, reply = await _turn(world, monkeypatch, "stop learning my voice", _take("robin", 2))
    assert "stopped learning your voice" in reply and "approve them" in reply
    assert [n["user"] for n in world["notices"]] == ["robin"]


@pytest.mark.asyncio
async def test_a_lost_notice_is_said_out_loud_with_where_to_approve(world, monkeypatch):
    async def _fail(payload, **_kw):
        return False

    monkeypatch.setattr(gateway_main._notify, "send", _fail)
    reply = await _teach(world, monkeypatch, "robin")
    assert "couldn't send you a notice" in reply and "Vocal Signature" in reply
    assert len(_held(world["engine"], "robin")) == voice_enroll.TAKES


@pytest.mark.asyncio
async def test_an_unassigned_satellite_says_how_to_assign_it(world, monkeypatch):
    _, reply = await _turn(world, monkeypatch, "learn my voice", _take("robin", 0), satellite=NOBODYS)
    assert "isn't assigned to anyone" in reply and "Users and Devices" in reply
    assert gateway_main._teaching_state(NOBODYS) is None


@pytest.mark.asyncio
async def test_a_shared_satellite_does_not_guess_whose_voice(world, monkeypatch):
    _, reply = await _turn(world, monkeypatch, "learn my voice", _take("robin", 0), satellite=SHARED)
    assert "shared by 2 people" in reply
    assert gateway_main._teaching_state(SHARED) is None


@pytest.mark.asyncio
async def test_a_recognised_voice_of_someone_else_keeps_the_old_path(world, monkeypatch):
    creds = ResolvedCredentials(user="casey", identified_by="voice")
    assert await gateway_main._voice_teaching_reply("learn my voice", creds, OFFICE) is None
    assert gateway_main._teaching_state(OFFICE) is None


@pytest.mark.asyncio
async def test_ordinary_requests_are_untouched_without_a_session(world):
    creds = ResolvedCredentials(user="household")
    assert await gateway_main._voice_teaching_reply("turn on the lights", creds, OFFICE) is None
    assert world["identity"].calls == []


def test_identity_reports_the_one_owner(world):
    client = world["identity"].client
    assert client.get("/api/internal/satellite-owner", params={"entity_id": OFFICE}).json() == {
        "username": "robin", "name": "Robin", "owners": 1,
    }
    assert client.get("/api/internal/satellite-owner", params={"entity_id": SHARED}).json()["owners"] == 2
    assert client.get("/api/internal/satellite-owner", params={"entity_id": NOBODYS}).json() == {
        "username": None, "name": None, "owners": 0,
    }


def test_a_satellite_take_needs_a_known_person_and_a_session(world):
    client = world["identity"].client
    take = {"embedding": _take("robin", 0), "model": voice.MODEL, "session": "s1"}
    assert client.post("/api/voice/enroll", json={**take, "username": "nobody"}).status_code == 404
    assert client.post("/api/voice/enroll", json={**take, "username": "robin", "session": " "}).status_code == 422
    assert client.post("/api/voice/enroll", json={**take, "username": "robin", "model": "x"}).status_code == 422
    with Session(world["engine"]) as session:
        assert session.exec(select(VoiceSignature)).all() == []
        assert session.exec(select(VoicePendingEnrolment)).all() == []


def test_a_session_held_for_one_person_cannot_take_another(world):
    client = world["identity"].client
    body = {"embedding": _take("robin", 0), "model": voice.MODEL, "session": "s1"}
    assert client.post("/api/voice/enroll", json={**body, "username": "robin"}).status_code == 200
    assert client.post("/api/voice/enroll", json={**body, "username": "casey"}).status_code == 409


def test_the_push_button_answers_as_the_signed_in_person(world, monkeypatch):
    """voice_pending_decide calls identity with the caller's own key, never as the service."""
    from services.gateway import push_callbacks

    seen = []

    async def _as_caller(request, method, path, **kwargs):
        seen.append((method, path))
        return 200, {"status": "SUCCESS"}

    monkeypatch.setattr(gateway_main, "_identity_as_caller", _as_caller)
    handler = push_callbacks.PUSH_CALLBACKS["voice_pending_decide"]
    asyncio.run(handler(None, {"id": "s1", "approve": True}, None))
    asyncio.run(handler(None, {"id": "s1", "approve": False}, None))
    assert seen == [("POST", "/api/users/me/voice/pending/s1"), ("DELETE", "/api/users/me/voice/pending/s1")]
