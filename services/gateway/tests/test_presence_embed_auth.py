"""Presence (who is in which room) and embeddings are for signed-in callers."""
import pytest
from fastapi.testclient import TestClient

from services.gateway import main


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.mark.parametrize("verb,path", [
    ("GET", "/api/presence/all"),
    ("GET", "/api/presence/rooms"),
    ("GET", "/api/presence/jeremiah"),
    ("POST", "/api/embed"),
    ("POST", "/api/embeddings"),
    ("POST", "/api/show"),
    ("POST", "/v1/embeddings"),
])
def test_anonymous_callers_are_refused(client, verb, path):
    resp = client.request(verb, path, json={} if verb == "POST" else None)
    assert resp.status_code == 401, resp.text


def test_all_and_rooms_reach_their_own_routes():
    paths = [r.path for r in main.app.routes if getattr(r, "path", "").startswith("/api/presence/")]
    assert paths.index("/api/presence/all") < paths.index("/api/presence/{user_id}")
    assert paths.index("/api/presence/rooms") < paths.index("/api/presence/{user_id}")
