"""General phone usage: day totals with the window the day actually spanned."""
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
    # Skip lifespan: startup is Redis/scheduler work the tests replace.
    with TestClient(app) as c:
        yield c


def _usage(day="2026-10-23", millis=3_600_000, first=1_700_000_000_000, last=1_700_004_000_000, sessions=4, **extra):
    body = {
        "user": "kiddo",
        "day": day,
        "millis": millis,
        "first_use": first,
        "last_use": last,
        "sessions": sessions,
    }
    body.update(extra)
    return body


def test_records_and_reads_back_a_day(client, rc):
    resp = client.post("/api/telemetry/usage", headers=HEADERS, json=_usage())
    assert resp.status_code == 200
    assert resp.json()["usage"]["millis"] == 3_600_000

    got = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()
    assert got["status"] == "SUCCESS"
    assert got["days"][0]["day"] == "2026-10-23"
    assert got["days"][0]["sessions"] == 4


def test_a_later_report_merges_into_the_day(client, rc):
    client.post(
        "/api/telemetry/usage",
        headers=HEADERS,
        json=_usage(millis=3_600_000, last=1_700_004_000_000, sessions=4),
    )
    client.post(
        "/api/telemetry/usage",
        headers=HEADERS,
        json=_usage(millis=7_200_000, first=1_699_999_000_000, last=1_700_010_000_000, sessions=9),
    )

    day = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()["days"][0]
    assert day["millis"] == 7_200_000  # the longest screen time wins
    assert day["first_use"] == 1_699_999_000_000  # the earliest start wins
    assert day["last_use"] == 1_700_010_000_000  # the latest stop wins
    assert day["sessions"] == 9


def test_a_smaller_report_never_shrinks_the_day(client, rc):
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(millis=7_200_000, sessions=9))
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(millis=60_000, sessions=1))

    day = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()["days"][0]
    assert day["millis"] == 7_200_000
    assert day["sessions"] == 9


def test_days_come_back_newest_first(client, rc):
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(day="2026-10-22"))
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(day="2026-10-23"))

    days = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()["days"]
    assert [d["day"] for d in days] == ["2026-10-23", "2026-10-22"]


def test_the_days_query_is_capped(client, rc):
    assert client.get("/api/telemetry/usage/kiddo?days=999", headers=HEADERS).status_code == 422


def test_requires_a_user_and_a_day(client, rc):
    assert client.post("/api/telemetry/usage", headers=HEADERS, json={}).status_code == 422
    assert client.post("/api/telemetry/usage", headers=HEADERS, json={"user": "kiddo"}).status_code == 422


def test_negative_millis_is_refused(client, rc):
    assert client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(millis=-5)).status_code == 422


def test_per_app_foreground_time_merges_by_package(client, rc):
    client.post(
        "/api/telemetry/usage",
        headers=HEADERS,
        json=_usage(apps=[{"package": "com.youtube", "millis": 1_800_000, "last_use": 1_700_003_000_000},
                         {"package": "com.spotify", "millis": 900_000, "last_use": 1_700_002_000_000}]),
    )
    client.post(
        "/api/telemetry/usage",
        headers=HEADERS,
        json=_usage(apps=[{"package": "com.youtube", "millis": 2_000_000, "last_use": 1_700_004_000_000},
                         {"package": "com.chrome", "millis": 300_000, "last_use": 1_700_001_000_000}]),
    )

    day = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()["days"][0]
    by_pkg = {a["package"]: a for a in day["apps"]}
    assert by_pkg["com.youtube"]["millis"] == 2_000_000  # longest wins
    assert by_pkg["com.youtube"]["last_use"] == 1_700_004_000_000  # latest wins
    assert by_pkg["com.spotify"]["millis"] == 900_000
    assert by_pkg["com.chrome"]["millis"] == 300_000
    # sorted by millis descending
    assert [a["package"] for a in day["apps"]] == ["com.youtube", "com.spotify", "com.chrome"]


def test_needs_the_internal_secret(client, rc):
    assert client.post("/api/telemetry/usage", json=_usage()).status_code == 401


def test_a_phone_and_a_tablet_add_up_rather_than_overwrite(client, rc):
    phone, tablet = "a" * 32, "b" * 32
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(
        device=phone, millis=3_600_000, first=1_700_000_000_000, last=1_700_004_000_000, sessions=4,
        apps=[{"package": "com.youtube", "millis": 1_000_000, "last_use": 1_700_003_000_000}]))
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(
        device=tablet, millis=1_800_000, first=1_699_990_000_000, last=1_700_002_000_000, sessions=2,
        apps=[{"package": "com.youtube", "millis": 500_000, "last_use": 1_700_002_000_000},
              {"package": "com.minecraft", "millis": 900_000, "last_use": 1_700_001_000_000}]))
    # The phone re-reports later in the day; only its own reading grows.
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(device=phone, millis=4_000_000, sessions=5))

    day = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()["days"][0]
    assert day["millis"] == 4_000_000 + 1_800_000
    assert day["sessions"] == 5 + 2
    assert day["first_use"] == 1_699_990_000_000
    assert day["last_use"] == 1_700_004_000_000
    assert day["devices"] == 2
    by_pkg = {a["package"]: a["millis"] for a in day["apps"]}
    assert by_pkg == {"com.youtube": 1_500_000, "com.minecraft": 900_000}


def test_an_unnamed_report_is_dropped_once_a_device_names_itself(client, rc):
    # An app from before devices were told apart reported without a key; the
    # same phone then reports with its key. Counting both would double the day.
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(millis=3_000_000))
    client.post("/api/telemetry/usage", headers=HEADERS, json=_usage(device="c" * 32, millis=3_100_000))

    day = client.get("/api/telemetry/usage/kiddo", headers=HEADERS).json()["days"][0]
    assert day["millis"] == 3_100_000
    assert day["devices"] == 1
