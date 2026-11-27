"""The ABS account list, as execution reports it.

Audiobookshelf mints one token per account and there is no API to mint another
(``POST /api/users/{id}/token`` answers 404 on 2.33), so "give everyone their own
token" means copying each account's own token. Execution is where the admin
credential lives, so it is where the list is read.
"""
import pytest

from services.execution import abs_client
from services.execution.handlers.audiobookshelf import _handle_users

ABS_USERS = {
    "users": [
        {"id": "u1", "username": "admin", "type": "root", "isActive": True, "token": "root-token"},
        {"id": "u2", "username": "sam", "type": "user", "isActive": True, "token": "sam-token"},
        {"id": "u3", "username": "quiet", "type": "user", "isActive": True},
    ]
}


@pytest.mark.asyncio
async def test_the_accounts_are_listed_with_whether_a_token_exists(monkeypatch):
    async def fake_get_users(abs_url, abs_api_key):
        assert (abs_url, abs_api_key) == ("http://abs.local:13378", "admin-key")
        return ABS_USERS

    monkeypatch.setattr(abs_client, "get_users", fake_get_users)
    result = await _handle_users("http://abs.local:13378", "admin-key")

    assert result.status == "SUCCESS"
    assert result.message == "Found 3 Audiobookshelf user(s)."
    users = result.detail["users"]
    assert [u["username"] for u in users] == ["admin", "sam", "quiet"]
    assert users[1]["has_token"] is True
    assert users[1]["token"] == "sam-token"
    assert users[2]["has_token"] is False
    assert users[2]["token"] == ""


@pytest.mark.asyncio
async def test_a_refused_list_is_a_failure_that_says_why(monkeypatch):
    async def fake_get_users(abs_url, abs_api_key):
        return {"error": "ABS returned 403: Forbidden"}

    monkeypatch.setattr(abs_client, "get_users", fake_get_users)
    result = await _handle_users("http://abs.local:13378", "user-key")

    assert result.status == "FAILURE"
    assert "Forbidden" in result.message


@pytest.mark.asyncio
async def test_a_new_account_carries_every_permission_abs_requires(monkeypatch):
    """ABS validates the permissions object whole: a missing key is a 400.

    ``createEreader`` was the one left out, and the server read it as a refusal
    of the whole account rather than as an incomplete permission list.
    """
    seen = {}

    async def fake_post(abs_url, abs_api_key, path, json=None):
        seen["path"] = path
        seen["body"] = json
        return {"user": {"id": "u1", "username": json["username"], "type": "user", "token": "new-token"}}

    monkeypatch.setattr(abs_client, "abs_post", fake_post)
    reply = await abs_client.create_user("http://abs.local:13378", "admin-key", "Robin", "temp-pass")

    assert seen["path"] == "/api/users"
    assert reply["user"]["token"] == "new-token"
    permissions = seen["body"]["permissions"]
    assert set(permissions) == {
        "download",
        "update",
        "delete",
        "upload",
        "createEreader",
        "accessAllLibraries",
        "accessAllTags",
        "accessExplicitContent",
    }
    assert permissions["createEreader"] is True
    # A family server: no deleting library items, no explicit content.
    assert permissions["delete"] is False
    assert permissions["accessExplicitContent"] is False


@pytest.mark.asyncio
async def test_a_new_account_is_activated_or_its_token_is_useless(monkeypatch):
    """ABS creates the account inactive, and an inactive token answers 401.

    Activation is a second call, and it answers with the account rather than the
    token, so the token from creation has to survive it.
    """
    calls = []

    async def fake_post(abs_url, abs_api_key, path, json=None):
        calls.append(("post", path, json))
        return {"user": {"id": "u1", "username": "Robin", "type": "user", "isActive": False, "token": "new-token"}}

    async def fake_patch(abs_url, abs_api_key, path, json=None):
        calls.append(("patch", path, json))
        return {"user": {"id": "u1", "username": "Robin", "type": "user", "isActive": True}}

    monkeypatch.setattr(abs_client, "abs_post", fake_post)
    monkeypatch.setattr(abs_client, "abs_patch", fake_patch)

    reply = await abs_client.create_user("http://abs.local:13378", "admin-key", "Robin", "temp-pass")

    assert calls[1] == ("patch", "/api/users/u1", {"isActive": True})
    assert reply["user"]["isActive"] is True
    assert reply["user"]["token"] == "new-token"


@pytest.mark.asyncio
async def test_a_failed_activation_does_not_claim_the_account_is_active(monkeypatch):
    """If PATCH /api/users/{id} fails, isActive stays False and _handle_user_create fails."""
    from services.execution.handlers.audiobookshelf import _handle_user_create
    from services.execution.schemas import AudiobookshelfRequest, UserContext

    async def fake_post(abs_url, abs_api_key, path, json=None):
        return {"user": {"id": "u1", "username": "Robin", "type": "user", "isActive": False, "token": "new-token"}}

    async def fake_patch(abs_url, abs_api_key, path, json=None):
        return {"error": "ABS returned 500: Internal Server Error"}

    monkeypatch.setattr(abs_client, "abs_post", fake_post)
    monkeypatch.setattr(abs_client, "abs_patch", fake_patch)

    reply = await abs_client.create_user("http://abs.local:13378", "admin-key", "Robin", "temp-pass")
    assert reply["user"]["isActive"] is False
    assert "500" in reply["user"]["activation_error"]

    req = AudiobookshelfRequest(
        action="user_create",
        username="Robin",
        password="temp-pass",
        user_context=UserContext(user="default"),
    )
    result = await _handle_user_create("http://abs.local:13378", "admin-key", req)
    assert result.status == "FAILURE"
    assert "activate" in result.message.lower()
