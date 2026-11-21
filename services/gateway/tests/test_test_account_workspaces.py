"""An automated agent's test account may create its own workspaces, and only its own.

Registering workspaces is otherwise an administrator's job. A test account's request is
narrowed to an id, a name and a description; the gateway forces user scope, makes the
account the owner and prefixes the id with its name, so it can never adopt someone
else's workspace or point one at another path. Made-up values only.
"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import _test_account_workspace, app


class _Resp:
    status = 200

    async def json(self, content_type=None):
        return {"status": "SUCCESS"}



class _Client:
    def __init__(self):
        self.sent: list[dict] = []

    async def request(self, method, url, json=None, headers=None):
        self.sent.append({"method": method, "url": url, "json": json})
        return _Resp()


@pytest.fixture
def setup(monkeypatch):
    fake = _Client()
    who: dict = {}

    async def strict(api_key):
        return who.get(api_key)

    async def proxy_json(resp):
        return {"status": "SUCCESS"}

    monkeypatch.setattr(gateway_main, "get_http_client", lambda: fake)
    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", strict)
    monkeypatch.setattr(gateway_main, "_proxy_json_response", proxy_json)
    return TestClient(app, raise_server_exceptions=False), fake, who


def test_a_test_account_creates_its_own_workspace(setup):
    client, fake, who = setup
    who["fake-agent-key"] = {"user": "test-agent", "is_admin": False, "is_test_account": True}
    resp = client.post("/api/workspaces", headers={"Authorization": "Bearer fake-agent-key"},
                       json={"id": "Scratch Pad", "display_name": "Scratch", "local_path": "/etc",
                             "owner_user": "parent", "scope": "system", "env": {"A": "b"}})
    assert resp.status_code == 200, resp.text
    sent = fake.sent[-1]["json"]
    assert sent == {"id": "test-agent-scratch-pad", "display_name": "Scratch", "scope": "user",
                    "owner_user": "test-agent"}


def test_an_ordinary_member_still_cannot_register_a_workspace(setup):
    client, fake, who = setup
    who["fake-kid-key"] = {"user": "kid", "is_admin": False, "is_test_account": False}
    resp = client.post("/api/workspaces", headers={"Authorization": "Bearer fake-kid-key"}, json={"id": "x"})
    assert resp.status_code == 403
    assert fake.sent == []


def test_an_existing_prefix_is_kept_once():
    assert _test_account_workspace({"id": "test-agent-demo"}, "test-agent")["id"] == "test-agent-demo"


def test_a_workspace_needs_an_id():
    with pytest.raises(HTTPException) as exc:
        _test_account_workspace({}, "test-agent")
    assert exc.value.status_code == 422
