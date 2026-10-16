"""services.common.notify.family: who "everyone" is for a notice."""
from services.common import notify


class _Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def json(self, **_):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Client:
    def __init__(self, status=200, body=None, fail=False):
        self.status, self.body, self.fail = status, body, fail

    def get(self, url, headers=None, **_):
        if self.fail:
            raise OSError("identity down")
        assert url == "http://identity/api/users" and headers == {"X-Internal-Secret": "s"}
        return _Resp(self.status, self.body)


async def _family(client):
    return await notify.family(client=client, identity="http://identity/", secret="s")


async def test_everyone_but_the_system_account():
    users = [{"username": "michele"}, {"username": "default", "is_system_default": True}, {"username": "jeremiah"}, {}]
    assert await _family(_Client(body=users)) == ["jeremiah", "michele"]


async def test_nobody_when_identity_fails():
    assert await _family(_Client(status=500)) == []
    assert await _family(_Client(fail=True)) == []
