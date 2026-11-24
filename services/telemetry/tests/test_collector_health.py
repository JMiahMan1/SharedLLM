"""Collection health: a phone's own report, and the server's staleness check
for phones that stopped sending calls, texts or screen time."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry import collector_health as ch
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


@pytest.fixture
def rc(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    return fake


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _report(**extra):
    body = {
        "user": "kiddo",
        "device_id": "dev-0001",
        "device_name": "Test Phone",
        "app_version": "1.2.3",
        "app_build": 512,
        "permissions": {"read_sms": True, "read_call_log": False, "usage_access": True,
                        "notification_listener": True, "location": True},
        "workers": {"comms_sync": "CANCELLED", "usage_sync": "ENQUEUED", "photo_scan": "RUNNING"},
        "collectors": {"comms": {"at": 1_700_000_000_000, "ok": False, "outcome": "signed_out",
                                 "message": "Not signed in on this phone", "last_ok_at": 1_699_000_000_000},
                       "listener": {"at": 1_700_000_000_000, "ok": True, "outcome": "ok", "message": "1 calls, 2 texts"}},
        "reported_at": 1_700_000_100_000,
    }
    body.update(extra)
    return body


# --- the phone's report -------------------------------------------------------


def test_a_report_is_kept_and_read_back_with_its_problems(client, rc):
    r = client.post("/api/telemetry/device_health", headers=HEADERS, json=_report())
    assert r.status_code == 200, r.text
    got = client.get("/api/telemetry/device_health/kiddo", headers=HEADERS).json()
    [device] = got["devices"]
    assert device["device_id"] == "dev-0001" and device["app_version"] == "1.2.3"
    assert device["received_at"]
    assert device["collectors"]["listener"]["ok"] is True
    kinds = [(p["kind"], p["what"]) for p in device["problems"]]
    assert kinds == [("permission", "read_call_log"), ("worker", "comms_sync"), ("collector", "comms")]
    assert device["problems"][2]["outcome"] == "signed_out"


def test_only_allow_listed_fields_are_stored():
    report = ch.clean_report(_report(
        body="a message text that must never be kept",
        permissions={"read_sms": True, "contacts": ["someone"], "location": "yes"},
        workers={"comms_sync": "enqueued", "usage_sync": "<script>", "other": "ENQUEUED"},
        collectors={"comms": {"at": 5, "ok": True, "outcome": "ok", "message": "x" * 500, "body": "secret"},
                    "sms_dump": {"at": 1}},
    ))
    assert "body" not in report and "user" not in report
    assert report["permissions"] == {"read_sms": True}
    assert report["workers"] == {"comms_sync": "ENQUEUED"}
    assert set(report["collectors"]) == {"comms"}
    assert "body" not in report["collectors"]["comms"]
    assert len(report["collectors"]["comms"]["message"]) == 200


def test_a_report_without_a_device_is_refused(client, rc):
    r = client.post("/api/telemetry/device_health", headers=HEADERS, json=_report(device_id=""))
    assert r.status_code == 422
    r = client.post("/api/telemetry/device_health", headers=HEADERS, json=_report(device_id="bad id with spaces"))
    assert r.status_code == 422
    assert client.post("/api/telemetry/device_health", headers=HEADERS, json=_report(user="")).status_code == 422


def test_the_routes_need_the_internal_secret(client, rc):
    assert client.post("/api/telemetry/device_health", json=_report()).status_code == 401
    assert client.get("/api/telemetry/device_health/kiddo").status_code == 401


def test_each_device_keeps_its_latest_report(client, rc):
    client.post("/api/telemetry/device_health", headers=HEADERS, json=_report())
    client.post("/api/telemetry/device_health", headers=HEADERS,
                json=_report(workers={"comms_sync": "ENQUEUED"}, permissions={"read_call_log": True}, collectors={}))
    client.post("/api/telemetry/device_health", headers=HEADERS, json=_report(device_id="dev-0002"))
    devices = client.get("/api/telemetry/device_health/kiddo", headers=HEADERS).json()["devices"]
    assert sorted(d["device_id"] for d in devices) == ["dev-0001", "dev-0002"]
    first = next(d for d in devices if d["device_id"] == "dev-0001")
    assert first["problems"] == []


async def test_the_oldest_devices_go_past_the_limit():
    rc = FakeRedis()
    for i in range(ch.MAX_DEVICES + 2):
        await ch.save_report(rc, "kiddo", ch.clean_report({"device_id": f"dev-{i:04d}"}),
                             now=NOW + timedelta(minutes=i))
    kept = [d["device_id"] for d in await ch.list_reports(rc, "kiddo")]
    assert len(kept) == ch.MAX_DEVICES
    assert "dev-0000" not in kept and "dev-0001" not in kept
    assert kept[0] == f"dev-{ch.MAX_DEVICES + 1:04d}"


def test_stored_reports_are_plain_json(client, rc):
    client.post("/api/telemetry/device_health", headers=HEADERS, json=_report())
    raw = rc.hashes[f"{ch.HEALTH_PREFIX}kiddo"]["dev-0001"]
    assert json.loads(raw)["device_id"] == "dev-0001"


# --- the server's own view ----------------------------------------------------


def _seen(**hours_ago):
    return {kind: NOW - timedelta(hours=h) for kind, h in hours_ago.items()}


def test_comms_are_stale_when_social_keeps_arriving_long_after_the_last_text():
    out = ch.staleness(_seen(sms=40, calls=50, social=1, driving=3), comms_hours=12)
    assert out["comms"]["stale"] is True
    assert out["comms"]["last_at"] == (NOW - timedelta(hours=40)).isoformat()
    assert out["comms"]["calls_last_at"] == (NOW - timedelta(hours=50)).isoformat()
    assert out["other_last_at"] == (NOW - timedelta(hours=1)).isoformat()


def test_a_quiet_evening_is_not_stale():
    assert ch.staleness(_seen(sms=8, social=1), comms_hours=12)["comms"]["stale"] is False


def test_a_child_who_never_had_calls_or_texts_is_not_flagged():
    out = ch.staleness(_seen(social=1, media=2), comms_hours=12)
    assert out["comms"] == {"stale": False, "last_at": None, "calls_last_at": None, "sms_last_at": None}


def test_a_phone_that_went_silent_altogether_is_not_a_comms_problem():
    # Everything stopped at once (phone off): nothing kept arriving after the texts.
    assert ch.staleness(_seen(sms=40, social=39), comms_hours=12)["comms"]["stale"] is False


def test_usage_is_stale_after_two_days_while_other_activity_arrives():
    out = ch.staleness(_seen(usage=60, social=2), usage_hours=48)
    assert out["usage"]["stale"] is True
    assert ch.staleness(_seen(usage=20, social=2), usage_hours=48)["usage"]["stale"] is False
    assert ch.staleness(_seen(social=2), usage_hours=48)["usage"]["stale"] is False


def test_last_seen_takes_the_newest_of_arrivals_events_and_usage_days():
    seen = ch.last_seen(
        {"sms": (NOW - timedelta(hours=5)).isoformat()},
        [{"feature": "sms", "at": (NOW - timedelta(hours=30)).isoformat()},
         {"feature": "calls", "at": "2026-11-20T10:00:00"},
         {"feature": "social", "at": "not a time"}],
        [{"day": "2026-11-20"}, {"day": "2026-11-19", "updated_at": "2026-11-19T23:00:00+00:00"}],
    )
    assert seen["sms"] == NOW - timedelta(hours=5)
    assert seen["calls"] == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    assert "social" not in seen
    # A day without an update time counts from its end.
    assert seen["usage"] == datetime(2026, 11, 21, 0, 0, tzinfo=UTC)


def test_arrivals_are_noted_as_activity_and_usage_land(client, rc, monkeypatch):
    from services.telemetry import guardian

    async def no_scan(*args, **kwargs):
        return []

    monkeypatch.setattr(guardian, "on_activity", no_scan)
    client.post("/api/telemetry/activity", headers=HEADERS,
                json={"user": "kiddo", "feature": "social", "item": "Chat app"})
    client.post("/api/telemetry/usage", headers=HEADERS, json={"user": "kiddo", "day": "2026-11-22", "millis": 60000})
    got = client.get("/api/telemetry/device_health/kiddo", headers=HEADERS).json()
    assert got["devices"] == []
    staleness = got["staleness"]
    assert staleness["other_last_at"] and staleness["usage"]["last_at"]
    assert staleness["comms"]["stale"] is False


def test_the_route_flags_a_phone_that_stopped_sending_texts(client, rc):
    # Texts from before arrivals were kept (an old event only), social still arriving now.
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    rc.lists["tel:activity:kiddo"] = [json.dumps({"user": "kiddo", "feature": "sms", "at": old, "detail": {}})]
    rc.hashes["tel:arrivals:kiddo"] = {"social": datetime.now(UTC).isoformat()}
    got = client.get("/api/telemetry/device_health/kiddo", headers=HEADERS).json()
    assert got["staleness"]["comms"]["stale"] is True
    assert got["staleness"]["comms"]["sms_last_at"] == old


def test_a_missing_bluetooth_permission_is_kept_and_reported():
    """Without Nearby devices the phone cannot say which linked car a trip was in."""
    from services.telemetry import collector_health as ch

    report = ch.clean_report({"device_id": "dev-1", "permissions": {"bluetooth": False, "location": True}})
    assert report["permissions"] == {"bluetooth": False, "location": True}
    assert {"kind": "permission", "what": "bluetooth"} in ch.problems(report)
