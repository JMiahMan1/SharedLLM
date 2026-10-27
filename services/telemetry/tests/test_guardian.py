"""Watching: a child's events are scanned as they land; parents get the recap on Sunday."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry import guardian
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}
KIDS = {"kid": {"username": "kid", "display_name": "Emma", "parents": ["dad", "mom"], "monitoring": "severe"}}


@pytest.fixture
def wired(monkeypatch):
    fake = FakeRedis()
    sent: list[dict] = []

    async def _children(_session):
        return KIDS

    async def _deliver(body):
        sent.append(body)
        return {"status": "SUCCESS"}

    async def _no_model(_session):
        return {}

    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    monkeypatch.setattr(guardian, "children", _children)
    # No local model configured unless a test says otherwise: the phrase list decides.
    monkeypatch.setattr(guardian, "_model_settings", _no_model)
    monkeypatch.setattr("services.telemetry.main.deliver_notice", _deliver)
    with TestClient(app) as client:
        yield client, fake, sent


def _sms(user, body, at="2026-10-21T10:00:00Z"):
    return {"user": user, "feature": "sms", "item": "Unknown", "action": "incoming", "at": at, "detail": {"body": body}}


def test_a_severe_message_pages_both_parents_once_a_day(wired):
    client, _fake, sent = wired
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "i want to die"))
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert sent[0]["priority"] == 5 and sent[0]["title"] == "Self-harm or suicide on Emma's phone"
    assert "want to die" in sent[0]["body"] and sent[0]["once"].startswith("concern:kid:self_harm:")
    concerns = client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"]
    assert concerns[0]["category"] == "self_harm"


def test_high_concerns_wait_for_the_recap_at_default_sensitivity(wired):
    client, _, sent = wired
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "nobody likes you loser"))
    assert sent == []
    assert client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"][0]["category"] == "bullying"


def test_an_adults_messages_are_never_scanned(wired):
    client, _fake, sent = wired
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("dad", "i want to die of embarrassment"))
    assert sent == [] and client.get("/api/telemetry/concerns/dad", headers=HEADERS).json()["concerns"] == []


def test_the_recap_route_builds_the_week(wired):
    client, _, _ = wired
    client.post("/api/telemetry/usage", headers=HEADERS, json={"user": "kid", "day": "2026-10-20", "millis": 7_200_000, "sessions": 3})
    client.post("/api/telemetry/activity", headers=HEADERS, json={"user": "kid", "feature": "bible", "item": "John 3", "at": "2026-10-20T07:00:00Z"})
    recap = client.get("/api/telemetry/recap/kid", headers=HEADERS, params={"day": "2026-10-21"}).json()["recap"]
    assert recap["week"] == {"start": "2026-10-19", "end": "2026-10-25"}
    assert recap["screen"]["total_min"] == 120 and recap["faith"]["bible_days"] == 1


def test_sunday_evening_sends_each_recap_once(wired):
    _, fake, _ = wired
    sent: list[dict] = []

    async def deliver(body):
        sent.append(body)

    tz = ZoneInfo("America/Phoenix")
    saturday = datetime(2026, 10, 24, 19, 0, tzinfo=tz)
    sunday_noon = datetime(2026, 10, 25, 12, 0, tzinfo=tz)
    sunday_evening = datetime(2026, 10, 25, 18, 5, tzinfo=tz)
    assert asyncio.run(guardian.send_weekly_recaps(fake, None, deliver, saturday)) == 0
    assert asyncio.run(guardian.send_weekly_recaps(fake, None, deliver, sunday_noon)) == 0
    assert asyncio.run(guardian.send_weekly_recaps(fake, None, deliver, sunday_evening)) == 1
    assert asyncio.run(guardian.send_weekly_recaps(fake, None, deliver, sunday_evening)) == 0
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert sent[0]["title"] == "Emma's week" and sent[0]["kind"] == "recap"


def _with_model(monkeypatch, answer):
    """A configured local model whose verdict is ``answer`` (a dict, or an exception to raise)."""
    async def _settings(_session):
        return {"llm_local_url": "http://llm.local", "guardian_monitor_model": "qwen3:8b"}

    asked: list[dict] = []

    async def _ask(_session, url, model, concern, text):
        asked.append({"url": url, "model": model, "category": concern["category"], "text": text})
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(guardian, "_model_settings", _settings)
    monkeypatch.setattr(guardian, "_ask_model", _ask)
    return asked


def test_without_a_model_the_phrase_list_decides_and_says_why(wired):
    client, _, sent = wired
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "i want to die"))
    assert [n["user"] for n in sent] == ["dad", "mom"]
    review = client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"][0]["review"]
    assert review["by"] == "phrases" and "no local model configured" in review["reason"]


def test_the_model_dismisses_a_false_alarm_which_stays_on_record_unpaged(wired, monkeypatch):
    client, _, sent = wired
    asked = _with_model(monkeypatch, {"concern": False, "level": None, "reason": "A song lyric, not a threat."})
    monkeypatch.setitem(KIDS["kid"], "monitoring", "all")
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "everyone hates you lol that song slaps"))
    assert sent == []
    assert asked[0]["model"] == "qwen3:8b" and "that song slaps" in asked[0]["text"]
    kept = client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"][0]
    assert kept["review"]["verdict"] == "dismissed" and kept["review"]["reason"] == "A song lyric, not a threat."
    recap = client.get("/api/telemetry/recap/kid", headers=HEADERS, params={"day": "2026-10-21"}).json()["recap"]
    assert recap["concerns"] == []


def test_the_model_can_raise_the_level_and_its_reason_reaches_the_parent(wired, monkeypatch):
    client, _, sent = wired
    _with_model(monkeypatch, {"concern": True, "level": "severe", "reason": "Repeated, targeted at the child."})
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "nobody likes you, just leave"))
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert "Checked: Repeated, targeted at the child." in sent[0]["body"]
    kept = client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"][0]
    assert kept["level"] == "severe" and kept["review"]["phrase_level"] == "high"


def test_self_harm_is_never_silenced_by_the_model(wired, monkeypatch):
    client, _, sent = wired
    _with_model(monkeypatch, {"concern": False, "level": None, "reason": "Probably a joke."})
    client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "i want to die"))
    assert [n["user"] for n in sent] == ["dad", "mom"] and sent[0]["priority"] == 5
    assert "Unsure: Probably a joke." in sent[0]["body"]
    assert client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"][0]["review"]["verdict"] == "doubted"


def test_an_unreachable_model_falls_back_to_the_phrase_list_with_a_logged_reason(wired, monkeypatch, caplog):
    client, _, sent = wired
    _with_model(monkeypatch, RuntimeError("connection refused"))
    with caplog.at_level("WARNING", logger="telemetry.guardian"):
        client.post("/api/telemetry/activity", headers=HEADERS, json=_sms("kid", "i want to die"))
    assert [n["user"] for n in sent] == ["dad", "mom"]
    review = client.get("/api/telemetry/concerns/kid", headers=HEADERS).json()["concerns"][0]["review"]
    assert review == {"by": "phrases", "reason": "local model unavailable: connection refused"}
    assert "using the phrase list" in caplog.text


def test_repeated_blocked_site_attempts_page_both_parents_once_a_day(wired):
    client, _fake, sent = wired
    # Fewer than 5 blocked attempts on a domain do not page parents.
    client.post("/api/telemetry/activity", headers=HEADERS, json={
        "user": "kid",
        "feature": "web",
        "item": "bad.example",
        "action": "blocked",
        "detail": {"visits": 0, "blocked": 3},
    })
    assert sent == []

    # Crossing 5 blocked attempts on the same domain pages both parents once that day.
    client.post("/api/telemetry/activity", headers=HEADERS, json={
        "user": "kid",
        "feature": "web",
        "item": "bad.example",
        "action": "blocked",
        "detail": {"visits": 0, "blocked": 2},
    })
    assert [n["user"] for n in sent] == ["dad", "mom"]
    assert sent[0]["kind"] == "blocked_site"
    assert sent[0]["title"] == "Blocked site on Emma's phone"
    assert "bad.example (5 blocked attempts today)" in sent[0]["body"]
    assert sent[0]["once"].startswith("blocked_site:kid:bad.example:")

    # Further blocked attempts on the same domain the same day do not re-page.
    sent.clear()
    client.post("/api/telemetry/activity", headers=HEADERS, json={
        "user": "kid",
        "feature": "web",
        "item": "bad.example",
        "action": "blocked",
        "detail": {"visits": 0, "blocked": 4},
    })
    assert sent == []
