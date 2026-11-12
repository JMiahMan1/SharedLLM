"""services.common.device_noun: a tablet is never called a phone."""
from services.common import device_noun


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
        assert url == "http://identity/api/internal/owned-devices/jordan" and headers == {"X-Internal-Secret": "s"}
        return _Resp(self.status, self.body)


def test_the_word_follows_the_kinds():
    assert device_noun.noun(["phone"]) == "phone"
    assert device_noun.noun(["tablet", "tablet"]) == "tablet"
    assert device_noun.noun(["tablet", "phone"]) == "phone or tablet"
    assert device_noun.noun(["watch"]) == "device"
    assert device_noun.noun([]) == "device"
    assert device_noun.noun(None) == "device"


def test_revoked_devices_do_not_count():
    details = [{"kind": "tablet"}, {"kind": "phone", "revoked": True}, {"kind": "watch"}]
    assert device_noun.kinds_of(details) == ["tablet"]


async def _kinds(client):
    return await device_noun.kinds("jordan", client=client, identity="http://identity/", secret="s")


async def test_kinds_come_from_identity():
    body = {"devices": ["a", "b"], "details": [{"kind": "tablet"}, {"kind": "Tablet"}]}
    assert await _kinds(_Client(body=body)) == ["tablet"]


async def test_nothing_known_when_identity_fails():
    assert await _kinds(_Client(status=500)) == []
    assert await _kinds(_Client(fail=True)) == []
    assert await device_noun.noun_for("jordan", client=_Client(fail=True), identity="http://identity", secret="s") == "device"
