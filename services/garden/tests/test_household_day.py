"""Garden counts days in the household's time zone, and never guesses one.

Timestamps are stored in UTC, but "today" is the household's. In Denver the UTC
day ends at 17:00, so counted in UTC an evening watering landed on tomorrow and
a valve got a fresh daily allowance at dinner time.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from scripts.build_plants_seed import build_seed
from services.common import household_tz
from services.config import INTERNAL_SECRET
from services.garden import achievements, clock
from services.garden import homehub_hardware as hardware
from services.garden.main import app
from services.garden.models import CareLog, HomeHubBinding, ValveRun

USER = {"X-Internal-Secret": INTERNAL_SECRET, "X-User-Id": "parent_one", "X-User-Role": "mother"}


@pytest.fixture()
def denver(monkeypatch):
    household_tz.reset_cache()
    monkeypatch.setenv("TZ", "America/Denver")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import services.garden.main as main_module

    seed = tmp_path / "plants-seed.db"
    build_seed(seed)
    main_module.app.state.engine = None
    main_module._SEED = None
    monkeypatch.setenv("GARDEN_DATABASE_URL", f"sqlite:///{tmp_path / 'garden.db'}")
    monkeypatch.setenv("PLANT_SEED_PATH", str(seed))
    engine = main_module._engine()
    SQLModel.metadata.create_all(engine)
    main_module.app.state.engine = engine
    return TestClient(app)


def _session():
    import services.garden.main as main_module

    return Session(main_module.app.state.engine)


def test_an_evening_stamp_is_still_that_day_at_home(denver):
    # 02:30 UTC on the 21st is 19:30 on the 20th in Denver (MST, UTC-7).
    assert clock.local_date("2026-11-21T02:30:00+00:00") == date(2026, 11, 20)
    start, end = clock.day_bounds_utc(date(2026, 11, 20))
    assert (start, end) == ("2026-11-20T07:00:00+00:00", "2026-11-21T07:00:00+00:00")


def test_a_dst_day_is_23_hours_long(denver):
    start, end = clock.day_bounds_utc(date(2027, 3, 14))
    span = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    assert span == timedelta(hours=23)


def test_no_zone_is_an_error_not_utc(monkeypatch):
    household_tz.reset_cache()
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)

    with pytest.raises(household_tz.HouseholdTimeZoneError):
        clock.today()


def test_routes_say_how_to_set_the_zone(client, monkeypatch):
    household_tz.reset_cache()
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.delenv("TIMEZONE", raising=False)

    resp = client.get("/streaks", headers=USER)

    assert resp.status_code == 503
    assert "time zone" in resp.json()["detail"]


def test_an_invalid_zone_is_an_error_too(monkeypatch):
    household_tz.reset_cache()
    monkeypatch.setenv("TZ", "Mars/Olympus_Mons")

    with pytest.raises(household_tz.HouseholdTimeZoneError):
        clock.today()


def test_the_valve_cap_counts_the_household_day(client, denver):
    with _session() as db:
        binding = HomeHubBinding(target_kind="location", target_id="bed-1", valve_entity_id="valve.x")
        db.add(binding)
        db.commit()
        # 18:00 and 19:00 Denver on the 20th: both after the UTC midnight.
        for stamp in ("2026-11-21T01:00:00+00:00", "2026-11-21T02:00:00+00:00"):
            db.add(ValveRun(binding_id=binding.id, valve_entity_id="valve.x", performed_by="p",
                            minutes=1, trigger="manual", started_at=stamp, ends_at=stamp))
        # 06:00 Denver on the 20th: before the evening runs, same household day.
        db.add(ValveRun(binding_id=binding.id, valve_entity_id="valve.x", performed_by="p",
                        minutes=1, trigger="manual", started_at="2026-11-20T13:00:00+00:00",
                        ends_at="2026-11-20T13:00:00+00:00"))
        db.commit()

        evening = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)  # 20:00 Denver
        assert hardware.runs_today(db, binding, now=evening) == 3
        next_morning = datetime(2026, 11, 21, 14, 0, tzinfo=UTC)  # 07:00 Denver on the 21st
        assert hardware.runs_today(db, binding, now=next_morning) == 0


def test_a_streak_survives_the_morning_before_todays_watering(client):
    yesterday = datetime.now(UTC) - timedelta(days=1)
    with _session() as db:
        for days_back in (1, 2, 3):
            stamp = (datetime.now(UTC) - timedelta(days=days_back)).isoformat()
            db.add(CareLog(plant_id="p-1", action="water", performed_by="parent_one", source="manual",
                           created_at=stamp))
        db.commit()
    assert yesterday  # three days running, none yet today

    body = client.get("/streaks", headers=USER).json()

    assert body["current_streak"] == 3


def test_a_streak_award_stays_earned_after_a_missed_day():
    days = {date(2026, 11, d) for d in range(1, 8)} | {date(2026, 11, 20)}

    assert achievements.longest_run(days) == 7
    assert achievements.longest_run(set()) == 0


def test_achievements_count_the_persons_own_plants(client):
    with _session() as db:
        for plant in ("p-1", "p-2", "p-3", "p-4", "p-5"):
            db.add(CareLog(plant_id=plant, action="water", performed_by="someone_else", source="manual"))
        db.add(CareLog(plant_id="p-1", action="water", performed_by="parent_one", source="manual"))
        db.commit()

    earned = {row["id"] for row in client.get("/streaks", headers=USER).json()["earned"]}

    assert "first_plant" in earned
    assert "plants_5" not in earned
