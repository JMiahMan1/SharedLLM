"""Low phone battery: the family hears once per discharge.

Every location upload carries the phone's battery percentage (and, from newer
app builds, whether it is charging). The first reading below LOW_BATTERY_PCT
that is not charging sends one ``family_safety`` notice to the rest of the
family; a charging phone, or a reading higher than the last one (it was
plugged in), arms it again for the next discharge. The state rides along in
the stored location record, so no other table is needed.
"""
from __future__ import annotations

import os

from services.common import notify

LOW_BATTERY_PCT = int(os.environ.get("LOW_BATTERY_PCT", "15"))
#: Key in the stored location record: this discharge was already announced.
SENT_FIELD = "battery_low_sent"


def step(previous: dict | None, battery: float | None, charging: bool | None) -> tuple[bool, bool]:
    """(announce now, the flag to store) for a new reading after ``previous``."""
    prev = previous or {}
    sent = bool(prev.get(SENT_FIELD))
    if battery is None:
        return False, sent
    last = prev.get("battery")
    if charging or (isinstance(last, (int, float)) and battery > last):
        return False, False
    if battery < LOW_BATTERY_PCT and not sent:
        return True, True
    return False, sent


async def announce(person: str, display_name: str, battery: float, family: list[str]) -> int:
    """Tell everyone in ``family`` but ``person``; how many notices went out."""
    url = f"/wander?person={person}"
    title = f"{display_name}'s phone is at {int(battery)}%"
    body = "It is not charging, so it may switch off soon."
    return await notify.send_many(
        [u for u in family if u != person],
        lambda user: notify.notice(
            user, "family_safety", title, body, url=url, priority=4, tag=f"battery:{person}",
            data={"person": person, "battery": int(battery)}, actions=[notify.view_action(url, "Map")]),
    )
