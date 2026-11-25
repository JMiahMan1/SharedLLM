"""Voices nobody enrolled: the gate, the anonymous clusters, once a day, the admin counts, never a profile.

Signatures are made-up vectors (a random direction per person plus per-take noise); no real voice or
embedding is used. Redis is fakeredis.
"""
import json
import math
import random

import fakeredis
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import hub_devices, new_voices, voice
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import User, VoiceCandidate, VoicePendingEnrolment, VoiceSignature

SATELLITE = "assist_satellite.test_office"
OTHER_SATELLITE = "assist_satellite.test_playroom"


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _direction(seed: int) -> list[float]:
    rng = random.Random(seed)
    return _unit([rng.gauss(0, 1) for _ in range(voice.DIMENSIONS)])


def _mix(a, b, share):
    return _unit([(1 - share) * x + share * y for x, y in zip(a, b, strict=True)])


def _take(base, n, wobble=0.4):
    rng = random.Random(n * 7919 + int(base[0] * 1e6))
    return [x + rng.gauss(0, wobble / math.sqrt(voice.DIMENSIONS)) for x in base]


# --- the gate -----------------------------------------------------------------------------
def _profiles():
    return [{"username": u, "name": u, "role": r, "samples": [{"embedding": _take(_direction(s), n)} for n in range(4)]}
            for u, s, r in (("parent", 11, "mother"), ("kid", 12, "child"))]


def test_a_voice_far_from_everyone_is_new():
    ok, why, best = voice.new_voice_gate(_take(_direction(77), 1), _profiles(), 0.8, 2.5)
    assert ok and why == "" and best < voice.NEW_VOICE_MAX_SCORE


@pytest.mark.parametrize(("embedding", "speech", "why"), [
    (_take(_direction(12), 9), 2.5, "close to someone enrolled"),  # the kid, not recognised this time
    (_mix(_direction(12), _direction(77), 0.45), 2.5, "close to someone enrolled"),  # the kid far from the mic
    (_take(_direction(77), 1), 1.2, "too little speech"),
])
def test_not_new_when_close_to_someone_or_too_short(embedding, speech, why):
    ok, reason, _best = voice.new_voice_gate(embedding, _profiles(), 0.8, speech)
    assert not ok and reason == why


def test_unsure_between_two_enrolled_people_is_not_a_new_voice():
    a = _direction(21)
    b = _mix(a, _direction(22), 0.3)  # two people who sound alike
    profiles = [{"username": u, "name": u, "samples": [{"embedding": _take(v, n)} for n in range(4)]}
                for u, v in (("a", a), ("b", b))]
    between = _mix(a, b, 0.5)
    verdict = voice.decide(voice.rank(between, profiles), 0.8, 0.04)
    assert verdict["matched"] is False and verdict["score"] > 0.8  # unsure, not unknown
    ok, why, _ = voice.new_voice_gate(between, profiles, 0.8, 3.0)
    assert not ok and why == "close to someone enrolled"


def test_nobody_enrolled_is_never_a_new_voice():
    assert voice.new_voice_gate(_direction(1), [], 0.8, 3.0)[:2] == (False, "nobody enrolled")


# --- the clusters ---------------------------------------------------------------------------
@pytest.fixture(name="redis")
def redis_fixture():
    return fakeredis.FakeRedis(decode_responses=True)


def test_the_same_new_voice_is_invited_once_a_day(redis):
    stranger = _direction(77)
    first = new_voices.heard(redis, "dev:office", "Office Echo", _take(stranger, 1), now=1000.0)
    again = new_voices.heard(redis, "dev:office", "Office Echo", _take(stranger, 2), now=2000.0)
    assert first["ask"] and first["new"] and not again["ask"] and not again["new"]
    assert again["cluster"] == first["cluster"] and again["visits_week"] == 2
    tomorrow = new_voices.heard(redis, "dev:office", "Office Echo", _take(stranger, 3), now=1000.0 + 86400)
    assert tomorrow["ask"] and tomorrow["cluster"] == first["cluster"]


def test_a_different_new_voice_and_a_different_satellite_are_asked_on_their_own(redis):
    one = new_voices.heard(redis, "dev:office", None, _direction(77), now=0.0)
    other = new_voices.heard(redis, "dev:office", None, _direction(78), now=10.0)
    elsewhere = new_voices.heard(redis, "dev:playroom", None, _direction(77), now=20.0)
    assert one["ask"] and other["ask"] and elsewhere["ask"]
    assert other["cluster"] != one["cluster"]


def test_clusters_expire_and_carry_no_name(redis):
    new_voices.heard(redis, "dev:office", "Office Echo", _direction(77), now=0.0)
    assert 0 < redis.ttl(new_voices.KEY_PREFIX + "dev:office") <= new_voices.TTL_S
    doc = json.loads(redis.get(new_voices.KEY_PREFIX + "dev:office"))
    assert set(doc[0]) == {"id", "centroid", "n", "visits", "asked_at", "first_heard", "label"}
    # A week later the old visits no longer count, and the voice is new again.
    later = new_voices.heard(redis, "dev:office", "Office Echo", _direction(77), now=new_voices.WEEK_S + 10)
    assert later["new"] and later["visits_week"] == 1


def test_the_overview_is_counts_only_and_needs_repeat_visits(redis):
    for i in range(new_voices.SUGGEST_VISITS - 1):
        new_voices.heard(redis, "dev:office", "Office Echo", _take(_direction(77), i), now=100.0 + i)
    assert new_voices.overview(redis, now=200.0) == []
    new_voices.heard(redis, "dev:office", "Office Echo", _take(_direction(77), 9), now=150.0)
    [row] = new_voices.overview(redis, now=200.0)
    assert row["visits_week"] == new_voices.SUGGEST_VISITS and row["label"] == "Office Echo"
    assert set(row) == {"id", "device", "label", "visits_week", "last_heard"}
    assert new_voices.forget(redis, "dev:office", row["id"]) and new_voices.overview(redis, now=200.0) == []
    assert not new_voices.forget(redis, "dev:office", row["id"])


# --- through the service --------------------------------------------------------------------
@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    hub_devices.clear_cache()
    identity_main._calibration_cache.clear()
    with Session(engine) as session:
        session.add(User(username="default", is_admin=True, is_system_default=True))
        session.add(User(username="parent", display_name="Parent", is_admin=True, family_role="mother"))
        session.add(User(username="kid", display_name="Kid", family_role="child"))
        session.add(User(username="newkid", display_name="New Kid", family_role="child"))
        session.commit()
        for p in _profiles():
            session.add(VoiceSignature(username=p["username"], model=voice.MODEL, samples=json.dumps(p["samples"])))
        session.commit()
        hub_devices.sync(session, [
            {"hardware_id": "mac:02:00:00:00:00:31", "kind": "voice_satellite", "name": "Office Echo",
             "entity_ids": [SATELLITE]},
            {"hardware_id": "mac:02:00:00:00:00:32", "kind": "voice_satellite", "name": "Playroom",
             "entity_ids": [OTHER_SATELLITE]},
        ])
        hub_devices.assign(session, "hub:mac:02:00:00:00:00:32", {"owner_username": "newkid"})
        yield session
    hub_devices.clear_cache()
    identity_main._calibration_cache.clear()


@pytest.fixture(name="client")
def client_fixture(session: Session, redis, monkeypatch):
    identity_main.engine = session.bind
    monkeypatch.setattr(identity_main, "_voice_redis", lambda: redis)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_internal] = lambda: True

    def _admin(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == "parent")).first()

    app.dependency_overrides[require_api_key] = _admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def _heard(client, embedding, satellite=SATELLITE, speech=2.6):
    return client.post("/api/voice/new_voice", json={"satellite": satellite, "embedding": embedding,
                                                      "speech_seconds": speech}).json()


def test_a_new_voice_is_counted_and_invited_once_without_any_profile(client, session):
    before = {r.username: r.samples for r in session.exec(select(VoiceSignature)).all()}
    first = _heard(client, _take(_direction(77), 1))
    assert first["status"] == "NEW_VOICE" and first["ask"] is True and first["learn_here"] is False
    for n in range(2, 6):
        assert _heard(client, _take(_direction(77), n))["ask"] is False
    # Nothing was made of it: no profile, no candidate, no held enrolment, no signature changed.
    assert {r.username: r.samples for r in session.exec(select(VoiceSignature)).all()} == before
    assert session.exec(select(VoiceCandidate)).all() == []
    assert session.exec(select(VoicePendingEnrolment)).all() == []
    overview = client.get("/api/admin/voice").json()
    [row] = overview["new_voices"]
    assert row["visits_week"] == 5 and row["label"] == "Office Echo"
    assert "centroid" not in json.dumps(overview) and "embedding" not in json.dumps(overview)
    assert client.delete(f"/api/admin/voice/new_voices/{row['id']}", params={"device": row["device"]}).status_code == 200
    assert client.get("/api/admin/voice").json()["new_voices"] == []


def test_learn_here_only_at_a_satellite_whose_person_has_no_voice_yet(client):
    assert _heard(client, _direction(80), satellite=OTHER_SATELLITE)["learn_here"] is True
    assert _heard(client, _direction(81), satellite=SATELLITE)["learn_here"] is False  # shared


def test_identity_checks_the_voice_itself(client):
    kid_far = _mix(_direction(12), _direction(77), 0.4)
    body = _heard(client, kid_far)
    assert body["status"] == "SKIPPED" and body["ask"] is False and body["new_voice"] is False
    assert _heard(client, _direction(77), speech=1.0)["reason"] == "too little speech"
    bad = client.post("/api/voice/new_voice", json={"satellite": SATELLITE, "embedding": [1.0]})
    assert bad.status_code == 422


def test_no_redis_means_no_invitation_not_an_error(client, monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(identity_main, "_voice_redis", broken)
    body = _heard(client, _direction(77))
    assert body["ask"] is False and body["status"] == "SKIPPED"
    assert client.get("/api/admin/voice").json()["new_voices"] == []
