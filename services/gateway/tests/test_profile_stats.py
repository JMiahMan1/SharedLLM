"""Profile stats through the gateway (services/gateway/profile_stats.py).

A person sees all of their own; anyone else sees an area only through the
switch that area already has; a failed source greys its own card and never
the page; the admin read is admins only, decided on the server.
"""
import asyncio
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway import profile_stats as ps
from services.gateway.main import app

ROSTER = [
    {"username": "pat", "display_name": "Pat"},
    {"username": "robin", "display_name": "Robin"},
    {"username": "kit", "display_name": "Kit"},
]


def _arcade(best: int) -> dict:
    return {"player": {"best_score": best, "crowns": 1, "podiums": 2, "total_games": 3, "per_game": [
        {"slug": "family-checkers", "title": "Checkers", "my_best": best, "my_rank": 1, "my_plays": 4},
        {"slug": "never-played", "title": "Unplayed", "my_best": None},
    ]}}


def _answers() -> dict:
    """Every source's answer, by path. Values are callables of the params so a
    source can answer differently per viewer."""
    return {
        "/api/internal/name-roster": lambda p, h: ROSTER,
        "/games/leaderboard": lambda p, h: {"day": "2026-11-24", "entries": [
            {"username": "robin", "correct": 8, "total": 10, "duration_ms": 61000},
            {"username": "pat", "correct": 6, "total": 10, "duration_ms": 90000},
        ]},
        "/stats": lambda p, h: {"read_streak_current": 4, "read_streak_longest": 9, "days_read": 30,
                                "metrics": {"chapters_total": 52, "books_read": 2},
                                "position": {"reference": "John 3"}, "window": {"2026-11-23": 2, "2026-11-24": 1}},
        "/activity/summary": lambda p, h: (
            {"achievements": [{"id": "a", "name": "First Chapter"}], "points": 10} if "username" in p
            else {"user_id": p["user_id"], "steps_today": 4000, "steps_average": 6500, "workout_count": 2, "window": "week"}
        ),
        "/activity/feed": lambda p, h: (
            {"entries": [{"username": "robin", "read_streak_current": 3, "days_read": 12, "chapters_read": 20,
                          "books_read": 1, "achievements": [], "points": 5}]} if "limit" in p
            else {"users": [{"username": "robin", "window": "week", "steps_today": 9000}]}
        ),
        "/streaks": lambda p, h: (
            {"current_streak": 2, "longest_streak": 5, "total_logs": 11, "earned": [{"name": "Green Thumb"}], "points": 15}
            if "X-User-Is-Minor" not in h
            else {"habits": [{"key": "sleep_log", "title": "Log sleep", "current": 3, "best": 6, "today_done": True}]}
        ),
        "/weekly": lambda p, h: {"days_logged": 5, "averages": {"sleep_minutes": 430},
                                 "changes": [{"metric": "sleep_minutes", "direction": "down", "delta": -40}]},
        "/family": lambda p, h: {"people": [{"user_id": "robin", "scopes": ["sleep"], "sleep": {"duration_minutes": 420}}]},
        "/recipes/stats": lambda p, h: {"recipes": {
            "cookbook/dinner/chili-con-carne.md": {"made": 5, "made_by_me": 3, "mine": 5, "rating": 4.5, "last_made": "2026-11-20"},
            "cookbook/soup.md": {"made": 1, "made_by_me": 0, "mine": None, "rating": None},
        }},
        "/api/telemetry/use/pat": lambda p, h: {"use": {"has_data": True, "days": 30, "total": 12,
                                                         "top": [{"label": "Music", "count": 7}]}},
        "/api/telemetry/use/robin": lambda p, h: {"use": {"has_data": True, "days": 30, "total": 3,
                                                           "top": [{"label": "Bible", "count": 3}]}},
        "/api/internal/profile-sharing": lambda p, h: {"areas": ["recipes"] if p["username"] == "robin" else []},
        "/api/internal/activity-sharing": lambda p, h: {"users": [{"username": "pat", "enabled": True,
                                                                     "audience": "circle", "share": ["totals", "bible"]}]},
        "/shares": lambda p, h: {"shares": [{"viewer": "robin", "scope": "sleep"}]},
        "/api/players/PAT": lambda p, h: _arcade(1200),
        "/api/players/ROB": lambda p, h: _arcade(900),
        "/api/players/KIT": lambda p, h: _arcade(100),
    }


@pytest.fixture
def wired(monkeypatch):
    ps.clear_cache()
    who = {"user": "pat", "is_admin": False}
    answers = _answers()
    calls: list[tuple[str, dict, dict]] = []

    async def acting(request):
        return None if who["user"] is None else dict(who)

    async def http_get(url, *, params=None, headers=None):
        path = urlparse(url).path
        calls.append((path, dict(params or {}), dict(headers or {})))
        answer = answers.get(path)
        if answer is None:
            return None
        if isinstance(answer, Exception):
            raise answer
        result = answer(params or {}, headers or {})
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    monkeypatch.setattr(gateway_main, "_arcade_url", lambda: "http://arcade.test")
    monkeypatch.setattr(ps, "http_get", http_get)
    monkeypatch.setattr(ps, "SOURCE_TIMEOUT_S", 0.5)

    async def no_outcomes(subject):
        return None

    monkeypatch.setattr(ps, "voice_outcomes", no_outcomes)
    return {"client": TestClient(app, headers={"Authorization": "Bearer key"}), "who": who,
            "answers": answers, "calls": calls}


def _cards(body: dict) -> dict:
    return {c["area"]: c for c in body["cards"]}


def test_a_person_sees_every_area_of_their_own(wired):
    r = wired["client"].get("/api/profile/stats/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["own"] is True and body["person"]["display_name"] == "Pat"
    cards = _cards(body)
    assert [c["area"] for c in body["cards"]] == list(ps.PROFILE_AREAS)
    assert all(c["state"] != "hidden" for c in cards.values())
    assert cards["bible"]["data"]["streak_current"] == 4
    assert cards["bible"]["data"]["reference"] == "John 3"
    assert cards["bible"]["data"]["quiz_today"]["correct"] == 6
    assert cards["health"]["data"]["habits"][0]["current"] == 3
    assert cards["garden"]["data"]["achievements"] == ["Green Thumb"]
    assert cards["recipes"]["data"]["most_made"][0]["title"] == "Chili Con Carne"
    assert cards["games"]["data"]["games"][0]["slug"] == "family-checkers"
    assert all(g["slug"] != "never-played" for g in cards["games"]["data"]["games"])
    assert cards["jarvis_use"]["data"]["top"][0]["label"] == "Music"
    # The page also says what the person shares, including the switches that already exist.
    assert body["sharing"]["activity_sharing"]["share"] == ["totals", "bible"]
    assert body["sharing"]["health_shares"] == [{"viewer": "robin", "scope": "sleep"}]


def test_someone_else_sees_only_what_they_share_through_the_existing_switches(wired):
    r = wired["client"].get("/api/profile/stats/robin")
    assert r.status_code == 200, r.text
    cards = _cards(r.json())
    assert r.json()["own"] is False and "sharing" not in r.json()
    assert cards["bible"]["state"] == "ok" and cards["bible"]["data"]["streak_current"] == 3
    assert cards["activity"]["data"]["steps_today"] == 9000
    assert cards["health"]["data"]["scopes"] == ["sleep"]
    assert cards["recipes"]["state"] == "ok"  # robin shares recipes
    assert cards["garden"]["state"] == "hidden" and cards["garden"]["data"] is None
    assert cards["jarvis_use"]["state"] == "hidden"
    # Game scores are on the family scoreboard already, so they show; the quiz follows its board.
    assert cards["games"]["state"] == "ok"
    assert cards["games"]["data"]["bible_quiz_today"]["correct"] == 8
    # The other person's unshared garden and Jarvis use are never even asked for.
    asked = [c[0] for c in wired["calls"]]
    assert "/api/telemetry/use/robin" not in asked
    assert not any(c[0] == "/streaks" and c[2].get("X-User-Id") == "robin" for c in wired["calls"])


def test_an_area_not_shared_has_no_data_at_all(wired):
    cards = _cards(wired["client"].get("/api/profile/stats/kit").json())
    for area in ("bible", "activity", "health", "garden", "recipes", "jarvis_use"):
        assert cards[area]["state"] == "hidden", area
        assert cards[area]["data"] is None


def test_a_failing_source_greys_only_its_own_card(wired):
    wired["answers"]["/stats"] = lambda p, h: RuntimeError("bible down")
    wired["answers"]["/weekly"] = lambda p, h: TimeoutError()
    r = wired["client"].get("/api/profile/stats/me")
    assert r.status_code == 200
    cards = _cards(r.json())
    assert cards["bible"]["state"] == "unavailable" and "not answering" in cards["bible"]["reason"]
    assert cards["health"]["state"] == "unavailable"
    assert cards["garden"]["state"] == "ok"


def test_a_slow_source_is_cut_off_at_its_limit(wired, monkeypatch):
    real = ps.http_get

    async def slow_garden(url, *, params=None, headers=None):
        if urlparse(url).path == "/streaks" and "X-User-Is-Minor" not in (headers or {}):
            await asyncio.sleep(3)
        return await real(url, params=params, headers=headers)

    monkeypatch.setattr(ps, "http_get", slow_garden)
    cards = _cards(wired["client"].get("/api/profile/stats/me").json())
    assert cards["garden"]["state"] == "unavailable" and "too long" in cards["garden"]["reason"]
    assert cards["bible"]["state"] == "ok"


def test_the_arcade_being_off_keeps_the_bible_quiz(wired, monkeypatch):
    def off():
        raise RuntimeError("ALPACA_ARCADE_URL is not set")

    monkeypatch.setattr(gateway_main, "_arcade_url", off)
    games = _cards(wired["client"].get("/api/profile/stats/me").json())["games"]
    assert games["state"] == "ok"
    assert games["data"]["arcade_unavailable"] is True
    assert games["data"]["bible_quiz_today"]["correct"] == 6


def test_unknown_people_and_bad_names(wired):
    assert wired["client"].get("/api/profile/stats/nobody").status_code == 404
    assert wired["client"].get("/api/profile/stats/Bad Name!").status_code == 422


def test_signed_out_is_refused(wired):
    wired["who"]["user"] = None
    assert wired["client"].get("/api/profile/stats/me").status_code == 401
    assert wired["client"].get("/api/profile/admin/pat").status_code == 401


def test_the_admin_page_is_admins_only_on_the_server(wired):
    r = wired["client"].get("/api/profile/admin/robin")
    assert r.status_code == 403
    assert not any(c[0].startswith("/api/internal/people") for c in wired["calls"])


def test_the_admin_sees_everything_without_the_opt_in(wired):
    wired["who"].update(user="pat", is_admin=True)
    wired["answers"].update({
        "/api/internal/people/kit/usage": lambda p, h: {
            "devices": [{"label": "Kit's phone", "app_version": "2.0.0"}], "features": [{"feature": "bible", "uses": 4}],
            "capability_gaps": [{"capability": "set_oven", "resolved": False, "occurrences": 2}],
            "conversations": {"total": 9, "per_day": [], "features": []}, "events": {}, "app_opens_per_day": []},
        "/api/telemetry/device_health/kit": lambda p, h: {"devices": [{"device_name": "Phone", "problems": [{"key": "usage"}]}],
                                                          "staleness": {}},
        "/api/telemetry/activity/kit/counts": lambda p, h: {"counts": {"media": 4}},
        "/api/telemetry/usage/kit": lambda p, h: {"days": [{"day": f"2026-11-{d:02d}", "millis": (60 if d < 18 else 180) * 60000}
                                                           for d in range(11, 25)]},
        "/api/telemetry/recap/kit": lambda p, h: {"recap": {"week": "2026-11-23"}},
        "/api/telemetry/concerns/kit": lambda p, h: {"concerns": [{"level": "high"}, {"level": "low"}]},
        "/api/logs": lambda p, h: [{"level": "ERROR", "service": "geo", "message": "boom", "user_id": "kit"}],
        "/stats": lambda p, h: {"read_streak_current": 0, "read_streak_longest": 12, "days_read": 40, "metrics": {},
                                "window": {}},
    })
    r = wired["client"].get("/api/profile/admin/kit")
    assert r.status_code == 200, r.text
    body = r.json()
    cards = _cards(body["profile"])
    # No opt-in: kit shares nothing with the family, but the admin sees each area.
    assert all(c["state"] != "hidden" for c in cards.values())
    s = body["sections"]
    assert s["usage"]["data"]["devices"][0]["app_version"] == "2.0.0"
    assert s["collector"]["data"]["problem_count"] == 1
    assert s["screen"]["data"]["days"][-1]["minutes"] == 180
    assert s["guardian"]["data"]["concerns"] == 2
    assert s["logs"]["data"]["by_level"] == {"ERROR": 1}
    texts = " ".join(f["text"] for f in body["flags"])
    assert "streak has stopped" in texts
    assert "Screen time is up" in texts
    assert "cannot do yet" in texts
    assert "Sleep minutes is down" in texts


def test_an_admin_section_that_fails_says_so_and_the_rest_stays(wired):
    wired["who"].update(user="pat", is_admin=True)
    wired["answers"]["/api/internal/people/kit/usage"] = lambda p, h: RuntimeError("identity down")
    body = wired["client"].get("/api/profile/admin/kit").json()
    assert body["sections"]["usage"]["state"] == "unavailable"
    assert body["profile"]["cards"]


def test_answers_are_cached_per_viewer_and_fresh_asks_again(wired):
    wired["client"].get("/api/profile/stats/me")
    first = len(wired["calls"])
    wired["client"].get("/api/profile/stats/me")
    assert len(wired["calls"]) == first
    wired["client"].get("/api/profile/stats/me", params={"fresh": 1})
    assert len(wired["calls"]) > first


def test_nothing_on_these_pages_reads_the_private_chat():
    import inspect

    source = inspect.getsource(ps)
    assert "private_chat" not in source
    assert "PRIVATE_CHAT" not in source


def test_the_admin_page_shows_request_outcomes_and_flags_a_high_failure_rate(wired, monkeypatch):
    wired["who"].update(user="pat", is_admin=True)

    async def outcomes(subject):
        assert subject == "kit"
        return {"days": 30, "total": 40, "outcomes": {"ok": 30, "timeout": 6, "tool_failure": 4},
                "latency": {"buckets": {}, "p50_ms": 2000, "p90_ms": 8000}, "per_day": [],
                "tools": [{"tool": "set_oven", "failures": 4, "calls": 5}], "slow_hours": []}

    monkeypatch.setattr(ps, "voice_outcomes", outcomes)
    body = wired["client"].get("/api/profile/admin/kit").json()
    assert body["sections"]["voice"]["data"]["total"] == 40
    assert any("25% of requests to Jarvis did not go through (most often set_oven)" in f["text"] for f in body["flags"])


def test_nothing_recorded_yet_is_an_empty_voice_section(wired, monkeypatch):
    wired["who"].update(user="pat", is_admin=True)

    async def none(subject):
        return None

    monkeypatch.setattr(ps, "voice_outcomes", none)
    body = wired["client"].get("/api/profile/admin/kit").json()
    assert body["sections"]["voice"] == {"state": "ok", "data": None}
    assert not any("requests to Jarvis" in f["text"] for f in body["flags"])
