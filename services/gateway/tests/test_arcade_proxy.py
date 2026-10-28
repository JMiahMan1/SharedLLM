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
