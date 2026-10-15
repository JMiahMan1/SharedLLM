"""Voice enrollment and identification against locally computed signatures.

The signature itself comes from the execution service's model; here it is
replaced by fixed vectors per person (plus per-take variation), so these
tests cover what identity owns: keeping samples, refusing a take that is not
the same person, and deciding who is speaking.
"""
import random

import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import voice
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import User, VoiceSignature


def _voice_of(seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(0, 1) for _ in range(voice.DIMENSIONS)]


VOICES = {"jeremiah": _voice_of(1), "michele": _voice_of(2), "leo": _voice_of(3)}


def _take(who: str, n: int, wobble: float = 0.15) -> list[float]:
    """Another recording of ``who``: their voice plus a little variation."""
    rng = random.Random(f"{who}:{n}")
    return [x + rng.gauss(0, wobble) for x in VOICES[who]]


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(id=1, username="jeremiah", display_name="Jeremiah", is_admin=True))
        session.add(User(id=2, username="michele", display_name="Michele"))
        session.add(User(id=3, username="leo", display_name="Leo"))
        session.commit()
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session, monkeypatch):
    identity_main.engine = session.bind
    spoken: list[list[float]] = []

    async def _signature(_file):
        if not spoken:
            raise HTTPException(status_code=422, detail="Only 0.4s of speech was heard")
        return {"embedding": spoken.pop(0), "speech_seconds": 4.2, "model": voice.MODEL}

    monkeypatch.setattr(identity_main, "_voice_signature_of", _signature)
    app.dependency_overrides[require_internal] = lambda: None
    client = TestClient(app)
    client.spoken = spoken
    client.act_as = lambda username: _act_as(username)
    _act_as("jeremiah")
    yield client
    app.dependency_overrides = {}


def _act_as(username: str) -> None:
    def _require(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == username)).first()

    app.dependency_overrides[require_api_key] = _require


def _say(client, embedding, path="/api/users/me/enroll"):
    client.spoken.append(embedding)
    return client.post(path, files={"file": ("take.webm", b"audio", "audio/webm")})


def _enroll(client, who: str, takes: int = voice.MIN_SAMPLES):
    client.act_as(who)
    for n in range(takes):
        assert _say(client, _take(who, n)).status_code == 200


def test_enrollment_needs_several_takes_before_it_counts(client, session):
    assert client.get("/api/users/me/voice").json()["enrolled"] is False
    for n in range(voice.MIN_SAMPLES):
        body = _say(client, _take("jeremiah", n)).json()
        assert body["samples"] == n + 1
        assert body["enrolled"] is (n + 1 >= voice.MIN_SAMPLES)
    user = session.exec(select(User).where(User.username == "jeremiah")).one()
    session.refresh(user)
    assert user.voice_fingerprint and user.voice_fingerprint.startswith("ge2e:")
    status = client.get("/api/users/me/voice").json()
    assert status["enrolled"] and status["consistency"] > 0.9


def test_a_take_from_someone_else_is_refused(client):
    _say(client, _take("jeremiah", 0))
    refused = _say(client, _take("michele", 0))
    assert refused.status_code == 422 and "does not sound like your earlier" in refused.json()["detail"]
    assert client.get("/api/users/me/voice").json()["samples"] == 1


def test_too_little_speech_is_reported_not_stored(client):
    bad = client.post("/api/users/me/enroll", files={"file": ("take.webm", b"audio", "audio/webm")})
    assert bad.status_code == 422 and "speech" in bad.json()["detail"]
    assert client.get("/api/users/me/voice").json()["samples"] == 0


def test_only_the_newest_takes_are_kept(client):
    for n in range(voice.MAX_SAMPLES + 2):
        _say(client, _take("jeremiah", n))
    assert client.get("/api/users/me/voice").json()["samples"] == voice.MAX_SAMPLES


def test_identify_names_the_right_person(client):
    for who in VOICES:
        _enroll(client, who)
    for who in VOICES:
        body = client.post("/api/voice/identify", json={"embedding": _take(who, 99)}).json()
        assert body["matched"] and body["matched_username"] == who
        assert body["candidates"][0]["name"] == who.capitalize()


def test_a_stranger_is_not_matched_but_the_ranking_is_returned(client):
    for who in VOICES:
        _enroll(client, who)
    body = client.post("/api/voice/identify", json={"embedding": _voice_of(42)}).json()
    assert body["matched"] is False and body["matched_username"] is None
    assert len(body["candidates"]) == 3


def test_two_close_voices_are_not_guessed_between(client):
    """Above the threshold for both but with no clear lead: no match."""
    _enroll(client, "jeremiah")
    VOICES["leo"] = [x + random.Random(7).gauss(0, 0.05) for x in VOICES["jeremiah"]]
    try:
        _enroll(client, "leo")
        body = client.post("/api/voice/identify", json={"embedding": _take("jeremiah", 50)}).json()
        assert body["score"] >= voice.MATCH_THRESHOLD and body["margin"] < voice.MATCH_MARGIN
        assert body["matched"] is False
    finally:
        VOICES["leo"] = _voice_of(3)


def test_identify_before_anyone_enrolled_is_404(client):
    _say(client, _take("jeremiah", 0))  # one take is not a profile yet
    assert client.post("/api/voice/identify", json={"embedding": _take("jeremiah", 1)}).status_code == 404


def test_identify_refuses_a_malformed_signature(client):
    _enroll(client, "jeremiah")
    assert client.post("/api/voice/identify", json={"embedding": [1.0, 2.0]}).status_code == 422
    other_model = {"embedding": _take("jeremiah", 1), "model": "openvoice"}
    assert client.post("/api/voice/identify", json=other_model).status_code == 422


def test_checking_my_voice_scores_against_everyone(client):
    _enroll(client, "michele")
    _enroll(client, "jeremiah")
    me = _say(client, _take("jeremiah", 77), "/api/users/me/voice/check").json()
    assert me["is_me"] is True
    not_me = _say(client, _take("michele", 77), "/api/users/me/voice/check").json()
    assert not_me["is_me"] is False and not_me["matched_username"] == "michele"


def test_checking_before_enrolling_says_what_to_do(client):
    resp = _say(client, _take("jeremiah", 0), "/api/users/me/voice/check")
    assert resp.status_code == 409 and "samples" in resp.json()["detail"]


def test_forgetting_erases_the_profile(client, session):
    _enroll(client, "jeremiah")
    assert client.delete("/api/users/me/voice").json()["samples"] == 0
    assert session.get(VoiceSignature, "jeremiah") is None
    user = session.exec(select(User).where(User.username == "jeremiah")).one()
    session.refresh(user)
    assert user.voice_fingerprint is None


def test_profiles_lists_who_can_be_recognised_without_signatures(client):
    _enroll(client, "michele")
    _enroll(client, "jeremiah", takes=1)
    body = client.get("/api/voice/profiles").json()
    assert body == {"profiles": [{"username": "michele", "name": "Michele", "samples": voice.MIN_SAMPLES}]}


def test_the_fingerprint_cannot_be_set_by_hand(client, session):
    client.patch("/api/users/me", json={"voice_fingerprint": "v1:forged"})
    user = session.exec(select(User).where(User.username == "jeremiah")).one()
    session.refresh(user)
    assert user.voice_fingerprint is None
