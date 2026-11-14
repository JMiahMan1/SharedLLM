"""Helper routes: admins only, forwarded to the hub's /hub/helpers with the internal secret.

The hub is faked at ``automation_routes._hub`` as in the automation route tests; names are made up.
"""
import pytest
from fastapi import HTTPException

from services.gateway import automation_routes
from services.gateway import main as gateway_main


@pytest.fixture
def hub(monkeypatch):
    seen = []
    answers = {}

    async def call(method, path, *, creds=None, params=None, json=None, timeout=None):
        seen.append((method, path, "secret" if creds is None else "caller", json))
        if path in answers:
            return answers[path]
        return 200, {"helpers": [], "types": [], "problems": []}

    monkeypatch.setattr(automation_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def member(monkeypatch):
    async def not_admin(request):
        raise HTTPException(status_code=403, detail="Admin only")

    monkeypatch.setattr(gateway_main, "_require_admin", not_admin)


ROUTES = [
    ("get", "/api/homehub/helpers", "/hub/helpers"),
    ("post", "/api/homehub/helpers/timer", "/hub/helpers/timer"),
    ("put", "/api/homehub/helpers/timer/pasta", "/hub/helpers/timer/pasta"),
    ("delete", "/api/homehub/helpers/input_boolean/guest_mode", "/hub/helpers/input_boolean/guest_mode"),
    ("post", "/api/homehub/helpers/counter/laps/call", "/hub/helpers/counter/laps/call"),
]


def _send(client, verb, path):
    if verb in ("post", "put"):
        return getattr(client, verb)(path, json={"name": "Pasta"})
    return getattr(client, verb)(path)


@pytest.mark.parametrize("verb,path,hub_path", ROUTES)
def test_admins_reach_the_hub_with_the_secret(client, hub, verb, path, hub_path):
    seen, _ = hub
    assert _send(client, verb, path).status_code == 200
    assert len(seen) == 1 and seen[0][:3] == (verb.upper(), hub_path, "secret")
    if verb in ("post", "put"):
        assert seen[0][3] == {"name": "Pasta"}


@pytest.mark.parametrize("verb,path,hub_path", ROUTES)
def test_members_cannot_touch_helpers(client, hub, member, verb, path, hub_path):
    seen, _ = hub
    assert _send(client, verb, path).status_code == 403
    assert seen == []


@pytest.mark.parametrize("path", ["/api/homehub/helpers/climate/x", "/api/homehub/helpers/timer/Bad-Id"])
def test_unknown_types_and_ids_are_not_forwarded(client, hub, path):
    seen, _ = hub
    assert client.put(path, json={}).status_code == 404
    assert seen == []


def test_the_hubs_refusal_reaches_the_page(client, hub):
    _, answers = hub
    answers["/hub/helpers/input_number"] = (400, {"message": "max must be greater than min"})
    resp = client.post("/api/homehub/helpers/input_number", json={"name": "x", "min": 3, "max": 1})
    assert resp.status_code == 400 and resp.json()["detail"] == "max must be greater than min"
