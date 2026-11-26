"""Setting every household member up on Audiobookshelf from one administrator token.

Audiobookshelf lets an administrator create an account and answers with that
account's own token, so the household needs exactly one administrator credential
(on the default account) to give everyone their own login. Nothing here knows a
member by name: Identity lists the members and Audiobookshelf lists the accounts,
both read at the time of the call.
"""
import pytest

from services.gateway import main as gateway_main


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
        return _Resp(payload=[{"username": "sam"}, {"username": "newbie"}])

    async def post(self, url, **kwargs):
        self.posts.append({"url": url, "json": kwargs.get("json"), "headers": kwargs.get("headers")})
        return _Resp()


class _Ctx:
    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


def _identity_posts(fake):
    """Identity writes only: the same client is also the logging transport."""
    return [post for post in fake.posts if "/api/users/" in post["url"]]


def _accounts(*entries):
    return {"users": list(entries)}


def test_provisioning_is_for_administrators_only(client):
    async def _not_admin(api_key):
        return {"user": "kid", "is_admin": False}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_resolve_strict_identity", _not_admin)
        assert client.post("/api/media/audiobookshelf/users", json={"username": "a", "password": "b"}).status_code == 403
        assert client.post("/api/media/audiobookshelf/users/provision", json={"password": "b"}).status_code == 403


def test_a_password_is_required_before_anything_is_created(client):
    async def exec_abs(creds, params, *, timeout=10.0):  # pragma: no cover - must not run
        raise AssertionError("no account may be created without a password")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        resp = client.post("/api/media/audiobookshelf/users/provision", json={})

    assert resp.status_code == 400
    assert "password" in resp.json()["message"].lower()


def test_one_account_is_created_and_its_token_filed(client):
    fake = _Client()
    seen = []

    async def exec_abs(creds, params, *, timeout=10.0):
        seen.append(params)
        if params["action"] == "user_create":
            return {"user": {"id": "u9", "username": params["username"], "type": "user", "has_token": True, "token": "new-token"}}
        return _accounts()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users", json={"username": "Kaleb", "password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    assert seen[0] == {"action": "user_create", "username": "Kaleb", "password": "temp-pass"}
    # The browser is told the account exists; it is never handed the token.
    assert resp.json()["user"] == {"id": "u9", "username": "Kaleb", "type": "user", "has_token": True}
    assert "new-token" not in resp.text


def test_provisioning_creates_the_missing_and_files_everyone(client):
    fake = _Client()
    made = []

    async def exec_abs(creds, params, *, timeout=10.0):
        if params["action"] == "user_create":
            made.append(params["username"])
            return {"user": {"id": f"id-{params['username']}", "username": params["username"], "type": "user", "has_token": True, "token": f"{params['username']}-token"}}
        # sam already has an account; newbie does not.
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass"})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the member without an account is created; the existing one is left alone.
    assert made == ["newbie"]
    assert data["created"] == ["newbie"]
    assert [entry["username"] for entry in data["filed"]] == ["newbie", "sam"]

    posts = _identity_posts(fake)
    filed = {post["url"].rsplit("/", 2)[-2]: post["json"]["token"] for post in posts}
    assert filed == {"newbie": "newbie-token", "sam": "sam-token"}
    assert all(post["headers"]["X-Internal-Secret"] == gateway_main.INTERNAL_SECRET for post in posts)


def test_an_account_that_cannot_be_created_is_reported_not_swallowed(client):
    fake = _Client()

    async def exec_abs(creds, params, *, timeout=10.0):
        if params["action"] == "user_create":
            return {"user": {}}
        return _accounts({"id": "u1", "username": "sam", "type": "user", "has_token": True, "token": "sam-token"})

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(gateway_main, "_media_exec_audiobookshelf", exec_abs)
        mp.setattr(gateway_main, "shared_http_client", lambda: _Ctx(fake))
        resp = client.post("/api/media/audiobookshelf/users/provision", json={"password": "temp-pass", "usernames": ["newbie"]})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Nothing came back, so nothing is claimed: no account, no token, and the
    # member is named with the reason instead of being silently dropped.
    assert data["created"] == []
    assert data["filed"] == []
    assert data["skipped"] == [{"username": "newbie", "reason": "Audiobookshelf did not return the new account"}]
