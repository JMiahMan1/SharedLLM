"""The gateway's own ABS calls work for a listener with only an ABS password.

Regression for 2026-11-21: a household account with its own Audiobookshelf
username and password but no API key (ABS does not let an ordinary user make
one) got 400 "Audiobookshelf API key is not configured" from every
``GET /api/media/item?uri=abs://...``, so the book page said "Couldn't load
this book" and nothing could be played. The execution service, which logs in
with the password, could search the same library, so the failure looked like a
broken book rather than a missing credential path.

ABS is mocked at its real HTTP boundary (aioresponses); identity is faked to
the listener's own resolved credentials.
"""
import base64
import json
import time

import pytest
from conftest_media import TEST_ABS_URL, TEST_MASS_TOKEN, TEST_MASS_URL, TEST_USER, mock_upstream
from yarl import URL

from services.gateway import abs_auth
from services.gateway import main as gateway_main

BOOK_ID = "book-1"
ITEM_URL = f"{TEST_ABS_URL}/api/items/{BOOK_ID}"
PROGRESS_URL = f"{TEST_ABS_URL}/api/me/progress/{BOOK_ID}"
LOGIN_URL = f"{TEST_ABS_URL}/login"

PASSWORD_ONLY = {
    "user": TEST_USER,
    "username": TEST_USER,
    "user_id": 7,
    "mass_url": TEST_MASS_URL,
    "mass_token": TEST_MASS_TOKEN,
    "audiobookshelf_url": TEST_ABS_URL,
    "audiobookshelf_user": "listener",
    "audiobookshelf_pass": "listener-password",
    "audiobookshelf_api_key": None,
    "is_admin": False,
}

BOOK = {
    "media": {
        "metadata": {"title": "A New Book", "authorName": "An Author"},
        "duration": 30066.0,
        "chapters": [{"title": "One", "duration": 100.0}],
    }
}


def _jwt(exp_in: float) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + exp_in}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


@pytest.fixture(autouse=True)
def _fresh_tokens():
    abs_auth._tokens.clear()
    yield
    abs_auth._tokens.clear()


@pytest.fixture
def as_user(client, monkeypatch):
    """Resolve the caller to ``creds`` (a password-only listener by default)."""
    current = {"creds": dict(PASSWORD_ONLY)}

    async def _resolve(body):
        return dict(current["creds"])

    monkeypatch.setattr(gateway_main, "resolve_identity", _resolve)
    return current


def _calls(upstream, method: str, url: str) -> list:
    return [call for (m, u), calls in upstream.requests.items() if m == method and str(u).startswith(url) for call in calls]


def _bearer(call) -> str:
    return (call.kwargs.get("headers") or {}).get("Authorization", "")


def test_book_opens_for_a_listener_with_only_a_password(client, upstream, as_user):
    token = _jwt(3600)
    upstream.post(LOGIN_URL, payload={"user": {"username": "listener", "accessToken": token}})
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK)
    mock_upstream(upstream, "GET", PROGRESS_URL, payload={"currentTime": 1234.5, "progress": 0.041, "isFinished": False})

    resp = client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "A New Book"
    assert body["resume_position"] == 1234.5
    assert body["progress"] == pytest.approx(0.041)
    assert body["is_finished"] is False
    # Logged in as the listener's own ABS account, and used that token.
    login = _calls(upstream, "POST", LOGIN_URL)
    assert [c.kwargs["json"]["username"] for c in login] == ["listener"]
    assert [_bearer(c) for c in _calls(upstream, "GET", ITEM_URL)] == [f"Bearer {token}"]
    assert [_bearer(c) for c in _calls(upstream, "GET", PROGRESS_URL)] == [f"Bearer {token}"]


def test_the_login_is_reused_until_it_expires(client, upstream, as_user):
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": _jwt(3600)}})
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK, repeat=2)
    mock_upstream(upstream, "GET", PROGRESS_URL, status=404, repeat=2)

    for _ in range(2):
        assert client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"}).status_code == 200

    assert len(_calls(upstream, "POST", LOGIN_URL)) == 1


def test_a_refused_token_logs_in_again_once(client, upstream, as_user):
    stale, fresh = _jwt(3600), _jwt(3600) + "x"
    abs_auth._tokens[(TEST_ABS_URL.lower(), "listener")] = (stale, time.time() + 600)
    mock_upstream(upstream, "GET", ITEM_URL, status=401)
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": fresh}})
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK)
    mock_upstream(upstream, "GET", PROGRESS_URL, status=404)

    resp = client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"})

    assert resp.status_code == 200, resp.text
    assert [_bearer(c) for c in _calls(upstream, "GET", ITEM_URL)] == [f"Bearer {stale}", f"Bearer {fresh}"]


def test_no_progress_yet_means_no_resume_position(client, upstream, as_user):
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": _jwt(3600)}})
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK)
    mock_upstream(upstream, "GET", PROGRESS_URL, status=404)

    body = client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"}).json()

    assert body["resume_position"] is None
    assert body["progress"] is None


def test_a_finished_book_starts_over(client, upstream, as_user):
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": _jwt(3600)}})
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK)
    mock_upstream(upstream, "GET", PROGRESS_URL, payload={"currentTime": 30000, "isFinished": True})

    body = client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"}).json()

    assert body["resume_position"] is None
    assert body["progress"] == 1.0
    assert body["is_finished"] is True


def test_an_api_key_is_still_used_as_is(client, upstream, as_user):
    as_user["creds"] = {**PASSWORD_ONLY, "audiobookshelf_api_key": "own-key", "audiobookshelf_pass": None}
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK)
    mock_upstream(upstream, "GET", PROGRESS_URL, status=404)

    assert client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"}).status_code == 200
    assert [_bearer(c) for c in _calls(upstream, "GET", ITEM_URL)] == ["Bearer own-key"]
    assert _calls(upstream, "POST", LOGIN_URL) == []


def test_neither_key_nor_password_says_what_is_missing(client, upstream, as_user):
    as_user["creds"] = {**PASSWORD_ONLY, "audiobookshelf_pass": None}

    resp = client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"})

    assert resp.status_code == 400
    assert "username and password" in resp.json()["detail"]
    # Nothing was asked of ABS on anyone else's behalf.
    assert [u for (_, u) in upstream.requests if str(u).startswith(TEST_ABS_URL)] == []


def test_a_wrong_password_is_reported_not_retried_as_someone_else(client, upstream, as_user):
    upstream.post(LOGIN_URL, status=401)

    resp = client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"})

    assert resp.status_code == 401
    assert "refused" in resp.json()["detail"]
    assert _calls(upstream, "GET", ITEM_URL) == []


def test_logins_are_kept_per_listener(client, upstream, as_user):
    """Two listeners on one ABS server never share a token."""
    first, second = _jwt(3600), _jwt(3600) + "2"
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": first}})
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": second}})
    mock_upstream(upstream, "GET", ITEM_URL, payload=BOOK, repeat=2)
    mock_upstream(upstream, "GET", PROGRESS_URL, status=404, repeat=2)

    client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"})
    as_user["creds"] = {**PASSWORD_ONLY, "audiobookshelf_user": "sibling", "audiobookshelf_pass": "sibling-pw"}
    client.get("/api/media/item", params={"uri": f"abs://{BOOK_ID}"})

    assert [c.kwargs["json"]["username"] for c in _calls(upstream, "POST", LOGIN_URL)] == ["listener", "sibling"]
    assert [_bearer(c) for c in _calls(upstream, "GET", ITEM_URL)] == [f"Bearer {first}", f"Bearer {second}"]


def test_a_cover_is_fetched_with_the_listeners_own_login(client, upstream, as_user):
    token = _jwt(3600)
    upstream.post(LOGIN_URL, payload={"user": {"accessToken": token}})
    cover = f"{TEST_ABS_URL}/api/items/{BOOK_ID}/cover"
    mock_upstream(upstream, "GET", cover, body=b"\x89PNG", content_type="image/png")

    resp = client.get("/api/media/imageproxy", params={"path": f"/api/items/{BOOK_ID}/cover"})

    assert resp.status_code == 200, resp.text
    assert [_bearer(c) for c in _calls(upstream, "GET", cover)] == [f"Bearer {token}"]


def test_token_ttl_follows_the_jwt_expiry():
    assert abs_auth._token_ttl(_jwt(120)) == pytest.approx(60, abs=2)
    assert abs_auth._token_ttl(_jwt(10 * 3600)) == abs_auth.TOKEN_TTL_S
    assert abs_auth._token_ttl("opaque-legacy-token") == abs_auth.TOKEN_TTL_S


def test_yarl_is_what_aioresponses_keys_on():
    # _calls() compares str(URL); keep that assumption visible.
    assert str(URL(ITEM_URL)) == ITEM_URL
