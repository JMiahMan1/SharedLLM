"""The live log stream is the whole system's logs: admins (or the internal
secret) only, and an unknown token is refused rather than resolved to a
default account."""
import asyncio

import pytest

from services.logging import main as logging_main


class _Resp:
    def __init__(self, status, body=None):
        self.status = status
        self._body = body or {}

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _identity(monkeypatch, answers):
    calls = []

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None, **kw):
            assert "params" not in kw, "the key must not travel in the URL"
            key = (headers or {}).get("X-Api-Key")
            calls.append((url, key))
            return answers.get(key, _Resp(401))

    monkeypatch.setattr(logging_main.aiohttp, "ClientSession", lambda *a, **k: _Session())
    return calls


class _Socket:
    def __init__(self, token):
        self.query_params = {"token": token} if token is not None else {}


def _authorized(token):
    return asyncio.run(logging_main._ws_authorized(_Socket(token)))


def test_an_unknown_token_is_refused_not_resolved_to_a_default_account(monkeypatch):
    calls = _identity(monkeypatch, {})
    assert _authorized("anything") is False
    assert calls and calls[0][0].endswith("/api/internal/validate-api-key")


def test_a_family_members_key_is_not_enough(monkeypatch):
    _identity(monkeypatch, {"kid-key": _Resp(200, {"user": "leo", "is_admin": False})})
    assert _authorized("kid-key") is False


def test_an_admins_key_opens_the_stream(monkeypatch):
    _identity(monkeypatch, {"admin-key": _Resp(200, {"user": "jeremiah", "is_admin": True})})
    assert _authorized("admin-key") is True


@pytest.mark.parametrize("token", [None, ""])
def test_no_token_is_refused_without_asking_identity(monkeypatch, token):
    calls = _identity(monkeypatch, {})
    assert _authorized(token) is False
    assert calls == []


def test_the_internal_secret_still_works(monkeypatch):
    calls = _identity(monkeypatch, {})
    assert _authorized(logging_main.INTERNAL_SECRET) is True
    assert calls == []


class _LiveSocket:
    """Accepted socket: a client that pings once, then leaves."""

    def __init__(self, incoming):
        self.query_params = {"token": logging_main.INTERNAL_SECRET}
        self.sent = []
        self._incoming = list(incoming)

    async def accept(self):
        pass

    async def close(self, code=1000):
        pass

    async def receive_text(self):
        await asyncio.sleep(0.01)
        if not self._incoming:
            raise RuntimeError("client left")
        return self._incoming.pop(0)

    async def send_text(self, text):
        self.sent.append(text)


class _PubSub:
    def __init__(self, lines):
        self.lines = lines
        self.closed = False

    async def subscribe(self, channel):
        pass

    async def unsubscribe(self, channel):
        pass

    async def close(self):
        self.closed = True

    async def listen(self):
        for line in self.lines:
            yield {"type": "message", "data": line}
        await asyncio.sleep(3600)


def test_stream_answers_pings_and_ends_when_the_client_leaves(monkeypatch):
    pubsub = _PubSub(['{"message": "hello"}'])

    class _Redis:
        def pubsub(self):
            return pubsub

    async def fake_redis():
        return _Redis()

    monkeypatch.setattr(logging_main, "get_redis", fake_redis)
    socket = _LiveSocket(['{"type": "ping"}', "not json"])
    asyncio.run(asyncio.wait_for(logging_main._ws_handler(socket), timeout=2))
    assert '{"message": "hello"}' in socket.sent
    assert '{"ping": true}' in socket.sent
    assert pubsub.closed  # the subscription is released, not left listening


def test_identity_settings_for_retention_take_effect(monkeypatch):
    """log_retention_days / log_max_entries resolved at startup replace the
    import-time defaults (they used to be ignored)."""
    from services import config

    async def resolve():
        monkeypatch.setattr(config, "LOG_RETENTION_DAYS", 7)
        monkeypatch.setattr(config, "LOG_MAX_ENTRIES", 2500)

    monkeypatch.setattr(config, "resolve_runtime_config", resolve)
    monkeypatch.setattr(logging_main, "retention_cleanup_task", lambda: asyncio.sleep(0))
    monkeypatch.setattr(logging_main, "LOG_RETENTION_DAYS", 30)
    monkeypatch.setattr(logging_main, "LOG_MAX_ENTRIES", 10000)

    async def run():
        async with logging_main.lifespan(logging_main.app):
            return logging_main.LOG_RETENTION_DAYS, logging_main.LOG_MAX_ENTRIES

    assert asyncio.run(run()) == (7, 2500)
