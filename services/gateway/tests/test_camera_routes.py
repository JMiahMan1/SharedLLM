"""Cameras page routes: the caller's own hub credentials, device permissions, and streams passed through.

The hub (homehub's camera API) is mocked at the HTTP layer; identity and the
permission lookup are faked. What matters here: the HA token goes to the hub
and never to the browser, a camera the caller may not see does not exist, the
picture/stream/recording routes take the signed media token (not an API key in
the URL), and Range reaches the hub so a recording can seek.
"""
import pytest
from conftest_media import DEFAULT_IDENTITY, TEST_HA_TOKEN, TEST_HA_URL, TEST_USER, mock_upstream
from fastapi.testclient import TestClient

from services.execution.entity_access import EntityPermissions
from services.gateway import camera_routes
from services.gateway import main as gateway_main
from services.shared.media_token import sign

CAMERAS = [
    {"entity_id": "camera.garage_door_camera", "name": "Garage Door", "area": "Garage", "platform": "esphome",
     "state": "idle", "recordings": False, "live": ["snapshot", "mjpeg"], "stream": "snapshots"},
    {"entity_id": "camera.backyard_fluent", "name": "Backyard", "area": "Yard", "platform": "reolink",
     "state": "idle", "recordings": True, "live": ["snapshot", "mjpeg"], "stream": "snapshots", "battery": True},
]
RECORDINGS = {"entity_id": "camera.backyard_fluent", "start": "2026-11-09T00:00:00+00:00",
              "end": "2026-11-10T00:00:00+00:00",
              "recordings": [{"file": "Mp4Record/2026-11-09/RecM07_DST20261109_101500_101530.mp4",
                              "start": "2026-11-09T10:15:00-07:00", "end": "2026-11-09T10:15:30-07:00",
                              "duration": 30.0, "triggers": ["person", "motion"], "size": 2048, "stream": "sub"}]}
JPEG = b"\xff\xd8jpegdata\xff\xd9"


@pytest.fixture
def perms(monkeypatch):
    """Who may see what: admins everything, others only their assigned cameras."""
    state = {"assigned": frozenset({"camera.garage_door_camera"})}

    async def load(username, is_admin=False):
        if is_admin:
            return EntityPermissions.everything()
        return EntityPermissions(assigned=state["assigned"])

    monkeypatch.setattr(camera_routes, "load_entity_permissions", load)
    return state


@pytest.fixture
def member(monkeypatch):
    """The caller is a family member, not an admin."""

    async def resolve(body):
        return {**DEFAULT_IDENTITY, "is_admin": False}

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)


def _sent(upstream, method, prefix):
    """The (url, kwargs) of every call made to a URL starting with ``prefix``."""
    return [(str(url), call.kwargs) for (m, url), calls in upstream.requests.items() if m == method
            and str(url).startswith(prefix) for call in calls]


def _media_query():
    token, _exp = sign(TEST_USER)
    return {"user": TEST_USER, "mt": token}


def test_list_forwards_the_callers_token_and_filters_by_permission(client, upstream, perms, member):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/cameras", payload=CAMERAS)
    resp = client.get("/api/cameras")
    assert resp.status_code == 200
    assert [c["entity_id"] for c in resp.json()["cameras"]] == ["camera.garage_door_camera"]
    (_url, kwargs), = _sent(upstream, "GET", f"{TEST_HA_URL}/api/homehub/cameras")
    assert kwargs["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"
    assert TEST_HA_TOKEN not in resp.text and TEST_HA_URL not in resp.text


def test_admin_sees_every_camera(client, upstream, perms):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/cameras", payload=CAMERAS)
    assert len(client.get("/api/cameras").json()["cameras"]) == 2


def test_list_says_when_the_hub_is_down(client, upstream, perms):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/homehub/cameras", status=500, payload={"message": "x"})
    resp = client.get("/api/cameras")
    assert resp.status_code == 502 and TEST_HA_URL not in resp.text


def test_list_needs_ha_credentials(client, monkeypatch, perms):
    async def resolve(body):
        return {**DEFAULT_IDENTITY, "ha_url": None, "ha_token": None}

    monkeypatch.setattr(gateway_main, "resolve_identity", resolve)
    resp = client.get("/api/cameras")
    assert resp.status_code == 503 and "not set up" in resp.json()["detail"]


def test_anonymous_callers_get_nothing(perms):
    anon = TestClient(gateway_main.app)
    assert anon.get("/api/cameras").status_code == 401
    assert anon.get("/api/media/cameras/camera.garage_door_camera/snapshot").status_code == 401


def test_recordings_pass_the_range_and_the_hubs_words(client, upstream, perms):
    url = f"{TEST_HA_URL}/api/homehub/cameras/camera.backyard_fluent/recordings"
    mock_upstream(upstream, "GET", url, payload=RECORDINGS)
    resp = client.get("/api/cameras/camera.backyard_fluent/recordings",
                      params={"start": "2026-11-09T00:00:00Z", "end": "2026-11-10T00:00:00Z"})
    assert resp.status_code == 200 and resp.json()["recordings"][0]["triggers"] == ["person", "motion"]
    (_url, kwargs), = _sent(upstream, "GET", url)
    assert kwargs["params"] == {"start": "2026-11-09T00:00:00Z", "end": "2026-11-10T00:00:00Z"}
    upstream.clear()
    mock_upstream(upstream, "GET", url, status=400, payload={"message": "The range is longer than 7 days"})
    resp = client.get("/api/cameras/camera.backyard_fluent/recordings", params={"start": "2026-10-01T00:00:00Z"})
    assert resp.status_code == 400 and "7 days" in resp.json()["detail"]


def test_a_camera_the_caller_may_not_see_does_not_exist(client, upstream, perms, member):
    for path in ("/api/cameras/camera.backyard_fluent/recordings",
                 "/api/media/cameras/camera.backyard_fluent/snapshot",
                 "/api/media/cameras/camera.backyard_fluent/stream",
                 "/api/media/cameras/camera.backyard_fluent/recordings/play"):
        assert client.get(path, params={**_media_query(), "file": "a.mp4"}).status_code == 404, path
    assert not _sent(upstream, "GET", TEST_HA_URL)  # the hub was never asked


def test_an_entity_id_that_is_not_a_camera_is_refused_before_the_hub(client, upstream, perms):
    for bad in ("light.kitchen", "camera.x%2F..%2Fapi%2Fstates", "camera.UPPER"):
        assert client.get(f"/api/media/cameras/{bad}/snapshot", params=_media_query()).status_code == 404
    assert not _sent(upstream, "GET", TEST_HA_URL)


def test_snapshot_takes_the_media_token_not_an_api_key_in_the_url(upstream, perms, fake_identity):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/camera_proxy/camera.garage_door_camera", body=JPEG,
                  content_type="image/jpeg")
    browser = TestClient(gateway_main.app)  # an <img>: no Authorization header
    resp = browser.get("/api/media/cameras/camera.garage_door_camera/snapshot", params=_media_query())
    assert resp.status_code == 200 and resp.content == JPEG
    assert resp.headers["content-type"] == "image/jpeg" and "no-store" in resp.headers["cache-control"]
    raw_key = browser.get("/api/media/cameras/camera.garage_door_camera/snapshot", params={"token": "test-token"})
    assert raw_key.status_code == 401
    forged = browser.get("/api/media/cameras/camera.garage_door_camera/snapshot",
                         params={"user": TEST_USER, "mt": "1.deadbeef"})
    assert forged.status_code == 403


def test_snapshot_of_a_sleeping_camera_is_503(client, upstream, perms):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/camera_proxy/camera.garage_door_camera", status=500,
                  payload={"message": "Error getting image"})
    assert client.get("/api/media/cameras/camera.garage_door_camera/snapshot").status_code == 503


def test_live_stream_is_passed_through_unbuffered(client, upstream, perms):
    frames = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n"
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/camera_proxy_stream/camera.garage_door_camera",
                  body=frames * 3, content_type="multipart/x-mixed-replace; boundary=frame")
    resp = client.get("/api/media/cameras/camera.garage_door_camera/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert resp.headers["x-accel-buffering"] == "no"
    assert resp.content == frames * 3


def test_live_stream_of_an_offline_camera_is_503(client, upstream, perms):
    mock_upstream(upstream, "GET", f"{TEST_HA_URL}/api/camera_proxy_stream/camera.garage_door_camera",
                  status=503, payload={"message": "Camera is not available: asleep"})
    resp = client.get("/api/media/cameras/camera.garage_door_camera/stream")
    assert resp.status_code == 503 and "asleep" in resp.json()["detail"]


def test_recording_play_forwards_range_and_only_player_headers(client, upstream, perms):
    url = f"{TEST_HA_URL}/api/homehub/cameras/camera.backyard_fluent/recordings/play"
    mock_upstream(upstream, "GET", url, status=206, body=b"0123456789",
                  headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-9/2048", "Accept-Ranges": "bytes",
                           "Set-Cookie": "hub=1", "Server": "homehub"})
    resp = client.get("/api/media/cameras/camera.backyard_fluent/recordings/play",
                      params={"file": RECORDINGS["recordings"][0]["file"]}, headers={"Range": "bytes=0-9"})
    assert resp.status_code == 206 and resp.content == b"0123456789"
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.headers["content-range"] == "bytes 0-9/2048" and resp.headers["accept-ranges"] == "bytes"
    assert "set-cookie" not in resp.headers
    (_url, kwargs), = _sent(upstream, "GET", url)
    assert kwargs["headers"]["Range"] == "bytes=0-9"
    assert kwargs["headers"]["Authorization"] == f"Bearer {TEST_HA_TOKEN}"
    assert kwargs["params"] == {"file": RECORDINGS["recordings"][0]["file"]}


def test_recording_play_of_an_unknown_file_is_404(client, upstream, perms):
    url = f"{TEST_HA_URL}/api/homehub/cameras/camera.backyard_fluent/recordings/play"
    mock_upstream(upstream, "GET", url, status=404, payload={"message": "no recording"})
    resp = client.get("/api/media/cameras/camera.backyard_fluent/recordings/play", params={"file": "x.mp4"})
    assert resp.status_code == 404
    assert client.get("/api/media/cameras/camera.backyard_fluent/recordings/play").status_code == 400
