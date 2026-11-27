"""Nudge the rest of the family when someone plays today's Bible quiz.

After a counted daily quiz attempt, the player can prompt the household to
take their turn. Each person gets at most one quiz nudge per day, never during
quiet hours, and never with name or score unless the player shares Bible
activity with them.
"""
from __future__ import annotations

import logging
from datetime import datetime

import aiohttp
from sqlmodel import Session, select

from services.common import notify
from services.common.notice_links import link

from .models import GameScore, UserBibleState
from .reminders import _in_quiet_hours

log = logging.getLogger(__name__)


def quiz_nudge_note(player_name: str | None, correct: int, total: int) -> dict:
    """The notice content for a family quiz nudge."""
    if player_name:
        body = f"{player_name} just played today's Bible quiz ({correct}/{total}). Your turn?"
    else:
        body = "Someone in the family just played today's Bible quiz. Your turn?"
    target = link("bible", tab="play")
    # telemetry's notify takes the link inside data; a top-level "link" is refused with HTTP 422.
    return {"title": "Bible quiz", "body": body, "data": {"link": target, "url": target["href"]}}


async def _resolve(value):
    if hasattr(value, "__await__"):
        return await value
    return value


async def nudge_family(
    engine,
    player,
    correct,
    total,
    *,
    identity_url,
    telemetry_url,
    secret,
    consent,
    display_name,
    now,
):
    """Send a once-per-day quiz nudge to household members who have not played."""
    now = now or datetime.now()
    if _in_quiet_hours(now.hour):
        return 0
    if not identity_url:
        return 0

    today = now.date().isoformat()
    try:
        async with aiohttp.ClientSession() as client, client.get(
            f"{identity_url.rstrip('/')}/api/internal/household",
            headers={"X-Internal-Secret": secret},
            timeout=aiohttp.ClientTimeout(total=10.0),
        ) as resp:
            if resp.status >= 400:
                log.warning("[Bible] quiz nudge household lookup failed: HTTP %s", resp.status)
                return 0
            data = await resp.json()
    except Exception as exc:
        log.warning("[Bible] quiz nudge household lookup errored: %s", exc)
        return 0

    usernames = data.get("usernames") if isinstance(data, dict) else None
    if not isinstance(usernames, list):
        log.warning("[Bible] quiz nudge household lookup returned an unexpected shape")
        return 0

    with Session(engine) as session:
        played = {
            row
            for row in session.exec(
                select(GameScore.username).where(
                    GameScore.day == today,
                    GameScore.counted == True,  # noqa: E712
                )
            ).all()
        }
        opted_out = {
            row
            for row in session.exec(
                select(UserBibleState.username).where(UserBibleState.quiz_nudges == False)  # noqa: E712
            ).all()
        }

    recipients = [u for u in usernames if u != player and u not in played and u not in opted_out]
    if not recipients:
        return 0

    player_name = await _resolve(display_name(player))
    named_note = quiz_nudge_note(player_name, correct, total)
    anon_note = quiz_nudge_note(None, correct, total)

    sent = 0
    for recipient in recipients:
        try:
            shared = await consent(player, recipient, "bible")
            note = named_note if shared is True and player_name else anon_note
            if shared is True and not player_name:
                note = anon_note
            body = {
                "user": recipient,
                "category": "bible",
                "kind": "bible_quiz_nudge",
                "tag": "bible:quiz",
                "once": f"bible-quiz:{today}",
                **note,
            }
            if await notify.send(body, telemetry=telemetry_url, secret=secret):
                sent += 1
        except Exception as exc:
            log.warning("[Bible] quiz nudge for %s failed: %s", recipient, exc)
    return sent
