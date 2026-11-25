"""Who a spoken request is from: the identified speaker, else the person the
satellite is assigned to, else the default user.

The gateway's half (``apply_voice_identity``, ``voice_decision``,
``_offer_voice_candidate``) runs against Identity's real app on an in-memory
database. People, satellites and voices are made up; signatures are fake vectors.
"""
import json
import math
import random
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine

import services.gateway.cache as gateway_cache
import services.gateway.main as gateway_main
import services.identity.main as identity_main
from services.common import voice_enroll, voice_speaker
from services.gateway import voice_outcomes
from services.identity import hub_devices, voice
from services.identity.models import User, VoiceCandidate, VoiceSignature

ASSIGNED = "assist_satellite.test_kids_room"
SHARED = "assist_satellite.test_kitchen"


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _voice(seed: int) -> list[float]:
    rng = random.Random(seed)
    return _unit([rng.gauss(0, 1) for _ in range(voice.DIMENSIONS)])


def _take(base, n, wobble=0.4):
    rng = random.Random(n)
    return [x + rng.gauss(0, wobble / math.sqrt(voice.DIMENSIONS)) for x in base]


@pytest.fixture(name="identity")
def identity_fixture(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    hub_devices.clear_cache()
    identity_main._calibration_cache.clear()
    with Session(engine) as session:
        session.add(User(username="default", is_admin=True, is_system_default=True))
        session.add(User(username="parent", display_name="Parent", is_admin=True, family_role="father"))
        session.add(User(username="kid", display_name="Kid", family_role="child"))
        session.commit()
        session.add(VoiceSignature(username="kid", model=voice.MODEL,
                                   samples=json.dumps([{"embedding": _take(_voice(2), n)} for n in range(4)])))
        hub_devices.sync(session, [
            {"hardware_id": "mac:02:00:00:00:00:a1", "kind": "voice_satellite", "name": "Kids", "entity_ids": [ASSIGNED]},
            {"hardware_id": "mac:02:00:00:00:00:a2", "kind": "voice_satellite", "name": "Kitchen", "entity_ids": [SHARED]},
        ])
        hub_devices.assign(session, "hub:mac:02:00:00:00:00:a1", {"owner_username": "kid"})
    monkeypatch.setattr(identity_main, "engine", engine)

    def _session():
        with Session(engine) as s:
            yield s

    identity_main.app.dependency_overrides[identity_main.get_session] = _session
    identity_main.app.dependency_overrides[identity_main.require_internal] = lambda: None
    yield {"client": TestClient(identity_main.app), "engine": engine}
    identity_main.app.dependency_overrides = {}
    hub_devices.clear_cache()


def _who(identity, record, satellite, extra=None):
    """The gateway's identity body for a spoken request, resolved by Identity."""
    body = {"query": "what's on today", **(extra or {})}
    speaker = gateway_main.apply_voice_identity(body, record, {"satellite": satellite} if satellite else None)
    if not any(body.get(k) for k in ("api_key", "voice_id", "device_id")):
        body["household"] = True
    resolved = identity["client"].post("/api/resolve", json=body)
    assert resolved.status_code == 200, resolved.text
    creds = resolved.json()
    identified_by = "voice" if speaker and creds["user"] == speaker else creds.get("identified_by")
    return creds, identified_by


def _record(username, top=(("kid", 0.91), ("parent", 0.41))):
    return {"username": username, "top": [{"username": u, "score": s} for u, s in top],
            "threshold": 0.8, "margin": 0.5, "speech_seconds": 2.1}


def test_an_identified_speaker_is_that_person_even_at_someone_elses_satellite(identity):
    creds, how = _who(identity, _record("parent", (("parent", 0.9), ("kid", 0.4))), ASSIGNED)
    assert creds["user"] == "parent" and how == "voice"


def test_not_identified_at_an_assigned_satellite_is_its_person(identity):
    creds, how = _who(identity, _record(None, (("kid", 0.62), ("parent", 0.4))), ASSIGNED)
    assert creds["user"] == "kid" and how == "device" and creds["is_admin"] is False


@pytest.mark.parametrize("satellite", [SHARED, "assist_satellite.unknown_test", None])
def test_not_identified_and_shared_or_unassigned_is_the_default(identity, satellite):
    creds, how = _who(identity, _record(None), satellite)
    assert creds["user"] not in ("kid", "parent") and how is None and creds["is_admin"] is False


def test_a_client_cannot_name_a_satellite_itself(identity):
    body = {"voice_device": ASSIGNED}
    gateway_main.apply_voice_identity(body, None, None)
    assert "voice_device" not in body


def test_an_api_key_outranks_the_satellite():
    body = {"api_key": "k"}
    assert gateway_main.apply_voice_identity(body, _record("kid"), {"satellite": ASSIGNED}) is None
    assert "voice_id" not in body and "voice_device" not in body


@pytest.mark.parametrize(("identified_by", "decision"), [
    ("voice", "identified"), ("device", "device-assigned"), (None, "default"),
])
def test_one_log_line_per_request_with_the_decision_and_top_two(identified_by, decision):
    got, line = gateway_main.voice_decision(identified_by, "kid", ASSIGNED, _record(None))
    assert got == decision
    assert f"decision={decision}" in line and "top=kid 0.91" in line and "second=parent 0.41" in line
    assert "speech=2.1s" in line and "embedding" not in line


def test_the_decision_line_without_any_scores():
    _, line = gateway_main.voice_decision(None, "household", SHARED, None)
    assert "top=- second=-" in line and "speech=-" in line


def test_the_decision_is_counted_in_the_persons_request_outcomes():
    record = voice_outcomes.begin("voice")
    voice_outcomes.set_user("kid")
    voice_outcomes.set_identity("device-assigned")
    voice_outcomes.set_identity("bogus")
    assert record.identity == "device-assigned"
    voice_outcomes.finish(record)
    with voice_outcomes._lock:
        keys = [k for k in voice_outcomes._pending if k[0] == "kid"]
        voice_outcomes._pending.clear()
    assert any(k[2] == "i:device-assigned" for k in keys)


class _Store:
    def __init__(self, values):
        self.values = values

    def getdel(self, key):
        return self.values.pop(key, None)


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


@pytest.mark.asyncio
async def test_a_close_unrecognised_command_is_offered_as_a_candidate_once(identity, monkeypatch):
    text = "what's on today"
    near = _unit([0.6 * x + 0.4 * y for x, y in zip(_voice(2), _voice(99), strict=True)])
    store = _Store({voice_speaker.candidate_key(text): voice_enroll.encode_take(
        {"embedding": near, "speech_seconds": 2.4, "model": voice.MODEL})})
    monkeypatch.setattr(gateway_cache, "get_redis", lambda: store)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: _Http(identity["client"]))
    await gateway_main._offer_voice_candidate(text, ASSIGNED)
    await gateway_main._offer_voice_candidate(text, ASSIGNED)  # the signature was used up
    with Session(identity["engine"]) as session:
        rows = session.query(VoiceCandidate).all()
        assert [r.username for r in rows] == ["kid"]
        # The profile itself is untouched until an admin approves.
        assert len(voice.load_samples(session.get(VoiceSignature, "kid").samples)) == 4


@pytest.mark.asyncio
async def test_resolve_drops_a_satellite_a_client_named_itself(monkeypatch):
    sent = []

    async def cached(payload, fetch):
        sent.append(payload)
        return {"user": "household"}

    monkeypatch.setattr(gateway_cache, "get_cached_identity", cached)
    await gateway_main.resolve_identity({"household": True, "voice_device": ASSIGNED})
    await gateway_main.resolve_identity({"household": True, "voice_device": ASSIGNED}, voice_device_checked=True)
    assert "voice_device" not in sent[0] and sent[1]["voice_device"] == ASSIGNED
