"""Giving each member their own Audiobookshelf token.

Audiobookshelf mints one token per account and shows it to an administrator, so
the household's administrator credential lives on the default account and these
two routes copy each account's own token onto the member of the same name. The
list route must never hand a token to a browser, and only an administrator may
run either of them: one writes a secret onto somebody else's account.
"""
import pytest

from services.gateway import main as gateway_main

ABS_USERS = {
    "users": [
        {"id": "u1", "username": "admin", "type": "root", "is_active": True, "has_token": True, "token": "root-token"},
        {"id": "u2", "username": "sam", "type": "user", "is_active": True, "has_token": True, "token": "sam-token"},
        {"id": "u3", "username": "quiet", "type": "user", "is_active": True, "has_token": False, "token": ""},
    ]
}


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    async def json(self):
        return self._payload


class _Client:
    def __init__(self):
        self.posts = []
        self.gets = []

    async def get(self, url, **kwargs):
        self.gets.append({"url": url, "headers": kwargs.get("headers")})
        return _Resp(payload=[{"username": "sam"}, {"username": "quiet"}, {"username": "nobody"}])

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        return _Resp()


def _identity_posts(fake):
    """Identity writes only: the same client is also the logging transport."""
    return [post for post in fake.posts if "/api/users/" in post["url"]]


class _Ctx:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


def test_the_list_is_for_administrators_only(client):
    async def _not_admin(api_key):
        return {"user": "kid", "is_admin": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_resolve_strict_identity", _not_admin)
        assert client.get("/api/media/audiobookshelf/users").status_code == 403
        assert client.post("/api/media/audiobookshelf/users/tokens", json={}).status_code == 403


def test_the_list_names_accounts_and_never_carries_a_token(client):
    asked = {}

    async def exec_abs(creds, params, *, timeout=10.0):
        asked["params"] = params
        return ABS_USERS

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        resp = client.get("/api/media/audiobookshelf/users")

    assert resp.status_code == 200, resp.text
    assert asked["params"] == {"action": "users"}
    data = resp.json()
    assert [u["username"] for u in data["users"]] == ["admin", "sam", "quiet"]
    assert data["users"][0]["has_token"] is True
    assert data["users"][2]["has_token"] is False
    # The list says whether a token is there; the token itself never leaves.
    assert set(data["users"][0]) == {"id", "username", "type", "is_active", "has_token"}
    assert "root-token" not in resp.text and "sam-token" not in resp.text


def test_every_matching_member_gets_their_own_token(client):
    fake = _Client()

    async def exec_abs(creds, params, *, timeout=10.0):
        return ABS_USERS

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users/tokens", json={})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # sam has an account and a token; quiet has an account but no token yet;
    # nobody has no account at all. Each outcome is reported as itself.
    assert [issued["username"] for issued in data["issued"]] == ["sam"]
    assert data["skipped"] == [
        {"username": "nobody", "reason": "no Audiobookshelf account with that name"},
        {"username": "quiet", "reason": "Audiobookshelf has no token for this account yet"},
    ]

    posts = _identity_posts(fake)
    sam_post = next(post for post in posts if post["url"].endswith("/api/users/sam/audiobookshelf-token"))
    assert sam_post["json"]["token"] == "sam-token"
    assert sam_post["json"]["audiobookshelf_user"] == "sam"
    assert sam_post["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET


def test_an_account_without_a_token_is_reported_not_invented(client):
    fake = _Client()

    async def exec_abs(creds, params, *, timeout=10.0):
        return {"users": [{"id": "u3", "username": "quiet", "type": "user", "has_token": False, "token": ""}]}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post(
            "/api/media/audiobookshelf/users/tokens", json={"usernames": ["quiet"]}
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["issued"] == []
    assert data["skipped"] == [
        {"username": "quiet", "reason": "Audiobookshelf has no token for this account yet"}
    ]
    assert _identity_posts(fake) == []


def test_named_members_only(client):
    fake = _Client()

    async def exec_abs(creds, params, *, timeout=10.0):
        return ABS_USERS

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users/tokens", json={"usernames": ["SAM"]})

    assert resp.status_code == 200, resp.text
    assert [issued["username"] for issued in resp.json()["issued"]] == ["sam"]
    # A named run must not enumerate the household at all.
    assert [get for get in fake.gets if "/api/users" in get["url"]] == []


def test_an_unreachable_server_is_a_502_not_a_silent_success(client):
    async def exec_abs(creds, params, *, timeout=10.0):
        raise gateway_main._MediaUpstreamError("Audiobookshelf is unreachable (TimeoutError)")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        resp = client.post("/api/media/audiobookshelf/users/tokens", json={})

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["message"]
