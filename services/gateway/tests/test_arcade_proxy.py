import os
import sys
from unittest.mock import MagicMock

import pytest

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["EXECUTION_SVC"] = "http://execution:8003"

# Mock dependencies before importing main (same preamble as test_calendar_proxy).
mock_redis = MagicMock()
sys.modules["redis"] = mock_redis
sys.modules["redis.asyncio"] = mock_redis
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

    async def fake_fetch():
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

    async def boom():
        raise ValueError("connect timeout")

    monkeypatch.setattr(main, "_fetch_arcade_games", boom)

    payload = await main.proxy_arcade_games()

    assert payload["success"] is False
    assert payload["arcade_available"] is False
    assert "connect timeout" in payload["error"]
    assert payload["games"] == []
    assert payload["count"] == 0
