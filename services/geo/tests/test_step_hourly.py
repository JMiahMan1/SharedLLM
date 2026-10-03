"""Hour-by-hour step buckets: ingest, per-hour max, and the day view.

The phone accumulates deltas from the cumulative pedometer into an on-device
`hours` table and uploads the running total for each hour. Two properties matter
enough to pin:

* an hour keeps the **max** seen, like a day bucket, so a re-upload (or an hour
  the app re-sent after a restart) is idempotent and can never lower an hour
  that was already credited; and
* a day with no hourly detail is **absent**, never a list of 24 zeros. "Your
  phone has not reported hourly steps yet" and "you sat still for 24 hours" are
  different facts and the UI must be able to tell them apart.

The day an hour belongs to is the day the phone filed its own reading under --
derived from the same `timestamp` + `timezone` that buckets the daily total --
so the two can never disagree.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.geo import main as geo_main

TZ = ZoneInfo("America/Phoenix")
HEADERS = {"X-Internal-Secret": geo_main.INTERNAL_SECRET}


class FakeRedis:
    """Just enough Redis for the step write path."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.strings: dict[str, str] = {}

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = str(value)
        return 1

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = str(value)
        return True

    async def expire(self, key, seconds):
        return True


@pytest.fixture
def rc():
    return FakeRedis()


def ts_for(day: str, hour: int = 12) -> float:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=TZ).timestamp()


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()

    async def fake_redis():
        return fake

    monkeypatch.setattr(geo_main, "get_redis", fake_redis)
    monkeypatch.setitem(geo_main.__dict__, "_FAKE", fake)
    return TestClient(geo_main.app)


@pytest.fixture
def fake(client):
    return geo_main.__dict__["_FAKE"]


class TestIngest:
    async def test_hours_land_under_their_day_and_hour(self, rc):
        await geo_main._record_hourly_steps(
            rc, "jeremiah", [{"hour": 9, "steps": 840}, {"hour": 13, "steps": 2100}],
            "2026-09-25", "phone",
        )
        stored = rc.hashes["geo:steps_hourly:jeremiah:phone"]
        assert stored == {"2026-09-25:09": "840", "2026-09-25:13": "2100"}

    async def test_an_hour_keeps_the_max_not_the_last_write(self, rc):
        """A re-upload must be idempotent, and a reboot (which shrinks the raw
        counter) must not erase an hour that was already credited."""
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 840}], "2026-09-25", "phone")
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 840}], "2026-09-25", "phone")
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 12}], "2026-09-25", "phone")
        assert rc.hashes["geo:steps_hourly:jeremiah:phone"]["2026-09-25:09"] == "840"
        # ...but a genuinely higher total still lands.
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 900}], "2026-09-25", "phone")
        assert rc.hashes["geo:steps_hourly:jeremiah:phone"]["2026-09-25:09"] == "900"

    async def test_sources_keep_separate_hour_buckets(self, rc):
        """A watch and a phone counting the same walk must not be summed -- the
        same rule the daily buckets already follow."""
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 800}], "2026-09-25", "phone")
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 900}], "2026-09-25", "watch")
        assert rc.hashes["geo:steps_hourly:jeremiah:phone"]["2026-09-25:09"] == "800"
        assert rc.hashes["geo:steps_hourly:jeremiah:watch"]["2026-09-25:09"] == "900"

    @pytest.mark.parametrize(
        "entry",
        [
            {"hour": 24, "steps": 500},    # not an hour
            {"hour": -1, "steps": 500},
            {"hour": 9, "steps": 0},        # nothing walked: no bucket to invent
            {"hour": 9, "steps": -3},
            {"hour": 9, "steps": 500000},   # beyond any real hour
            {"hour": "nine", "steps": 500},
            {"steps": 500},                 # no hour
            {"hour": 9},                    # no steps
            "not-an-object",
            None,
        ],
    )
    async def test_an_unusable_bucket_is_dropped_not_stored(self, rc, entry):
        await geo_main._record_hourly_steps(rc, "jeremiah", [entry], "2026-09-25", "phone")
        assert rc.hashes.get("geo:steps_hourly:jeremiah:phone", {}) == {}

    async def test_good_buckets_survive_a_bad_one_in_the_same_upload(self, rc):
        await geo_main._record_hourly_steps(
            rc, "jeremiah",
            [{"hour": 9, "steps": 500}, {"hour": 99, "steps": 500}, {"hour": 10, "steps": 300}],
            "2026-09-25", "phone",
        )
        assert rc.hashes["geo:steps_hourly:jeremiah:phone"] == {
            "2026-09-25:09": "500",
            "2026-09-25:10": "300",
        }

    async def test_a_non_list_is_refused_rather_than_coerced(self, rc):
        assert await geo_main._record_hourly_steps(rc, "jeremiah", {"9": 500}, "2026-09-25", "phone") == 0
        assert not rc.hashes.get("geo:steps_hourly:jeremiah:phone")

    async def test_string_digits_are_accepted(self, rc):
        """JSON from a phone may well carry "9"/"500"; that is not corruption."""
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": "9", "steps": "500"}], "2026-09-25", "phone")
        assert rc.hashes["geo:steps_hourly:jeremiah:phone"]["2026-09-25:09"] == "500"


class TestRead:
    async def test_an_unrecorded_day_is_empty_not_twenty_four_zeros(self, rc):
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 500}], "2026-09-25", "phone")
        assert await geo_main._get_hourly_steps(rc, "jeremiah", "2026-09-26") == []
        assert await geo_main._get_hourly_steps(rc, "nobody", "2026-09-25") == []

    async def test_buckets_come_back_in_hour_order_with_labels(self, rc):
        await geo_main._record_hourly_steps(
            rc, "jeremiah",
            [{"hour": 17, "steps": 90}, {"hour": 6, "steps": 20}, {"hour": 12, "steps": 55}],
            "2026-09-25", "phone",
        )
        assert await geo_main._get_hourly_steps(rc, "jeremiah", "2026-09-25") == [
            {"hour": 6, "label": "06:00", "steps": 20},
            {"hour": 12, "label": "12:00", "steps": 55},
            {"hour": 17, "label": "17:00", "steps": 90},
        ]

    async def test_another_day_in_the_same_hash_is_not_leaked(self, rc):
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 500}], "2026-09-25", "phone")
        await geo_main._record_hourly_steps(rc, "jeremiah", [{"hour": 9, "steps": 700}], "2026-09-26", "phone")
        got = await geo_main._get_hourly_steps(rc, "jeremiah", "2026-09-25")
        assert got == [{"hour": 9, "label": "09:00", "steps": 500}]

    async def test_a_corrupt_field_is_skipped_not_fatal(self, rc):
        rc.hashes["geo:steps_hourly:jeremiah:phone"] = {
            "2026-09-25:09": "500",
            "2026-09-25:xx": "500",
            "2026-09-25:11": "not-a-number",
        }
        assert await geo_main._get_hourly_steps(rc, "jeremiah", "2026-09-25") == [
            {"hour": 9, "label": "09:00", "steps": 500}
        ]


class TestPostStepsRoute:
    def test_hourly_buckets_are_stored_with_the_reading(self, client, fake):
        day = "2026-09-25"
        r = client.post(
            "/steps",
            headers=HEADERS,
            json={
                "user_id": "jeremiah",
                "steps": 4321,
                "timestamp": ts_for(day),
                "timezone": "America/Phoenix",
                "hourly": [{"hour": 8, "steps": 100}, {"hour": 9, "steps": 200}],
            },
        )
        assert r.status_code == 200
        assert r.json()["hours_recorded"] == 2
        assert fake.hashes["geo:steps_hourly:jeremiah:phone"] == {
            "2026-09-25:08": "100",
            "2026-09-25:09": "200",
        }
        # The daily total is still recorded exactly as before.
        assert fake.hashes["geo:steps:jeremiah"][day] == "4321"

    def test_hours_file_under_the_phones_own_day_not_the_servers(self, client, fake):
        """A phone in Tokyo at 23:00 local files its reading under *its* today.
        If hours were derived from the server's zone they would land on a
        different day than the total above them."""
        r = client.post(
            "/steps",
            headers=HEADERS,
            json={
                "user_id": "jeremiah",
                "steps": 500,
                "timestamp": datetime(2026, 9, 25, 23, 0, tzinfo=ZoneInfo("Asia/Tokyo")).timestamp(),
                "timezone": "Asia/Tokyo",
                "hourly": [{"hour": 23, "steps": 500}],
            },
        )
        assert r.status_code == 200
        assert "2026-09-25:23" in fake.hashes["geo:steps_hourly:jeremiah:phone"]
        assert fake.hashes["geo:steps:jeremiah"] == {"2026-09-25": "500"}

    def test_a_build_without_hours_still_succeeds(self, client, fake):
        """Older APKs omit the key entirely. That must not 422 -- their day
        totals still have to land, and the hour chart just stays empty."""
        r = client.post("/steps", headers=HEADERS, json={"user_id": "jeremiah", "steps": 700, "timestamp": ts_for("2026-09-25")})
        assert r.status_code == 200
        assert r.json()["hours_recorded"] == 0
        assert not fake.hashes.get("geo:steps_hourly:jeremiah:phone")
        assert fake.hashes["geo:steps:jeremiah"]["2026-09-25"] == "700"

    def test_an_empty_hourly_list_records_nothing(self, client, fake):
        r = client.post(
            "/steps",
            headers=HEADERS,
            json={"user_id": "jeremiah", "steps": 700, "timestamp": ts_for("2026-09-25"), "hourly": []},
        )
        assert r.json()["hours_recorded"] == 0
        assert not fake.hashes.get("geo:steps_hourly:jeremiah:phone")

    def test_hours_are_namespaced_per_source(self, client, fake):
        client.post(
            "/steps",
            headers=HEADERS,
            json={
                "user_id": "jeremiah",
                "steps": 800,
                "timestamp": ts_for("2026-09-25"),
                "source": "watch",
                "hourly": [{"hour": 9, "steps": 800}],
            },
        )
        assert "geo:steps_hourly:jeremiah:watch" in fake.hashes
        assert "geo:steps_hourly:jeremiah:phone" not in fake.hashes


class TestRangesRouteCarriesHours:
    def _record_today(self, fake, hours):
        day = geo_main._today_date().isoformat()
        for hour, steps in hours:
            fake.hashes.setdefault("geo:steps_hourly:jeremiah:phone", {})[f"{day}:{hour:02d}"] = str(steps)

    def test_the_day_view_charts_the_hours_it_has(self, client, fake):
        self._record_today(fake, [(7, 120), (8, 640), (13, 310)])
        r = client.get("/steps/ranges?range=D&user_id=jeremiah", headers=HEADERS)
        body = r.json()
        assert [b["hour"] for b in body["hourly"]] == [7, 8, 13]
        assert body["peak"] == {"hour": 8, "label": "08:00", "steps": 640}

    def test_the_day_view_says_nothing_rather_than_claiming_zero(self, client, fake):
        r = client.get("/steps/ranges?range=D&user_id=jeremiah", headers=HEADERS)
        body = r.json()
        # Absent, not `[]`: an empty list renders as a flat, sedentary day.
        assert "hourly" not in body
        assert "peak" not in body

    def test_longer_ranges_carry_no_hours(self, client, fake):
        """A week's hourly bars would be 168 columns; the range owns its own
        aggregation and hours belong to the day view alone."""
        self._record_today(fake, [(7, 120)])
        for rng in ("W", "M", "3M", "Y"):
            body = client.get(f"/steps/ranges?range={rng}&user_id=jeremiah", headers=HEADERS).json()
            assert "hourly" not in body, rng

    def test_hours_need_the_same_consent_check_as_the_buckets(self, client, fake, monkeypatch):
        """The hourly detail is the same private data one step finer-grained, so
        it must not become a way around `_require_may_view`."""
        async def opted_out():
            return {"users": [{"username": "jeremiah", "enabled": False, "audience": "circle", "user_ids": []}]}

        self._record_today(fake, [(7, 120)])
        monkeypatch.setattr(geo_main, "_fetch_activity_sharing", opted_out)
        r = client.get("/steps/ranges?range=D&user_id=jeremiah&viewer=michele", headers=HEADERS)
        assert r.status_code == 404