"""A filed token is the credential the household uses, not the stored password.

A server was logging "Login failed ... 401 Unauthorized" on every Audiobookshelf
request because the stored username and password were tried before the token an
administrator had filed. A changed password on the ABS account should not make
the working token look broken, and it should not fill the log either.
"""
from __future__ import annotations

import pytest

from services.execution import abs_client
from services.execution.handlers import audiobookshelf as abs_handler
from services.execution.schemas import AudiobookshelfRequest, UserContext


def _req(ctx: UserContext) -> AudiobookshelfRequest:
    return AudiobookshelfRequest(action="libraries", user_context=ctx)


@pytest.mark.asyncio
async def test_the_filed_token_is_used_without_trying_the_password(monkeypatch) -> None:
    logins: list[tuple] = []
    used: list[str] = []

    async def no_login(*args, **kwargs):
        logins.append(args)
        return None

    async def fake_libraries(_url, key):
        used.append(key)
        return {"libraries": []}

    monkeypatch.setattr(abs_client, "abs_login", no_login)
    monkeypatch.setattr(abs_client, "get_libraries", fake_libraries)

    ctx = UserContext(
        user="default",
        audiobookshelf_url="https://abs.example.com",
        audiobookshelf_api_key="filed-token",
        audiobookshelf_user="admin",
        audiobookshelf_pass="a password changed elsewhere",
    )

    from services.execution import abs_session_state

    token = abs_session_state.current_relogin.set(None)
    try:
        result = await abs_handler.handle_audiobookshelf(_req(ctx))

        assert result.status == "SUCCESS"
        assert logins == [], "the password must not be tried when a token is filed"
        assert used == ["filed-token"]
        assert abs_session_state.current_relogin.get() is None
    finally:
        abs_session_state.current_relogin.reset(token)


@pytest.mark.asyncio
async def test_a_stored_password_is_still_the_way_in_when_no_token_is_filed(monkeypatch) -> None:
    logins: list[tuple] = []
    used: list[str] = []

    async def ok_login(url, username, password, *, force=False):
        logins.append((url, username, password))
        return "minted-token"

    async def fake_libraries(_url, key):
        used.append(key)
        return {"libraries": []}

    monkeypatch.setattr(abs_client, "abs_login", ok_login)
    monkeypatch.setattr(abs_client, "get_libraries", fake_libraries)

    ctx = UserContext(
        user="default",
        audiobookshelf_url="https://abs.example.com",
        audiobookshelf_user="jarvis",
        audiobookshelf_pass="the household password",
    )

    from services.execution import abs_session_state

    token = abs_session_state.current_relogin.set(None)
    try:
        result = await abs_handler.handle_audiobookshelf(_req(ctx))

        assert result.status == "SUCCESS"
        assert logins == [("https://abs.example.com", "jarvis", "the household password")]
        assert used == ["minted-token"]
        # Sessions opened via password login must register relogin so expired tokens can refresh.
        assert callable(abs_session_state.current_relogin.get())
    finally:
        abs_session_state.current_relogin.reset(token)
