"""Low device battery: the family hears once per discharge.

Every location upload carries the phone's battery percentage (and, from newer
app builds, whether it is charging). The first reading below LOW_BATTERY_PCT
that is not charging sends one ``family_safety`` notice to the rest of the
family. Only a charging phone, or a reading of at least LOW_BATTERY_PCT +
REARM_MARGIN (it was charged), arms it again for the next discharge: a reading
that wobbles around the threshold, or two phones on one account reporting in
turn, must not announce again. The state rides along in the stored location
record, so no other table is needed. On top of that each notice carries a
``once`` key per person per day, so nobody hears about the same phone twice in
one day whatever the readings do.

Watches use the same threshold and re-arm rule, but their state is kept in a
``GlobalSetting`` under ``WATCH_STATE_PREFIX`` because telemetry is not tied to
the location record. The owner is not notified about their own watch; the rest
of the family is, just as they are for a phone.
"""
from __future__ import annotations

import os
from datetime import date

from services.common import notify

LOW_BATTERY_PCT = int(os.environ.get("LOW_BATTERY_PCT", "15"))
#: Percentage points above LOW_BATTERY_PCT that count as charged again.
REARM_MARGIN = 5
#: Key in the stored location record: this discharge was already announced.
SENT_FIELD = "battery_low_sent"
#: Prefix for a watch's discharge state in GlobalSetting.
WATCH_STATE_PREFIX = "watch_battery:"


def step(previous: dict | None, battery: float | None, charging: bool | None) -> tuple[bool, bool]:
    """(announce now, the flag to store) for a new reading after ``previous``."""
    prev = previous or {}
    sent = bool(prev.get(SENT_FIELD))
    if battery is None:
        return False, sent
    if charging or battery >= LOW_BATTERY_PCT + REARM_MARGIN:
        return False, False
    if battery < LOW_BATTERY_PCT and not sent:
        return True, True
    return False, sent


async def announce(person: str, display_name: str, battery: float, family: list[str],
                   today: date | None = None) -> int:
    """Tell everyone in ``family`` but ``person``; how many notices went out."""
    url = f"/wander?person={person}"
    once = f"battery:{person}:{(today or date.today()).isoformat()}"
    title = f"{display_name}'s phone is at {int(battery)}%"
    body = "It is not charging, so it may switch off soon."
    return await notify.send_many(
        [u for u in family if u != person],
        lambda user: notify.notice(
            user, "family_safety", title, body, url=url, priority=4, tag=f"battery:{person}", once=once,
            data={"person": person, "battery": int(battery)}, actions=[notify.view_action(url, "Map")]),
    )


async def announce_watch(device_key: str, label: str, battery: float, family: list[str],
                         owner: str | None, today: date | None = None) -> int:
    """Tell the family (but not the watch's owner) that a watch is low."""
    url = "/identity"
    once = f"watch_battery:{device_key}:{(today or date.today()).isoformat()}"
    title = f"{label or 'Watch'} is at {int(battery)}%"
    body = "It is not charging, so it may switch off soon."
    return await notify.send_many(
        [u for u in family if u != owner],
        lambda user: notify.notice(
            user, "family_safety", title, body, url=url, priority=4, tag=f"watch_battery:{device_key}",
            once=once, data={"device_key": device_key, "battery": int(battery)},
            actions=[notify.view_action(url, "Devices")]),
    )
