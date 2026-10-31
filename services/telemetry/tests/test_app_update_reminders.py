"""Missed-update reminders: only people whose phone is actually behind hear."""
import json
from datetime import datetime, timedelta

import pytest
from fakes import FakeRedis

from services.telemetry import app_updates

NOW = 1_800_000_000.0


def _seen(days_ago: float) -> str:
    return (datetime.fromtimestamp(NOW) - timedelta(days=days_ago)).isoformat()


DEVICES = {
    "dad": [{"device_key": "d1", "kind": "phone", "label": "Pixel", "app_build": "509", "last_seen_at": _seen(0.1)}],
    "mom": [{"device_key": "m1", "kind": "phone", "label": "Galaxy", "app_build": "510", "last_seen_at": _seen(0.1)}],
    "kid": [
        {"device_key": "k1", "kind": "tablet", "label": "Tab", "app_build": "447", "last_seen_at": _seen(1)},
        {"device_key": "k2", "kind": "phone", "label": "Old", "app_build": "300", "last_seen_at": _seen(90)},
    ],
}
CHILDREN = {"kid": {"username": "kid", "display_name": "Kid", "parents": ["dad", "mom"]}}


@pytest.fixture
def world(monkeypatch):
    async def family(**_):
        return list(DEVICES)

    async def identity_get(_session, path, params=None):
        return {"details": DEVICES[path.rsplit("/", 1)[1]]}

    monkeypatch.setattr(app_updates.notify, "family", family)
    monkeypatch.setattr(app_updates.notices, "_identity_get", identity_get)
    sent = []

    async def deliver(body):
        sent.append(body)

    return FakeRedis(), deliver, sent


async def _publish(rc, code=510, ago=4 * 3600):
    await rc.set(app_updates.LATEST_APK_KEY, json.dumps(
        {"version_code": code, "version_name": "1.6.0", "notes": "", "published_at": NOW - ago}))


async def test_only_people_with_a_device_behind_hear_and_a_childs_go_to_the_parents(world):
    rc, deliver, sent = world
    await _publish(rc)
    assert await app_updates.remind_missed_updates(rc, None, deliver, CHILDREN, NOW) == 3
    by = {(n["user"], n["title"]) for n in sent}
    # Dad's own phone is behind; mom's is current; the kid's tablet goes to both parents.
    assert by == {("dad", "Your Jarvis app needs updating"),
                  ("dad", "Kid's Jarvis app needs updating"), ("mom", "Kid's Jarvis app needs updating")}
    kid_notice = next(n for n in sent if n["title"].startswith("Kid"))
    assert "Tab (build 447)" in kid_notice["body"] and "Old" not in kid_notice["body"]  # 90 days unseen: not counted
    assert all(n["audience"] == "parents" and n["category"] == "app_updates" for n in sent)


async def test_a_fresh_build_waits_and_a_reminder_is_once_a_day(world):
    rc, deliver, sent = world
    await _publish(rc, ago=600)  # ten minutes old: more pushes may follow
    assert await app_updates.remind_missed_updates(rc, None, deliver, CHILDREN, NOW) == 0
    await _publish(rc)
    await app_updates.remind_missed_updates(rc, None, deliver, CHILDREN, NOW)
    first = len(sent)
    # A newer build the same day: dad and the kid were reminded today, so they
    # wait; only mom, whose phone is behind for the first time, hears.
    await _publish(rc, code=511)
    assert await app_updates.remind_missed_updates(rc, None, deliver, CHILDREN, NOW) == 1
    assert [(n["user"], n["title"]) for n in sent[first:]] == [("mom", "Your Jarvis app needs updating")]


async def test_nothing_published_means_nothing_sent(world):
    rc, deliver, sent = world
    assert await app_updates.remind_missed_updates(rc, None, deliver, CHILDREN, NOW) == 0
    assert sent == []
