import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

# Mock dependencies before importing main (same preamble as test_calendar_proxy).
sys.modules["fastembed"] = MagicMock()
sys.modules["intent_engine"] = MagicMock()
sys.modules["background_worker"] = MagicMock()


def _game(slug, title="Game", average=0.0, votes=0, benchmark=50.0, plays=0):
    return {
        "slug": slug,
        "title": title,
        "category": "arcade",
        "plays": plays,
        "benchmark_score": benchmark,
        "rating": {"count": votes, "average": average},
    }


def test_rank_prefers_admin_picks_in_order():
    from services.gateway import main

    games = [_game("a", "A"), _game("b", "B"), _game("c", "C")]
    ranked = main._rank_arcade_games(games, ["c", "a"])

    assert [g["slug"] for g in ranked["featured"]] == ["c", "a"]
    assert ranked["featured_source"] == "admin"
    assert all(g["featured"] for g in ranked["featured"])
    assert len(ranked["games"]) == 3


def test_rank_falls_back_to_rating_with_min_votes_then_benchmark():
    from services.gateway import main

    voted = [_game("voted", "Voted", average=4.5, votes=2), _game("quiet", "Quiet", average=5.0, votes=0, benchmark=99.0)]
    ranked = main._rank_arcade_games(voted, [])
    assert ranked["featured_source"] == "rating"
    assert [g["slug"] for g in ranked["featured"]] == ["voted"]

    unvoted = [_game("low", "Low", benchmark=10.0), _game("high", "High", benchmark=90.0)]
    ranked = main._rank_arcade_games(unvoted, [])
    assert ranked["featured_source"] == "benchmark"
    assert [g["slug"] for g in ranked["featured"]] == ["high", "low"]


def test_rank_ignores_unknown_admin_slugs():
    from services.gateway import main

    ranked = main._rank_arcade_games([_game("a")], ["missing"])
    assert ranked["featured_source"] == "benchmark"
    assert [g["slug"] for g in ranked["featured"]] == ["a"]


@pytest.mark.asyncio
async def test_arcade_games_returns_shelf(monkeypatch):
    from services.gateway import main

    async def fake_fetch(sort=None):
        return [_game("invaders", "Invaders", average=5.0, votes=1)]

    async def no_curation():
        return []

    monkeypatch.setattr(main, "_fetch_arcade_games", fake_fetch)
    monkeypatch.setattr(main, "_get_arcade_featured_slugs", no_curation)
    monkeypatch.setattr(main, "_family_published_slugs", AsyncMock(return_value={"invaders"}))

    payload = await main.proxy_arcade_games()

    assert payload["success"] is True
    assert payload["arcade_available"] is True
    assert payload["count"] == 1
    assert payload["play_base"] == main.ALPACA_ARCADE_PUBLIC_URL
    assert payload["featured"][0]["slug"] == "invaders"
    assert payload["games"][0]["slug"] == "invaders"


@pytest.mark.asyncio
async def test_arcade_games_reports_offline(monkeypatch):
    from services.gateway import main

    async def boom(sort=None):
        raise ValueError("connect timeout")

    monkeypatch.setattr(main, "_fetch_arcade_games", boom)

    payload = await main.proxy_arcade_games()

    assert payload["success"] is False
    assert payload["arcade_available"] is False
    assert "connect timeout" in payload["error"]
    assert payload["games"] == []
    assert payload["count"] == 0


@pytest.mark.asyncio
async def test_arcade_rate_uses_the_signed_in_username_initials(monkeypatch):
    from services.gateway import main

    seen = {}

    class _Resp:
        status = 200

        async def json(self):
            return {"success": True, "rating": {"count": 4, "average": 4.5}}

        async def text(self):
            return ""

    class _Client:
        async def post(self, url, **kwargs):
            seen["url"] = url
            seen["json"] = kwargs.get("json")
            return _Resp()

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(main, "shared_http_client", fake_client)
    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "jeremiah"}),
    )
    request = MagicMock()
    request.json = AsyncMock(return_value={"stars": 4})

    response = await main.proxy_arcade_rate("space-invaders", request)

    assert seen["url"] == f"{main.ALPACA_ARCADE_URL}/api/games/space-invaders/rate"
    assert seen["json"] == {"stars": 4, "initials": "JER"}
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_arcade_rate_rejects_an_out_of_range_vote(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "jeremiah"}),
    )
    request = MagicMock()
    request.json = AsyncMock(return_value={"stars": 6})

    with pytest.raises(HTTPException) as exc:
        await main.proxy_arcade_rate("space-invaders", request)

    assert exc.value.status_code == 422


def test_arcade_callsign_is_the_first_three_letters_of_the_account():
    from services.gateway import main

    assert main._arcade_initials("jeremiah") == "JER"
    assert main._arcade_initials("Michele") == "MIC"
    assert main._arcade_initials("kate.summers") == "KAT"
    assert main._arcade_initials("") == "YOU"


@pytest.mark.asyncio
async def test_arcade_scoreboard_reads_each_family_member(monkeypatch):
    from services.gateway import main

    class _Resp:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        async def json(self):
            return self._payload

    class _Client:
        async def get(self, url, **kwargs):
            if url.endswith("/api/users/family-directory"):
                return _Resp(200, [
                    {"username": "jeremiah", "display_name": "Jeremiah"},
                    {"username": "michele", "display_name": "Michele"},
                ])
            if url.endswith("/api/players/JER"):
                return _Resp(200, {"player": {"best_score": 62960, "games_scored": 9, "crowns": 9, "podiums": 9, "submits": 15, "total_games": 20}})
            return _Resp(404, {})

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(main, "shared_http_client", fake_client)
    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "jeremiah"}),
    )
    request = MagicMock()
    request.headers = {"Authorization": "Bearer test-key"}

    payload = await main.proxy_arcade_scoreboard(request)

    assert payload["arcade_available"] is True
    assert [p["username"] for p in payload["players"]] == ["jeremiah", "michele"]
    assert payload["players"][0]["initials"] == "JER"
    assert payload["players"][0]["best_score"] == 62960
    assert payload["players"][0]["you"] is True
    assert payload["players"][1]["best_score"] == 0
    assert payload["players"][1]["you"] is False


@pytest.mark.asyncio
async def test_arcade_scoreboard_carries_the_games_each_member_played(monkeypatch):
    from services.gateway import main

    class _Resp:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        async def json(self):
            return self._payload

    class _Client:
        async def get(self, url, **kwargs):
            if url.endswith("/api/users/family-directory"):
                return _Resp(200, [{"username": "jeremiah", "display_name": "Jeremiah"}])
            if url.endswith("/api/players/JER"):
                return _Resp(200, {"player": {
                    "best_score": 4200, "games_scored": 1, "crowns": 1, "podiums": 1, "submits": 2, "total_games": 7,
                    "per_game": [
                        {"slug": "family-telly-swing", "title": "Telly Swing", "my_best": 4200, "my_rank": 1, "my_plays": 2},
                        {"slug": "family-petal-swap", "title": "Petal Swap", "my_best": None, "my_rank": None, "my_plays": 0},
                    ],
                }})
            return _Resp(404, {})

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(main, "shared_http_client", fake_client)
    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "jeremiah"}),
    )
    request = MagicMock()
    request.headers = {}
    request.query_params = {"collection": "family"}

    payload = await main.proxy_arcade_scoreboard(request)

    me = payload["players"][0]
    # The family board's player card opens the family collection, not the arcade.
    assert me["player_url"].endswith("/player/JER?collection=family")
    # Only games actually played are carried, so the payload stays lean.
    assert [g["slug"] for g in me["per_game"]] == ["family-telly-swing"]
    assert me["per_game"][0]["my_best"] == 4200
    assert me["per_game"][0]["my_rank"] == 1


@pytest.mark.asyncio
async def test_arcade_score_is_saved_under_the_signed_in_member(monkeypatch):
    from services.gateway import main

    seen = {}

    class _Resp:
        status = 200

        async def json(self):
            return {"success": True, "score": 1234}

        async def text(self):
            return ""

    class _Client:
        async def post(self, url, **kwargs):
            seen["url"] = url
            seen["json"] = kwargs.get("json")
            return _Resp()

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(main, "shared_http_client", fake_client)
    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "michele"}),
    )
    request = MagicMock()
    request.json = AsyncMock(return_value={"score": 1234, "capture_id": "capture-1"})

    response = await main.proxy_arcade_score("space-invaders", request)

    assert seen["url"] == f"{main.ALPACA_ARCADE_URL}/api/games/space-invaders/scores"
    assert seen["json"] == {
        "initials": "MIC",
        "score": 1234,
        "player_id": "michele",
        "capture_id": "capture-1",
    }
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_arcade_score_requires_a_captured_score(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "michele"}),
    )
    request = MagicMock()
    request.json = AsyncMock(return_value={"score": 1234})

    with pytest.raises(main.HTTPException) as exc:
        await main.proxy_arcade_score("space-invaders", request)
    assert exc.value.status_code == 422
    assert "captured" in exc.value.detail


@pytest.mark.asyncio
async def test_arcade_games_forwards_the_requested_sort(monkeypatch):
    from services.gateway import main

    seen = {}

    async def fake_fetch(sort=None):
        seen["sort"] = sort
        return [_game("b", "B", average=0, votes=0), _game("a", "A", average=0, votes=0)]

    async def no_curation():
        return []

    monkeypatch.setattr(main, "_fetch_arcade_games", fake_fetch)
    monkeypatch.setattr(main, "_get_arcade_featured_slugs", no_curation)
    monkeypatch.setattr(main, "_family_published_slugs", AsyncMock(return_value={"a", "b"}))
    request = MagicMock()
    request.query_params = {"sort": "plays"}

    payload = await main.proxy_arcade_games(request)

    assert seen["sort"] == "plays"
    assert [g["slug"] for g in payload["games"]] == ["b", "a"]


@pytest.mark.asyncio
async def test_arcade_highscores_marks_the_signed_in_player(monkeypatch):
    from services.gateway import main

    class _Resp:
        status = 200

        async def json(self):
            return {
                "success": True,
                "players": [
                    {"player_id": "jeremiah", "initials": "JER", "best_score": 62960},
                    {"player_id": "michele", "initials": "MIC", "best_score": 0},
                ],
                "games": [{"slug": "space-invaders", "title": "Space Invaders", "scores": []}],
            }

    class _Client:
        async def get(self, url, **kwargs):
            assert url.endswith("/api/highscores")
            return _Resp()

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    monkeypatch.setattr(main, "shared_http_client", fake_client)
    monkeypatch.setattr(
        main,
        "_require_identity_from_request",
        AsyncMock(return_value={"user": "jeremiah"}),
    )

    payload = await main.proxy_arcade_highscores(MagicMock())

    assert payload["arcade_available"] is True
    assert payload["players"][0]["you"] is True
    assert payload["players"][1]["you"] is False
    assert payload["games"][0]["slug"] == "space-invaders"


@pytest.mark.asyncio
async def test_benchmark_games_show_only_once_published_to_the_family(monkeypatch):
    from services.gateway import main

    async def fake_fetch(sort=None):
        return [_game("published", "P", average=4, votes=2), _game("benchmark-only", "B", average=5, votes=9)]

    monkeypatch.setattr(main, "_fetch_arcade_games", fake_fetch)
    monkeypatch.setattr(main, "_get_arcade_featured_slugs", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "_family_published_slugs", AsyncMock(return_value={"published"}))

    payload = await main.proxy_arcade_games()

    assert [g["slug"] for g in payload["games"]] == ["published"]
    assert all(g["slug"] != "benchmark-only" for g in payload["featured"])


# ---------------------------------------------------------------- party game TV stage


def _stage_client(seen, payload):
    class _Resp:
        status = 200
        content_type = "application/json"

        async def json(self):
            return payload

        async def text(self):
            return ""

    class _Client:
        async def get(self, url, **kwargs):
            assert kwargs.get("headers", {}).get("X-Internal-Secret") is not None
            seen.append(("GET", url, kwargs.get("params")))
            return _Resp()

        async def post(self, url, **kwargs):
            assert kwargs.get("headers", {}).get("X-Internal-Secret") is not None
            seen.append(("POST", url, kwargs.get("json")))
            return _Resp()

    @asynccontextmanager
    async def fake_client():
        yield _Client()

    return fake_client


@pytest.mark.asyncio
async def test_arcade_tvs_come_from_execution_discovery_for_the_caller(monkeypatch):
    from services.gateway import main

    seen = []
    screens = [{"id": "ha:media_player.den", "name": "Den TV", "display": "web"}]
    monkeypatch.setattr(main, "shared_http_client", _stage_client(seen, {"screens": screens, "home_assistant": True}))
    monkeypatch.setattr(main, "_require_identity_from_request", AsyncMock(return_value={"user": "jeremiah"}))

    out = await main.proxy_arcade_tvs(MagicMock(), refresh=True)

    assert out == {"success": True, "tvs": screens, "home_assistant": True}
    assert seen == [("GET", f"{main.EXECUTION_SVC}/execute/stage/screens", {"user_id": "jeremiah", "refresh": "true"})]


@pytest.mark.asyncio
async def test_arcade_cast_sends_only_the_arcades_own_stage_page(monkeypatch):
    from services.gateway import main

    seen = []
    monkeypatch.setattr(main, "shared_http_client", _stage_client(seen, {"success": True}))
    monkeypatch.setattr(main, "_require_identity_from_request", AsyncMock(return_value={"user": "jeremiah"}))
    request = MagicMock()
    request.json = AsyncMock(return_value={
        "screen_id": "ha:media_player.den", "room_code": "zi-on", "url": "http://evil.example/", "ha_token": "x",
    })

    await main.proxy_arcade_cast(request)

    _method, url, body = seen[0]
    assert url == f"{main.EXECUTION_SVC}/execute/stage/show"
    assert body == {
        "screen_id": "ha:media_player.den",
        "url": f"{main.ALPACA_ARCADE_PUBLIC_URL.rstrip('/')}/tv/ZION",
        "method": "",
        "user_id": "jeremiah",
    }


@pytest.mark.asyncio
async def test_arcade_cast_stop_and_missing_screen(monkeypatch):
    from services.gateway import main

    seen = []
    monkeypatch.setattr(main, "shared_http_client", _stage_client(seen, {"success": True}))
    monkeypatch.setattr(main, "_require_identity_from_request", AsyncMock(return_value={"user": "jeremiah"}))
    request = MagicMock()
    request.json = AsyncMock(return_value={"screen_id": "cast:abc", "room_code": "ZION", "stop": True})
    await main.proxy_arcade_cast(request)
    assert seen[0][1] == f"{main.EXECUTION_SVC}/execute/stage/stop"

    request.json = AsyncMock(return_value={"room_code": "ZION"})
    with pytest.raises(HTTPException) as exc:
        await main.proxy_arcade_cast(request)
    assert exc.value.status_code == 400
