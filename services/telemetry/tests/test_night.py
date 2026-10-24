"""Overnight screen-on stretches, stored per device for the sleep estimate."""
import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}


@pytest.fixture
def rc(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    return fake


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _night(device="tablet-key-0000001", intervals=None, night="2026-10-21"):
    return {"device": device, "night": night, "user": "kiddo", "intervals": intervals if intervals is not None else [[1, 2]]}


def test_records_and_reads_back_per_device(client, rc):
    assert client.post("/api/telemetry/night", headers=HEADERS, json=_night(intervals=[[10, 20]])).status_code == 200
    client.post("/api/telemetry/night", headers=HEADERS, json=_night(device="phone-key-00000001", intervals=[[30, 40]]))
    got = client.get(
        "/api/telemetry/night",
        headers=HEADERS,
        params={"night": "2026-10-21", "devices": "tablet-key-0000001,phone-key-00000001,unknown"},
    ).json()
    assert [n["intervals"] for n in got["nights"]] == [[[10, 20]], [[30, 40]]]


def test_a_later_report_only_adds_stretches(client, rc):
    client.post("/api/telemetry/night", headers=HEADERS, json=_night(intervals=[[10, 20], [50, 60]]))
    merged = client.post("/api/telemetry/night", headers=HEADERS, json=_night(intervals=[[50, 60], [70, 80], ["x", 1]])).json()
    assert merged["night"]["intervals"] == [[10, 20], [50, 60], [70, 80]]


def test_requires_the_fields_and_the_secret(client, rc):
    assert client.post("/api/telemetry/night", headers=HEADERS, json={"device": "d", "night": "n"}).status_code == 422
    assert client.post("/api/telemetry/night", json=_night()).status_code in (401, 403)


def test_keeps_two_weeks_of_nights(client, rc):
    for day in range(1, 20):
        client.post("/api/telemetry/night", headers=HEADERS, json=_night(night=f"2026-10-{day:02d}"))
    kept = client.get("/api/telemetry/night", headers=HEADERS, params={"night": "2026-10-01", "devices": "tablet-key-0000001"}).json()
    assert kept["nights"] == []
