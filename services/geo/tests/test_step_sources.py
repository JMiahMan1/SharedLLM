"""Per-device step history.

A fused daily total cannot say which device produced it -- a phone left in a
drawer and a watch that walked all afternoon look identical. These tests pin
the two distinctions that keep a device's own page honest:

* a source with nothing recorded is **absent**, not zero, because no report
  and a reported zero are different facts and only one means the device works;
* the window is the caller's to choose and the store's to obey, so old days
  are left out rather than silently included with today's.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET

SECRET = {"X-Internal-Secret": INTERNAL_SECRET}
TZ = ZoneInfo("America/Phoenix")


class PopulatedRedis:
    """Only the hash reads this route makes, over a fixed store."""

    def __init__(self, hashes: dict[str, dict[str, str]] | None = None):
        self.hashes = hashes or {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)


def day(offset: int) -> str:
    """A local day, `offset` days before today. 0 is today."""
    return (datetime.now(TZ) - timedelta(days=offset)).strftime("%Y-%m-%d")


@pytest.fixture
def client():
    return TestClient(geo.app, headers=SECRET)


def _use(monkeypatch, hashes):
    store = PopulatedRedis(hashes)

    async def _get():
        return store

    monkeypatch.setattr(geo, "get_redis", _get)
    return store


def test_each_device_keeps_its_own_days(client, monkeypatch):
    _use(
        monkeypatch,
        {
            f"geo:steps_src:jeremiah:phone": {day(1): "1200", day(0): "900"},
            f"geo:steps_src:jeremiah:watch": {day(0): "1500"},
        },
    )
    body = client.get("/steps/sources", params={"user_id": "jeremiah", "days": 7}).json()
    assert body["sources"] == {
        "phone": {day(1): 1200, day(0): 900},
        "watch": {day(0): 1500},
    }


def test_a_device_that_never_reported_is_absent_rather_than_zero(client, monkeypatch):
    """The phone exists as a source but has nothing; the watch is not there at
    all. Neither may appear as a zero day, which would read as "it worked and
    you walked nowhere"."""
    _use(monkeypatch, {"geo:steps_src:jeremiah:phone": {day(0): "900"}})
    body = client.get("/steps/sources", params={"user_id": "jeremiah", "days": 7}).json()
    assert "watch" not in body["sources"]
    assert set(body["sources"]["phone"]) == {day(0)}


def test_days_before_the_window_are_left_out(client, monkeypatch):
    _use(
        monkeypatch,
        {
            f"geo:steps_src:jeremiah:phone": {
                day(0): "900",
                day(3): "800",
                day(30): "700",
            }
        },
    )
    body = client.get("/steps/sources", params={"user_id": "jeremiah", "days": 7}).json()
    assert set(body["sources"]["phone"]) == {day(0), day(3)}
    assert day(30) not in body["sources"]["phone"]


def test_a_long_window_keeps_more_of_the_history(client, monkeypatch):
    _use(monkeypatch, {f"geo:steps_src:jeremiah:phone": {day(0): "900", day(30): "700"}})
    body = client.get("/steps/sources", params={"user_id": "jeremiah", "days": 60}).json()
    assert set(body["sources"]["phone"]) == {day(0), day(30)}


def test_the_days_arrive_in_order(client, monkeypatch):
    _use(
        monkeypatch,
        {f"geo:steps_src:jeremiah:phone": {day(0): "900", day(4): "800", day(2): "700"}},
    )
    body = client.get("/steps/sources", params={"user_id": "jeremiah", "days": 7}).json()
    assert list(body["sources"]["phone"]) == [day(4), day(2), day(0)]


def test_an_unreadable_count_is_skipped_rather_than_guessed(client, monkeypatch):
    _use(
        monkeypatch,
        {f"geo:steps_src:jeremiah:phone": {day(0): "900", day(1): "not a number"}},
    )
    body = client.get("/steps/sources", params={"user_id": "jeremiah", "days": 7}).json()
    assert body["sources"]["phone"] == {day(0): 900}


def test_hourly_history_is_opt_in(client, monkeypatch):
    _use(
        monkeypatch,
        {
            f"geo:steps_hourly:jeremiah:phone": {
                f"{day(0)}:9": "200",
                f"{day(0)}:10": "300",
                f"{day(9)}:9": "999",
            }
        },
    )
    quiet = client.get("/steps/sources", params={"user_id": "jeremiah"}).json()
    assert quiet["hourly"] == {}
    loud = client.get(
        "/steps/sources", params={"user_id": "jeremiah", "hourly": "true"}
    ).json()
    assert loud["hourly"] == {"phone": {f"{day(0)}:9": 200, f"{day(0)}:10": 300}}


def test_last_synced_is_the_last_successful_upload(client, monkeypatch):
    _use(monkeypatch, {"geo:steps_meta:jeremiah": {"updated_at": "1759700000.5"}})
    body = client.get("/steps/sources", params={"user_id": "jeremiah"}).json()
    assert body["last_synced"] == 1759700000.5


def test_no_sync_yet_is_reported_as_null_not_omitted(client, monkeypatch):
    _use(monkeypatch, {})
    body = client.get("/steps/sources", params={"user_id": "jeremiah"}).json()
    assert body["last_synced"] is None
    assert body["sources"] == {}


def test_a_broken_timestamp_does_not_break_the_reading(client, monkeypatch):
    _use(
        monkeypatch,
        {
            "geo:steps_meta:jeremiah": {"updated_at": "yesterday"},
            f"geo:steps_src:jeremiah:phone": {day(0): "900"},
        },
    )
    body = client.get("/steps/sources", params={"user_id": "jeremiah"}).json()
    assert body["last_synced"] is None
    assert body["sources"]["phone"] == {day(0): 900}
