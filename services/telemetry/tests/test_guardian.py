"""Watching: a child's events are scanned as they land; parents get the recap on Sunday."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.telemetry import guardian
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

from fakes import FakeRedis

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

    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    monkeypatch.setattr(guardian, "children", _children)
    monkeypatch.setattr("services.telemetry.main.deliver_notice", _deliver)
    with TestClient(app) as client:
        yield client, fake, sent


def _sms(user, body, at="2026-10-21T10:00:00Z"):
    return {"user": user, "feature": "sms", "item": "Unknown", "action": "incoming", "at": at, "detail": {"body": body}}


def test_a_severe_message_pages_both_parents_once_a_day(wired):
    client, fake, sent = wired
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
    client, fake, sent = wired
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
