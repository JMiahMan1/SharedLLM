"""What a fast sends: a call to prayer to those it is shared with, and the
hours of prayer and its end to the one fasting.

Sharing a fast (``HealthShare`` scope "fasting") is how a person asks others
to stand with them. When the fast starts and when it ends, each of them gets a
notice with a verse to pray. While it runs, the person fasting is reminded at
the third, sixth and ninth hours (Acts 2:15; 10:9; 3:1), and told when the
fast is complete. Every notice carries a ``once`` key, so a restart or a
second replica never sends one twice.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

import aiohttp
from sqlmodel import Session, select

from services.common import notify

from . import fasting_traditions as traditions
from .models import FastingSession

log = logging.getLogger(__name__)

INTERVAL_S = 300
#: A prayer reminder goes out within this long after its hour, never later.
WINDOW = timedelta(minutes=30)
URL = "/health"


def _who(user: str) -> str:
    return user[:1].upper() + user[1:]


def bible_link(ref: str) -> str:
    """Where a tap on a verse goes: that passage in the app's Bible."""
    return f"/bible?ref={quote(ref)}"


async def _reading_version(client: aiohttp.ClientSession, username: str | None) -> str | None:
    """The translation the person reads in, so a verse comes in their own words."""
    from services import config

    if not username:
        return None
    try:
        async with client.get(
            f"{config.BIBLE_SVC_URL}/state", params={"username": username},
            headers={"X-Internal-Secret": config.INTERNAL_SECRET},
        ) as resp:
            if resp.status != 200:
                return None
            prefs = (await resp.json()).get("preferences") or {}
            return prefs.get("default_version") or None
    except Exception:
        return None


async def verse_text(ref: str, username: str | None = None) -> str:
    """``“…” (Psalm 51:17, NKJV)``: the passage's opening words from the family's
    Bible, in the person's own translation; the reference alone if it cannot be read."""
    from services import config

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0)) as client:
            version = await _reading_version(client, username)
            params = {"ref": ref, **({"version": version} if version else {})}
            async with client.get(
                f"{config.BIBLE_SVC_URL}/passages", params=params,
                headers={"X-Internal-Secret": config.INTERNAL_SECRET},
            ) as resp:
                if resp.status != 200:
                    return ref
                data = await resp.json()
    except Exception as exc:  # a missing Bible never stops the notice
        log.info("[fasting] verse %s not read: %s", ref, exc)
        return ref
    text = " ".join(str(v.get("text") or "").strip() for v in (data.get("verses") or [])[:4]).strip()
    if not text:
        return ref
    if len(text) > 300:
        text = text[:297].rsplit(" ", 1)[0] + "…"
    return f"“{text}” ({data.get('reference') or ref}, {data.get('version') or ''})".replace(", )", ")")


async def shared_started(fast: dict, owner: str, viewers: list[str]) -> int:
    """Ask those the fast is shared with to pray, with an encouraging verse."""
    if not viewers:
        return 0
    ref = traditions.pick(traditions.ENCOURAGEMENT, int(fast.get("id") or 0))
    what = fast.get("kind_name") or "a fast"
    intention = fast.get("intention")
    body = f"{_who(owner)} has begun {'the ' + what if fast.get('kind_name') else what}."
    if intention:
        body += f" Praying for: {intention}."
    body += " Will you pray with them?"
    sent = 0
    for viewer in viewers:
        verse = await verse_text(ref, viewer)
        sent += int(await notify.send(notify.notice(
            viewer, "health", f"Pray with {_who(owner)}", f"{body} {verse}", url=bible_link(ref),
            actions=[notify.view_action(bible_link(ref), f"Read {ref}"), notify.view_action(URL, "See the fast")],
            tag=f"fast:{fast.get('id')}", once=f"fast-start:{fast.get('id')}:{viewer}", kind="fast_shared",
        )))
    return sent


async def shared_ended(fast: dict, owner: str, viewers: list[str]) -> int:
    """Tell those who prayed that the fast is over, and give thanks with them."""
    if not viewers:
        return 0
    ref = "Psalm 34:8"
    sent = 0
    for viewer in viewers:
        verse = await verse_text(ref, viewer)
        sent += int(await notify.send(notify.notice(
            viewer, "health", f"{_who(owner)}'s fast is complete",
            f"{_who(owner)} has ended their fast. Give thanks with them. {verse}", url=bible_link(ref),
            actions=[notify.view_action(bible_link(ref), f"Read {ref}")],
            tag=f"fast:{fast.get('id')}", once=f"fast-end:{fast.get('id')}:{viewer}", kind="fast_shared",
        )))
    return sent


def due(rows: list[FastingSession], now: datetime, tz) -> list[tuple[FastingSession, str, dict]]:
    """(fast, what, detail) for every notice owed right now.

    ``what`` is "prayer" (an hour of prayer within ``WINDOW`` after it struck,
    inside the fast) or "complete" (the planned end has passed).
    """
    local = now.astimezone(tz)
    out: list[tuple[FastingSession, str, dict]] = []
    for row in rows:
        started = datetime.fromisoformat(row.started_at)
        ends = datetime.fromisoformat(row.ends_at) if row.ends_at else None
        if ends and now >= ends:
            out.append((row, "complete", {}))
            continue
        if not row.prayer_reminders:
            continue
        for hour in traditions.PRAYER_HOURS:
            hh, mm = (int(p) for p in hour["clock"].split(":"))
            strike = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if strike <= local < strike + WINDOW and strike > started.astimezone(tz):
                out.append((row, "prayer", {**hour, "day": local.date().isoformat()}))
    return out


async def remind_once(engine_factory, tz_factory, now: datetime | None = None) -> int:
    now = now or datetime.now().astimezone()
    tz = tz_factory()
    with Session(engine_factory()) as session:
        rows = list(session.exec(select(FastingSession).where(FastingSession.ended_at.is_(None))).all())  # type: ignore[union-attr]
    sent = 0
    for row, what, detail in due(rows, now, tz):
        if what == "complete":
            payload = notify.notice(
                row.user_id, "health", "Your fast is complete",
                "Thanks be to God. Break it gently with something small, and end the fast on the card.",
                url=URL, tag=f"fast:{row.id}", once=f"fast-complete:{row.id}", kind="fast_complete",
            )
        else:
            # A different psalm at each hour, and a different set each day.
            hour_index = [h["clock"] for h in traditions.PRAYER_HOURS].index(detail["clock"])
            psalm = traditions.pick(traditions.PSALMS, now.astimezone(tz).toordinal() * 3 + hour_index)
            # The whole call in the message (the reason for the hour, then the
            # psalm's opening words in the person's own translation), and a tap
            # opens that psalm in the Bible.
            verse = await verse_text(psalm, row.user_id)
            payload = notify.notice(
                row.user_id, "health", f"{detail['name']}: time to pray",
                f"{detail['reason']} Pray the Lord's Prayer, then read {psalm}: {verse}",
                url=bible_link(psalm),
                actions=[notify.view_action(bible_link(psalm), f"Read {psalm}"), notify.view_action(URL, "Open my fast")],
                tag=f"fast-prayer:{row.id}",
                once=f"fast-prayer:{row.id}:{detail['day']}:{detail['clock']}", kind="fast_prayer",
            )
        sent += int(await notify.send(payload))
    return sent


async def reminder_loop(engine_factory, tz_factory) -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await remind_once(engine_factory, tz_factory)
        except Exception as exc:  # the loop must survive a bad tick
            log.warning("[fasting] reminder tick failed: %s", exc)
