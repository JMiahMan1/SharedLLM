"""Image entities (a vacuum's map) as pictures: the caller's own hub credentials, device permissions, media token.

The hub's ``/api/image_proxy`` is mocked at the HTTP layer; identity and the permission lookup are faked.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, TEST_HA_TOKEN, TEST_HA_URL, TEST_USER, mock_upstream
from fastapi.testclient import TestClient

from services.execution.entity_access import EntityPermissions
from services.gateway import camera_routes
from services.gateway import main as gateway_main
from services.shared.media_token import sign

PNG = b"\x89PNG\r\n\x1a\nmap"
MAP = "image.robot_vacuum_map_0"
HUB_URL = f"{TEST_HA_URL}/api/image_proxy/{MAP}"


@pytest.fixture
def perms(monkeypatch):
    state = {"assigned": frozenset({MAP})}

    async def load(username, is_admin=False):
        if is_admin:
            return EntityPermissions.everything()
        return EntityPermissions(assigned=state["assigned"])

    monkeypatch.setattr(camera_routes, "load_entity_permissions", load)
    return state


@pytest.fixture
def member(monkeypatch):
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)


def _media_query():
    token, _exp = sign(TEST_USER)
    return {"user": TEST_USER, "mt": token}


def test_the_picture_takes_the_media_token_and_the_hub_gets_the_callers_token(upstream, perms, fake_identity):
    mock_upstream(upstream, "GET", HUB_URL, body=PNG, content_type="image/png")
    browser = TestClient(gateway_main.app)  # an <img>: no Authorization header
    resp = browser.get(f"/api/media/images/{MAP}", params=_media_query())
    assert resp.status_code == 200 and resp.content == PNG
    assert resp.headers["content-type"] == "image/png" and "no-store" in resp.headers["cache-control"]
    calls = [c for (m, url), cs in upstream.requests.items() if m == "GET" and str(url) == HUB_URL for c in cs]
    assert calls[0].kwargs["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"
    assert browser.get(f"/api/media/images/{MAP}", params={"token": "test-token"}).status_code == 401


def test_an_image_the_caller_may_not_see_does_not_exist(client, upstream, perms, member):
    perms["assigned"] = frozenset()
    assert client.get(f"/api/media/images/{MAP}").status_code == 404
    for bad in ("camera.front", "image.UPPER", "image.a/../b"):
        assert client.get(f"/api/media/images/{bad}").status_code == 404


def test_the_hubs_words_reach_the_page_when_there_is_no_picture(client, upstream, perms):
    mock_upstream(upstream, "GET", HUB_URL, status=503,
                  payload={"message": "the vacuum only sends the map it is on now; switch it to 'Upstairs'"})
    resp = client.get(f"/api/media/images/{MAP}")
    assert resp.status_code == 503 and "switch it to" in resp.json()["detail"]
    assert TEST_HA_URL not in resp.text


def test_an_image_the_hub_does_not_know_is_404(client, upstream, perms):
    mock_upstream(upstream, "GET", HUB_URL, status=404, payload={"message": "Entity not found."})
    assert client.get(f"/api/media/images/{MAP}").status_code == 404


def test_anonymous_callers_get_nothing(perms):
    assert TestClient(gateway_main.app).get(f"/api/media/images/{MAP}").status_code == 401
