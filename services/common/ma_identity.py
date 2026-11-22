"""Acting as the right Music Assistant account, and saying clearly why there is none.

Every service gets a person's Music Assistant from identity's ``/api/resolve``
(``services/identity/music_assistant.py``): ``mass_url``, ``mass_token``, plus
``mass_server`` ("own" | "household" | "absent"), ``mass_user_id`` (their account
on the household server) and ``mass_error`` (why there is none).

Music Assistant records play history, resume points and playback speed under the
account that started playback. Calls made straight to MA with the person's token
are theirs already; calls that go through HomeHub (which signs in to MA with the
household integration's own login) carry ``user`` so MA acts as the person
(MA 2.10's impersonation argument on ``player_queues/play_media`` and
``music/search``). Background work (polling players) carries nothing.
"""
from __future__ import annotations

from typing import Any

NO_MA_MESSAGE = ("No Music Assistant is set up: add the Music Assistant integration in HomeHub "
                 "or your own in Accounts & Integrations.")


def _get(creds: Any, key: str) -> Any:
    if creds is None:
        return None
    if isinstance(creds, dict):
        return creds.get(key)
    return getattr(creds, key, None)


def act_as(creds: Any) -> dict[str, str]:
    """``{"user": <MA account id>}`` for a hub call made for this person; {} when it is nobody's."""
    if _get(creds, "mass_server") == "household" and (uid := _get(creds, "mass_user_id")):
        return {"user": str(uid)}
    return {}


def unavailable_message(creds: Any) -> str:
    """Why this person has no usable Music Assistant (identity's own words when it gave them)."""
    return str(_get(creds, "mass_error") or NO_MA_MESSAGE)


def unreachable_message(creds: Any, why: str) -> str:
    """A person's Music Assistant did not answer: say whose, and what they can do."""
    url = _get(creds, "mass_url") or "its address"
    if _get(creds, "mass_server") == "own":
        return (f"Your own Music Assistant at {url} did not answer ({why}). Fix it in Accounts & Integrations, "
                "or switch to the household Music Assistant there.")
    if _get(creds, "mass_server") == "household":
        return f"The household Music Assistant at {url} is unreachable ({why})."
    return f"Music Assistant at {url} is unreachable ({why})."
