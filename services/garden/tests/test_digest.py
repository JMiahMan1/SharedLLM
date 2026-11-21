"""The morning digest: everyone's own plants plus the household's unassigned
ones, and a digest missed by a restart is still sent that day."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from services.garden import clock, notices
from services.garden.models import CareRule, GardenProfile, Plant


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    SQLModel.metadata.create_all(eng)
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    with Session(eng) as session:
        for pid, who in (("p-mine", "child_one"), ("p-theirs", "parent_one"), ("p-nobody", None)):
            session.add(Plant(id=pid, category="houseplant", species_ref="x", nickname=pid, assigned_to=who))
            session.add(CareRule(plant_id=pid, action="water", interval_days=7, next_due_at=past))
        session.add(GardenProfile(user_id="household", daily_digest_time="08:00"))
        session.commit()
    return eng


def test_a_digest_lists_my_plants_and_the_unassigned_ones(engine):
    with Session(engine) as session:
        tasks = notices.collect_due_tasks(session, "child_one", include_unassigned=True)
        only_mine = notices.collect_due_tasks(session, "child_one")

    assert sorted(t["plant_id"] for t in tasks) == ["p-mine", "p-nobody"]
    assert [t["plant_id"] for t in only_mine] == ["p-mine"]


def test_a_restart_after_the_digest_time_still_sends_it_once(engine, monkeypatch):
    sent: list[str] = []

    async def send(member, _factory, *, client=None):
        sent.append(member)
        return True

    async def family():
        return ["parent_one", "child_one"]

    monkeypatch.setattr(notices, "send_digest_to_member", send)
    after = clock.now().replace(hour=9, minute=30, second=0, microsecond=0)  # long past 08:00

    day = asyncio.run(notices.digest_tick(lambda: engine, family, None, now=after))
    again = asyncio.run(notices.digest_tick(lambda: engine, family, day, now=after + timedelta(minutes=1)))

    assert sent == ["parent_one", "child_one"]
    assert again == day == clock.today_iso(after)


def test_before_the_digest_time_nothing_is_sent(engine, monkeypatch):
    sent: list[str] = []

    async def send(member, _factory, *, client=None):
        sent.append(member)

    async def family():
        return ["parent_one"]

    monkeypatch.setattr(notices, "send_digest_to_member", send)
    early = clock.now().replace(hour=7, minute=0, second=0, microsecond=0)

    assert asyncio.run(notices.digest_tick(lambda: engine, family, None, now=early)) is None
    assert sent == []
