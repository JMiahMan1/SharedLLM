"""Spotify linking: the signed state, the authorize URL, tokens and recently played."""
from urllib.parse import parse_qs, urlsplit

import pytest

from services.common import spotify

SECRET = "s3cret"


def test_not_configured_until_all_three_settings_are_set():
    assert spotify.missing_settings({}) == list(spotify.SETTING_KEYS)
    assert spotify.missing_settings({"spotify_client_id": "id", "spotify_client_secret": " ", "spotify_redirect_uri": "https://x/cb"}) == ["spotify_client_secret"]
    assert spotify.missing_settings({k: "v" for k in spotify.SETTING_KEYS}) == []


def test_the_state_names_the_member_and_cannot_be_forged_or_replayed_late():
    state = spotify.sign_state(SECRET, "emma", now=1000)
    assert spotify.verify_state(SECRET, state, now=1100) == "emma"
    with pytest.raises(ValueError):
        spotify.verify_state(SECRET, state, now=1000 + spotify.STATE_MAX_AGE_S + 1)
    with pytest.raises(ValueError):
        spotify.verify_state("other", state, now=1100)
    forged = spotify.sign_state("other", "dad", now=1000)
    with pytest.raises(ValueError):
        spotify.verify_state(SECRET, forged, now=1100)
    with pytest.raises(ValueError):
        spotify.verify_state(SECRET, "garbage", now=1100)


def test_the_authorize_url_asks_only_for_recently_played():
    q = parse_qs(urlsplit(spotify.authorize_url("cid", "https://jarvis/api/spotify/callback", "st")).query)
    assert q["client_id"] == ["cid"] and q["scope"] == ["user-read-recently-played"]
    assert q["redirect_uri"] == ["https://jarvis/api/spotify/callback"] and q["state"] == ["st"]


def test_a_token_answer_is_read_and_an_error_is_refused():
    t = spotify.parse_token({"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": "s"}, now=100)
    assert t == {"access_token": "a", "refresh_token": "r", "expires_at": 3640, "scope": "s"}
    assert spotify.parse_token({"access_token": "a"}, now=0)["refresh_token"] is None
    with pytest.raises(ValueError, match="invalid_grant"):
        spotify.parse_token({"error": "invalid_grant"})


PAYLOAD = {"items": [
    {"played_at": "2026-10-24T10:05:00.000Z", "track": {"id": "t2", "name": "Blessings", "duration_ms": 200000, "explicit": False,
                                                          "artists": [{"name": "Lecrae"}, {"name": "Ty Dolla $ign"}], "album": {"name": "All Things Work Together"}}},
    {"played_at": "2026-10-24T10:00:00.000Z", "track": {"id": "t1", "name": "Old", "artists": [{"name": "Someone"}], "album": {}}},
    {"played_at": "2026-10-24T10:06:00.000Z", "track": {}},
]}


def test_recently_played_becomes_media_events_after_the_cursor():
    cursor = 1761300000000  # 2025, before both plays
    events, new_cursor = spotify.recently_played_events(PAYLOAD, cursor)
    assert [e["item"] for e in events] == ["Old", "Blessings"]  # oldest first
    blessing = events[1]
    assert blessing["feature"] == "media" and blessing["action"] == "play" and blessing["at"] == "2026-10-24T10:05:00.000Z"
    assert blessing["detail"]["artist"] == "Lecrae, Ty Dolla $ign" and blessing["detail"]["source"] == "spotify"
    assert blessing["duration_s"] == 200
    # The second pass picks up nothing already copied.
    again, same = spotify.recently_played_events(PAYLOAD, new_cursor)
    assert again == [] and same == new_cursor
