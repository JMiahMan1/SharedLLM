"""Spotify linking (Phase 11, "music everywhere"): a member links their own
Spotify account, and what they play in the Spotify app lands on their
activity feed like any other media session.

Opt-in and per member: nothing is read until the member links, and
unlinking stops it. Linking needs the household's own Spotify app
registration (developer.spotify.com): the settings ``spotify_client_id``,
``spotify_client_secret`` and ``spotify_redirect_uri`` (the public address of
``/api/spotify/callback``, registered with that app). Without all three the
feature says "not configured" and nothing is attempted.

Pure: building URLs and requests, the signed OAuth state, and turning the
recently-played answer into activity events. The calls are made by the
gateway (linking) and telemetry (the sync).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
SCOPES = "user-read-recently-played"
SETTING_KEYS = ("spotify_client_id", "spotify_client_secret", "spotify_redirect_uri")
STATE_MAX_AGE_S = 600


def missing_settings(settings: dict[str, str]) -> list[str]:
    """The settings still empty; linking is possible only when this is empty."""
    return [k for k in SETTING_KEYS if not str(settings.get(k) or "").strip()]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(secret: str, username: str, now: float | None = None, nonce: str = "") -> str:
    """The OAuth ``state``: who is linking and when, signed with the internal secret.

    Spotify sends the browser back without our login, so the state is how the
    callback knows whose account it is; the signature stops anyone forging it.
    """
    body = _b64(json.dumps({"u": username, "t": int(now if now is not None else time.time()), "n": nonce}).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_state(secret: str, state: str, now: float | None = None) -> str:
    """The username in a valid, fresh state; ValueError otherwise."""
    if not secret or not state or "." not in state:
        raise ValueError("bad state")
    body, sig = state.rsplit(".", 1)
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig.encode(), expected.encode()):
        raise ValueError("bad state signature")
    data = json.loads(_unb64(body))
    age = (now if now is not None else time.time()) - int(data.get("t") or 0)
    if age < 0 or age > STATE_MAX_AGE_S:
        raise ValueError("the link request expired; start again")
    username = str(data.get("u") or "")
    if not username:
        raise ValueError("bad state")
    return username


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    })


def basic_auth(client_id: str, client_secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


def code_form(code: str, redirect_uri: str) -> dict[str, str]:
    return {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}


def refresh_form(refresh_token: str) -> dict[str, str]:
    return {"grant_type": "refresh_token", "refresh_token": refresh_token}


def parse_token(data: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Spotify's token answer as {access_token, refresh_token|None, expires_at (epoch s), scope}."""
    if not isinstance(data, dict) or not data.get("access_token"):
        raise ValueError(str((data or {}).get("error_description") or (data or {}).get("error") or "no access token"))
    start = now if now is not None else time.time()
    return {
        "access_token": str(data["access_token"]),
        "refresh_token": str(data["refresh_token"]) if data.get("refresh_token") else None,
        "expires_at": int(start + int(data.get("expires_in") or 3600) - 60),
        "scope": str(data.get("scope") or ""),
    }


def _played_ms(played_at: str) -> int:
    return int(datetime.fromisoformat(played_at.replace("Z", "+00:00")).timestamp() * 1000)


def recently_played_events(payload: dict[str, Any], after_ms: int) -> tuple[list[dict[str, Any]], int]:
    """Activity events for tracks played after ``after_ms``, oldest first, and the new cursor.

    Each lands like a phone media session (``feature: media``) so the profile,
    the recap, music suggestions and content monitoring read it the same way.
    """
    events: list[tuple[int, dict[str, Any]]] = []
    for entry in (payload or {}).get("items") or []:
        track = (entry or {}).get("track") or {}
        played_at = str((entry or {}).get("played_at") or "")
        if not track.get("name") or not played_at:
            continue
        try:
            at_ms = _played_ms(played_at)
        except ValueError:
            continue
        if at_ms <= after_ms:
            continue
        artists = ", ".join(a.get("name") for a in track.get("artists") or [] if a.get("name"))
        events.append((at_ms, {
            "feature": "media",
            "item": track["name"],
            "action": "play",
            "at": played_at,
            "duration_s": int((track.get("duration_ms") or 0) / 1000) or None,
            "detail": {
                "app": "Spotify",
                "artist": artists,
                "album": ((track.get("album") or {}).get("name")) or "",
                "kind": "music",
                "source": "spotify",
                "explicit": bool(track.get("explicit")),
                "spotify_id": track.get("id") or "",
            },
        }))
    events.sort(key=lambda e: e[0])
    cursor = max([after_ms] + [at for at, _ in events])
    return [e for _, e in events], cursor
