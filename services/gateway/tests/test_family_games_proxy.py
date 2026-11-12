"""Family Games shelf: a game reaches players once an admin publishes it.

Admins see the review queue and unpublished games, and publish through the
arcade with the shared admin token.
"""

import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

sys.modules["fastembed"] = MagicMock()
sys.modules["intent_engine"] = MagicMock()
sys.modules["background_worker"] = MagicMock()


def _game(slug, state, average=0.0, votes=0, plays=0):
    return {
        "slug": slug,
        "title": slug.title(),
        "collection": "family",
        "family_state": state,
        "mobile_compatible": True,
        "plays": plays,
        "rating": {"count": votes, "average": average},
    }


GAMES = [
    _game("great", "published", average=4.8, votes=5),
    _game("good", "published", average=4.2, votes=3),
    _game("unloved", "published", average=2.5, votes=4),
    _game("new-and-shiny", "published", average=5.0, votes=1),
    _game("testing-one", "testing", average=5.0, votes=9),
    _game("retired", "unpublished", average=4.9, votes=8),
]


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return ""


def _client(monkeypatch, main, *, get_payload=None, seen=None, post_status=200):
    class _Client:
        async def get(self, url, **kwargs):
            if seen is not None:
                seen.setdefault("get", []).append((url, kwargs))
            return _Resp(200, get_payload)

        async def post(self, url, **kwargs):
            if seen is not None:
                seen["post"] = (url, kwargs)
            return _Resp(post_status, {"success": True, "state": kwargs["json"]["state"]})

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(main, "shared_http_client", fake)
    monkeypatch.setattr(main, "_safe_json", AsyncMock(side_effect=lambda r: r._payload))


def _as(monkeypatch, main, user, is_admin):
    monkeypatch.setattr(main, "_acting_identity", AsyncMock(return_value={"user": user, "is_admin": is_admin}))


@pytest.mark.asyncio
async def test_players_see_every_published_game_best_rated_first(monkeypatch):
    from services.gateway import main

    _client(monkeypatch, main, get_payload={"success": True, "games": GAMES})
    _as(monkeypatch, main, "kate", False)

    payload = await main.proxy_family_games(MagicMock())

    assert payload["arcade_available"] is True
    # Published is enough: a new game with one vote and a low-rated one are on the shelf too.
    assert [g["slug"] for g in payload["games"]] == ["new-and-shiny", "great", "good", "unloved"]
    assert payload["review"] == [] and payload["unpublished"] == []
    assert payload["is_admin"] is False
    assert "admin_token_configured" not in payload


@pytest.mark.asyncio
async def test_admins_get_the_review_queue_with_reasons(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "ARCADE_ADMIN_TOKEN", "tok")
    _client(monkeypatch, main, get_payload={"success": True, "games": GAMES})
    _as(monkeypatch, main, "alex", True)

    payload = await main.proxy_family_games(MagicMock())

    review = {g["slug"]: g["shelf_reason"] for g in payload["review"]}
    assert review == {"testing-one": "In testing"}
    assert [g["slug"] for g in payload["unpublished"]] == ["retired"]
    assert [g["slug"] for g in payload["games"]] == ["new-and-shiny", "great", "good", "unloved"]
    assert payload["admin_token_configured"] is True


@pytest.mark.asyncio
async def test_an_unreachable_arcade_is_reported_not_raised(monkeypatch):
    from services.gateway import main

    @asynccontextmanager
    async def broken():
        raise main.aiohttp.ClientError("connect refused")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "shared_http_client", broken)
    _as(monkeypatch, main, "kate", False)

    payload = await main.proxy_family_games(MagicMock())

    assert payload["success"] is False and payload["arcade_available"] is False
    assert "connect refused" in payload["error"]
    assert payload["games"] == []


@pytest.mark.asyncio
async def test_the_shelf_needs_a_signed_in_member(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "_acting_identity", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await main.proxy_family_games(MagicMock())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_admin_publish_forwards_the_arcade_token(monkeypatch):
    from services.gateway import main

    seen = {}
    monkeypatch.setattr(main, "ARCADE_ADMIN_TOKEN", "tok")
    monkeypatch.setattr(main, "_require_admin", AsyncMock(return_value="alex"))
    _client(monkeypatch, main, seen=seen)
    request = MagicMock()
    request.json = AsyncMock(return_value={"state": "published"})

    response = await main.proxy_family_game_state("family-cloud-hop", request)

    url, kwargs = seen["post"]
    assert url == f"{main.ALPACA_ARCADE_URL}/api/admin/family/family-cloud-hop/state"
    assert kwargs["headers"] == {"X-Arcade-Token": "tok"}
    assert kwargs["json"] == {"state": "published", "by": "alex"}
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_publish_fails_loudly_without_the_admin_token(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "ARCADE_ADMIN_TOKEN", "")
    monkeypatch.setattr(main, "_require_admin", AsyncMock(return_value="alex"))
    request = MagicMock()
    request.json = AsyncMock(return_value={"state": "published"})

    with pytest.raises(HTTPException) as exc:
        await main.proxy_family_game_state("family-cloud-hop", request)

    assert exc.value.status_code == 503
    assert "ARCADE_ADMIN_TOKEN" in exc.value.detail


@pytest.mark.asyncio
async def test_publish_rejects_a_bad_state_and_non_admins(monkeypatch):
    from services.gateway import main

    monkeypatch.setattr(main, "ARCADE_ADMIN_TOKEN", "tok")
    monkeypatch.setattr(main, "_require_admin", AsyncMock(return_value="alex"))
    request = MagicMock()
    request.json = AsyncMock(return_value={"state": "featured"})
    with pytest.raises(HTTPException) as exc:
        await main.proxy_family_game_state("family-cloud-hop", request)
    assert exc.value.status_code == 422

    monkeypatch.setattr(
        main, "_require_admin", AsyncMock(side_effect=HTTPException(status_code=403, detail="Admin only"))
    )
    with pytest.raises(HTTPException) as exc:
        await main.proxy_family_game_state("family-cloud-hop", request)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_family_highscores_ask_the_arcade_for_the_family_board(monkeypatch):
    from services.gateway import main

    seen = {}
    _client(monkeypatch, main, get_payload={"players": [], "games": []}, seen=seen)
    monkeypatch.setattr(main, "_require_identity_from_request", AsyncMock(return_value={"user": "kate"}))
    request = MagicMock()
    request.query_params = {"collection": "family"}

    payload = await main.proxy_arcade_highscores(request)

    assert payload["success"] is True
    url, kwargs = seen["get"][0]
    assert url.endswith("/api/highscores") and kwargs["params"] == {"collection": "family"}


@pytest.mark.asyncio
async def test_a_card_not_marked_mobile_compatible_is_never_shown(monkeypatch):
    from services.gateway import main

    desktop = {**_game("desktop-thing", "published", average=5.0, votes=9), "mobile_compatible": False}
    _client(monkeypatch, main, get_payload={"success": True, "games": [*GAMES, desktop]})
    _as(monkeypatch, main, "alex", True)

    payload = await main.proxy_family_games(MagicMock())

    every = payload["games"] + payload["review"] + payload["unpublished"]
    assert "desktop-thing" not in {g["slug"] for g in every}
