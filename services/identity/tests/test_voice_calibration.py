"""Calibrating the voice threshold on the household's own enrolled voices, the
child/adult guard, and candidate samples from assigned satellites.

Signatures are made-up vectors (a direction per person plus per-take noise):
no real voice or embedding is used anywhere here.
"""
import json
import math
import random

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, StaticPool, create_engine, select

import services.identity.main as identity_main
from services.identity import hub_devices, voice
from services.identity.main import app, get_session, require_api_key, require_internal
from services.identity.models import User, VoiceCandidate, VoiceSignature

SATELLITE = "assist_satellite.test_bedroom"


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _direction(seed: int) -> list[float]:
    rng = random.Random(seed)
    return _unit([rng.gauss(0, 1) for _ in range(voice.DIMENSIONS)])


def _mix(a: list[float], b: list[float], share: float) -> list[float]:
    """A voice ``share`` of the way from ``a`` towards ``b`` (similar voices)."""
    return _unit([(1 - share) * x + share * y for x, y in zip(a, b, strict=True)])


def _take(base: list[float], n: int, wobble: float) -> list[float]:
    rng = random.Random(n * 7919 + int(base[0] * 1e6))
    return [x + rng.gauss(0, wobble / math.sqrt(voice.DIMENSIONS)) for x in base]


def _profile(username: str, base: list[float], takes: int = 4, wobble: float = 0.4, role: str = "father") -> dict:
    return {"username": username, "name": username.title(), "role": role,
            "samples": [{"embedding": _take(base, n, wobble)} for n in range(takes)]}


def test_nothing_enrolled_keeps_the_default():
    result = voice.calibrate([])
    assert result["status"] == "default" and result["threshold"] == voice.MATCH_THRESHOLD


def test_one_person_enrolled_stays_under_their_own_scores_and_inside_the_bounds():
    result = voice.calibrate([_profile("one", _direction(1))])
    assert result["status"] == "single"
    assert result["other_speaker"] is None
    assert voice.CALIBRATED_MIN_THRESHOLD <= result["threshold"] <= voice.CALIBRATED_MAX_THRESHOLD
    assert result["threshold"] <= result["same_speaker"]["min"]


def test_distinct_voices_are_separated_above_every_other_speaker_score():
    profiles = [_profile("a", _direction(1)), _profile("b", _direction(2)), _profile("c", _direction(3), role="child")]
    result = voice.calibrate(profiles)
    assert result["status"] == "separated"
    assert result["threshold"] > result["other_speaker"]["max"]
    assert result["same_speaker"]["p10"] >= result["threshold"]
    assert voice.MIN_MARGIN <= result["margin"] <= voice.MAX_MARGIN


def test_similar_voices_raise_the_threshold_and_say_so():
    a = _direction(1)
    profiles = [_profile("a", a, wobble=1.2), _profile("b", _mix(a, _direction(2), 0.25), wobble=1.2)]
    result = voice.calibrate(profiles)
    assert result["threshold"] >= result["other_speaker"]["max"] + voice.IMPOSTOR_SAFETY - 1e-9 \
        or result["threshold"] == voice.CALIBRATED_MAX_THRESHOLD
    assert result["status"] in ("separated", "overlap")


def test_a_calibration_never_contains_a_signature():
    result = voice.calibrate([_profile("a", _direction(1)), _profile("b", _direction(2))])
    text = json.dumps(result)
    assert "embedding" not in text and len(text) < 2000


def test_a_weak_lead_between_a_child_and_an_adult_says_nothing():
    candidates = [{"username": "kid", "name": "Kid", "score": 0.86, "group": "child"},
                  {"username": "dad", "name": "Dad", "score": 0.80, "group": "adult"}]
    # Same-group, this lead would be enough...
    same = [dict(c, group="adult") for c in candidates]
    assert voice.decide(same, 0.80, 0.05)["matched"] is True
    # ...across age groups it is not: fall through to the device or the default.
    verdict = voice.decide(candidates, 0.80, 0.05)
    assert verdict["matched"] is False and verdict["cross_age"] is True
    # A clear lead across age groups still identifies.
    clear = [dict(candidates[0], score=0.92), candidates[1]]
    assert voice.decide(clear, 0.80, 0.05)["matched_username"] == "kid"


def test_auto_samples_never_push_out_enrolled_ones():
    enrolled = [{"embedding": [1.0], "n": i} for i in range(voice.MAX_SAMPLES)]
    auto = [{"embedding": [1.0], "source": voice.AUTO_SOURCE, "n": i} for i in range(voice.MAX_AUTO_SAMPLES + 3)]
    kept = voice.trim(enrolled + auto)
    assert [s for s in kept if s.get("source") != voice.AUTO_SOURCE] == enrolled
    assert len([s for s in kept if s.get("source") == voice.AUTO_SOURCE]) == voice.MAX_AUTO_SAMPLES


# --- through the service ---------------------------------------------------------------


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
        session.commit()
        for name, seed, role in (("parent", 11, "mother"), ("kid", 12, "child")):
            session.add(VoiceSignature(username=name, model=voice.MODEL, samples=json.dumps(
                _profile(name, _direction(seed), role=role)["samples"])))
        session.commit()
        yield session
    hub_devices.clear_cache()
    identity_main._calibration_cache.clear()


@pytest.fixture(name="client")
def client_fixture(session: Session):
    identity_main.engine = session.bind
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_internal] = lambda: True

    def _admin(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == "parent")).first()

    app.dependency_overrides[require_api_key] = _admin
    yield TestClient(app)
    app.dependency_overrides.clear()


def _assign(session, owner):
    hub_devices.sync(session, [{"hardware_id": "mac:02:00:00:00:00:09", "kind": "voice_satellite",
                                "name": "Test Bedroom", "entity_ids": [SATELLITE]}])
    hub_devices.assign(session, "hub:mac:02:00:00:00:00:09", {"owner_username": owner})


def test_identify_uses_the_stored_calibration(client, session):
    near_kid = _take(_direction(12), 99, 0.4)
    body = client.post("/api/voice/identify", json={"embedding": near_kid}).json()
    assert body["matched_username"] == "kid"
    assert body["calibration"] == "separated"
    stored = identity_main._voice_calibration(session)
    assert body["threshold"] == pytest.approx(stored["threshold"], abs=voice.CROSS_AGE_SCORE_LIFT + 1e-9)


def test_a_close_command_at_an_assigned_satellite_becomes_a_candidate_only(client, session):
    _assign(session, "kid")
    kid = _direction(12)
    far_field = _mix(kid, _direction(50), 0.45)  # the kid, but through a far-field microphone
    resp = client.post("/api/voice/candidate", json={"satellite": SATELLITE, "embedding": far_field,
                                                      "speech_seconds": 2.5}).json()
    assert resp["status"] == "PENDING"
    before = json.loads(session.get(VoiceSignature, "kid").samples)
    overview = client.get("/api/admin/voice").json()
    assert [c["username"] for c in overview["candidates"]] == ["kid"]
    assert "embedding" not in json.dumps(overview)
    # Nothing changed until the admin approves...
    assert json.loads(session.get(VoiceSignature, "kid").samples) == before
    cid = overview["candidates"][0]["id"]
    assert client.post(f"/api/admin/voice/candidates/{cid}").status_code == 200
    after = json.loads(session.get(VoiceSignature, "kid").samples)
    assert len(after) == len(before) + 1 and after[-1]["source"] == voice.AUTO_SOURCE
    assert after[: len(before)] == before


@pytest.mark.parametrize(("owner", "speech", "voice_of", "reason"), [
    (None, 2.5, lambda: _direction(12), "not assigned"),
    ("kid", 0.8, lambda: _direction(12), "too little speech"),
    # Close to the kid, but closer still to the parent.
    ("kid", 2.5, lambda: _mix(_direction(12), _direction(11), 0.52), "closer to someone else"),
    ("kid", 2.5, lambda: _direction(77), "not close"),
])
def test_candidates_are_refused_when_they_do_not_qualify(client, session, owner, speech, voice_of, reason):
    _assign(session, owner)
    resp = client.post("/api/voice/candidate", json={"satellite": SATELLITE, "embedding": voice_of(),
                                                      "speech_seconds": speech}).json()
    assert resp["status"] == "SKIPPED" and reason in resp["reason"]
    assert session.exec(select(VoiceCandidate)).all() == []


def test_a_rejected_candidate_is_gone_and_the_queue_is_capped(client, session, monkeypatch):
    _assign(session, "kid")
    monkeypatch.setattr(voice, "MAX_CANDIDATES", 2)
    for n in range(4):
        client.post("/api/voice/candidate", json={"satellite": SATELLITE, "embedding": _take(_direction(12), 200 + n, 0.8),
                                                   "speech_seconds": 3.0})
    rows = session.exec(select(VoiceCandidate)).all()
    assert len(rows) == 2
    assert client.delete(f"/api/admin/voice/candidates/{rows[0].id}").status_code == 200
    assert len(session.exec(select(VoiceCandidate)).all()) == 1


def test_recalibrating_is_admin_only_and_reports_scores_only(client, session):
    body = client.post("/api/admin/voice/calibrate").json()
    assert body["people"] == 2 and "embedding" not in json.dumps(body)

    def _kid(request_session: Session = Depends(get_session)) -> User:
        return request_session.exec(select(User).where(User.username == "kid")).first()

    app.dependency_overrides[require_api_key] = _kid
    assert client.post("/api/admin/voice/calibrate").status_code == 403
    assert client.get("/api/admin/voice").status_code == 403


def _household(people: int = 3) -> list[dict]:
    return [_profile(f"person{i}", _direction(i + 1)) for i in range(people)]


def test_a_quiet_household_still_names_the_right_person():
    """Every score sits under the absolute floor, yet the right person leads the cohort."""
    profiles = _household(3)
    quiet = [_mix(_direction(1), _direction(9), 0.5)]
    candidates = voice.rank(quiet[0], profiles)
    calibration = voice.calibrate(profiles)
    assert candidates[0]["score"] < calibration["threshold"]
    result = voice.decide(candidates, calibration["threshold"], calibration["margin"], calibration["relative"])
    assert result["matched"] is True
    assert result["matched_username"] == "person0"
    assert result["decided_by"] == "cohort"
    assert result["relative"] >= result["relative_needed"]


def test_the_measured_ambiguous_clip_names_nobody():
    """A live request scored 0.78 with the next person at 0.756: the cohort says
    the leader is above the house mean, but two-hundredths is a coin toss, so it
    falls back to the household rather than answer in the wrong person's voice."""
    candidates = [
        {"username": "jeremiah", "name": "Jeremiah", "score": 0.78, "group": "adult"},
        {"username": "zachary", "name": "Zachary", "score": 0.756, "group": "adult"},
        {"username": "noah", "name": "Noah", "score": 0.604, "group": "adult"},
    ]
    result = voice.decide(candidates, 0.752, 0.08, 0.03)
    assert result["relative"] >= result["relative_needed"]
    assert result["margin"] < result["margin_needed"]
    assert result["matched"] is False


def test_a_near_tie_inside_the_cohort_is_left_unmatched():
    candidates = [
        {"username": "jeremiah", "name": "Jeremiah", "score": 0.78, "group": "adult"},
        {"username": "zachary", "name": "Zachary", "score": 0.775, "group": "adult"},
        {"username": "noah", "name": "Noah", "score": 0.77, "group": "adult"},
    ]
    result = voice.decide(candidates, 0.752, 0.08, 0.03)
    assert result["matched"] is False
    assert result["decided_by"] is None


def test_a_lone_profile_has_no_cohort_and_falls_back_to_the_score():
    candidates = [{"username": "only", "name": "Only", "score": 0.83, "group": "adult"}]
    result = voice.decide(candidates, 0.78, 0.08, 0.03)
    assert result["relative"] is None
    assert result["matched"] is True
    assert result["decided_by"] == "score"
    weak = voice.decide([{"username": "only", "name": "Only", "score": 0.70, "group": "adult"}], 0.78, 0.08, 0.03)
    assert weak["matched"] is False


def test_a_stranger_never_rides_a_quiet_cohort():
    """Leading a cohort of quiet scores is not enough when the absolute score is weak."""
    candidates = [
        {"username": "a", "name": "A", "score": 0.62, "group": "adult"},
        {"username": "b", "name": "B", "score": 0.55, "group": "adult"},
        {"username": "c", "name": "C", "score": 0.54, "group": "adult"},
    ]
    result = voice.decide(candidates, 0.78, 0.08, 0.03)
    assert result["relative"] >= result["relative_needed"]
    assert result["score"] < result["relative_floor"]
    assert result["matched"] is False


def test_a_child_and_an_adult_need_double_the_relative_lead():
    candidates = [
        {"username": "dad", "name": "Dad", "score": 0.79, "group": "adult"},
        {"username": "kid", "name": "Kid", "score": 0.76, "group": "child"},
        {"username": "other", "name": "Other", "score": 0.60, "group": "adult"},
    ]
    result = voice.decide(candidates, 0.752, 0.08, 0.03)
    assert result["cross_age"] is True
    assert result["relative_needed"] == pytest.approx(0.06, abs=0.001)
    assert result["matched"] is False


def test_calibration_derives_a_relative_lead_from_the_household():
    calibration = voice.calibrate(_household(4))
    assert 0.02 <= calibration["relative"] <= 0.15
    assert calibration["relative_speaker"]["n"] >= 1
    assert calibration["relative_impostor"]["n"] >= 1


def test_a_parent_is_not_refused_because_a_child_is_second():
    """Three live requests were rejected this way: the parent led by 0.16-0.18
    with their own child as runner-up, and the doubling asked for 0.20."""
    candidates = [
        {"username": "dad", "name": "Dad", "score": 0.813, "group": "adult"},
        {"username": "kid", "name": "Kid", "score": 0.635, "group": "child"},
        {"username": "other", "name": "Other", "score": 0.58, "group": "adult"},
    ]
    result = voice.decide(candidates, 0.72, 0.10, 0.03)
    assert result["margin_needed"] == pytest.approx(0.10, abs=0.001)
    assert result["matched_username"] == "dad" and result["decided_by"] == "score"


def test_a_quiet_clip_beside_a_child_still_stands_out():
    """Under the old floor this clip was 0.73 against 0.752 and went to the default."""
    candidates = [
        {"username": "dad", "name": "Dad", "score": 0.73, "group": "adult"},
        {"username": "kid", "name": "Kid", "score": 0.60, "group": "child"},
        {"username": "other", "name": "Other", "score": 0.52, "group": "adult"},
    ]
    result = voice.decide(candidates, 0.752, 0.08, 0.03)
    assert result["score"] < result["threshold"]
    assert result["matched_username"] == "dad" and result["decided_by"] == "cohort"


def test_a_calibration_from_an_older_version_is_not_reused():
    current = {"key": "ge2e:abc", "version": voice.CALIBRATION_VERSION, "threshold": 0.72}
    assert voice.calibration_is_current(current, "ge2e:abc") is True
    assert voice.calibration_is_current({**current, "version": voice.CALIBRATION_VERSION - 1}, "ge2e:abc") is False
    assert voice.calibration_is_current({"key": "ge2e:abc", "threshold": 0.782}, "ge2e:abc") is False
    assert voice.calibration_is_current(None, "ge2e:abc") is False
    assert voice.calibration_is_current(current, "ge2e:other") is False
