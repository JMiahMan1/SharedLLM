"""A voice nobody enrolled: the gateway's invitation line, once a day, never for an unsure match.

Runs the gateway's half (``_new_voice_check``, the response fields) against Identity's real app on an
in-memory database, with fakeredis for the anonymous clusters. Signatures are fake vectors; people
and satellites are made up.
"""
import json
import math
import random
from urllib.parse import urlparse

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.gateway.cache as gateway_cache
import services.gateway.main as gateway_main
import services.identity.main as identity_main
from services.common import voice_enroll, voice_speaker
from services.identity import hub_devices, voice
from services.identity.models import User, VoiceCandidate, VoicePendingEnrolment, VoiceSignature

SHARED = "assist_satellite.test_office"
NEW_KIDS = "assist_satellite.test_playroom"
QUERY = "what time is it"


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _voice(seed: int) -> list[float]:
    rng = random.Random(seed)
    return _unit([rng.gauss(0, 1) for _ in range(voice.DIMENSIONS)])


def _mix(a, b, share):
    return _unit([(1 - share) * x + share * y for x, y in zip(a, b, strict=True)])


def _take(base, n, wobble=0.4):
    rng = random.Random(n)
    return [x + rng.gauss(0, wobble / math.sqrt(voice.DIMENSIONS)) for x in base]


@pytest.fixture(name="world")
def world_fixture(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    hub_devices.clear_cache()
    identity_main._calibration_cache.clear()
    a, b = _voice(1), _mix(_voice(1), _voice(2), 0.3)  # two family members who sound alike
    with Session(engine) as session:
        session.add(User(username="default", is_admin=True, is_system_default=True))
        for name in ("parent", "sibling", "newkid"):
            session.add(User(username=name, display_name=name.title()))
        session.commit()
        for name, base in (("parent", a), ("sibling", b)):
            session.add(VoiceSignature(username=name, model=voice.MODEL,
                                       samples=json.dumps([{"embedding": _take(base, n)} for n in range(4)])))
        hub_devices.sync(session, [
            {"hardware_id": "mac:02:00:00:00:00:41", "kind": "voice_satellite", "name": "Office", "entity_ids": [SHARED]},
            {"hardware_id": "mac:02:00:00:00:00:42", "kind": "voice_satellite", "name": "Playroom",
             "entity_ids": [NEW_KIDS]},
        ])
        hub_devices.assign(session, "hub:mac:02:00:00:00:00:42", {"owner_username": "newkid"})
        session.commit()
    monkeypatch.setattr(identity_main, "engine", engine)
    clusters = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(identity_main, "_voice_redis", lambda: clusters)

    def _session():
        with Session(engine) as s:
            yield s

    identity_main.app.dependency_overrides[identity_main.get_session] = _session
    identity_main.app.dependency_overrides[identity_main.require_internal] = lambda: None
    client = TestClient(identity_main.app)
    gateway_redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: gateway_redis)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _Http(client))
    yield {"client": client, "engine": engine, "redis": gateway_redis, "a": a, "b": b}
    identity_main.app.dependency_overrides = {}
    hub_devices.clear_cache()


class _Resp:
    def __init__(self, response):
        self.status = response.status_code
        self._body = response.json()

    async def json(self, content_type=None):
        return self._body


class _Http:
    def __init__(self, client):
        self.client = client

    async def post(self, url, json=None, headers=None, timeout=None):
        return _Resp(self.client.post(urlparse(url).path, json=json))


def _verdict(world, embedding, speech=2.6, snr=24.0):
    """What execution's proxy would hold for this command: identity's verdict plus the signature."""
    verdict = world["client"].post("/api/voice/identify", json={"embedding": embedding}).json()
    return {**verdict, "embedding": embedding, "model": voice.MODEL, "speech_seconds": speech, "snr_db": snr}


async def _command(world, embedding, satellite=SHARED, query=QUERY, whispered=False):
    """One spoken command: execution keeps the take when it looks new, the gateway checks it."""
    verdict = _verdict(world, embedding)
    if voice_speaker.looks_new(verdict):
        world["redis"].set(voice_speaker.new_voice_key(query), voice_enroll.encode_take(verdict), ex=20)
    ctx = {"satellite": satellite, **({"whispered": True} if whispered else {})}
    return await gateway_main._new_voice_check(query, ctx)


@pytest.mark.asyncio
async def test_a_new_voice_is_invited_once_a_day_per_voice(world):
    stranger = _voice(77)
    first = await _command(world, _take(stranger, 1))
    assert first == {"note": gateway_main.NEW_VOICE_LINE_ASK_GROWN_UP}
    again = await _command(world, _take(stranger, 2))
    assert again == {"note": None}  # recognised as the same new voice: counted, not asked again
    someone_else = await _command(world, _voice(78))
    assert someone_else == {"note": gateway_main.NEW_VOICE_LINE_ASK_GROWN_UP}


@pytest.mark.asyncio
async def test_learn_my_voice_is_offered_where_it_would_work(world):
    body = await _command(world, _voice(79), satellite=NEW_KIDS)
    assert body == {"note": gateway_main.NEW_VOICE_LINE_LEARN_HERE}
    assert "learn my voice" in gateway_main.NEW_VOICE_LINE_LEARN_HERE


@pytest.mark.asyncio
async def test_an_unsure_match_between_two_enrolled_people_is_not_a_new_voice(world):
    between = _mix(world["a"], world["b"], 0.5)
    verdict = _verdict(world, between)
    assert verdict["matched"] is False and verdict["score"] >= 0.8  # unsure between two known people
    assert not voice_speaker.looks_new(verdict)
    assert await _command(world, between) is None


@pytest.mark.asyncio
async def test_a_known_voice_that_was_merely_not_recognised_is_not_new(world):
    far_parent = _mix(world["a"], _voice(90), 0.45)
    assert await _command(world, far_parent) is None


@pytest.mark.asyncio
async def test_a_whisper_or_someone_asking_to_be_learned_is_not_invited(world):
    assert await _command(world, _voice(81), whispered=True) == {"note": None}
    assert await _command(world, _voice(82), query="please learn my voice") == {"note": None}


@pytest.mark.asyncio
async def test_no_profile_is_ever_made_from_new_voices(world):
    with Session(world["engine"]) as session:
        before = {r.username: r.samples for r in session.exec(select(VoiceSignature)).all()}
    for n in range(8):
        await _command(world, _take(_voice(77), n))
        await _command(world, _take(_voice(83), n), satellite=NEW_KIDS)
    with Session(world["engine"]) as session:
        assert {r.username: r.samples for r in session.exec(select(VoiceSignature)).all()} == before
        assert session.exec(select(VoiceCandidate)).all() == []
        assert session.exec(select(VoicePendingEnrolment)).all() == []


@pytest.mark.asyncio
async def test_identity_down_means_no_line_and_no_error(world, monkeypatch):
    class Down:
        async def post(self, *a, **k):
            raise ConnectionError("identity is down")

    monkeypatch.setattr(gateway_main, "get_http_client", lambda: Down())
    verdict = _verdict(world, _voice(77))
    world["redis"].set(voice_speaker.new_voice_key(QUERY), voice_enroll.encode_take(verdict), ex=20)
    assert await gateway_main._new_voice_check(QUERY, {"satellite": SHARED}) is None
    assert await gateway_main._new_voice_check(QUERY, {"satellite": SHARED}) is None  # nothing was kept


def test_the_answer_carries_the_line_for_the_satellite():
    token = gateway_main._VOICE_NEW.set({"note": "By the way."})
    try:
        body = json.loads(gateway_main._make_ollama_response("It is nine.", "m").body)
        assert body["new_voice"] is True and body["voice_note"] == "By the way."
        assert body["message"]["content"] == "It is nine."  # the answer itself is untouched
        openai = json.loads(gateway_main._make_openai_response("It is nine.", "m").body)
        assert openai["voice_note"] == "By the way."
    finally:
        gateway_main._VOICE_NEW.reset(token)
    token = gateway_main._VOICE_NEW.set({"note": None})
    try:
        assert gateway_main._voice_new_fields() == {"new_voice": True}
    finally:
        gateway_main._VOICE_NEW.reset(token)
    assert "voice_note" not in json.loads(gateway_main._make_ollama_response("Hi.", "m").body)
